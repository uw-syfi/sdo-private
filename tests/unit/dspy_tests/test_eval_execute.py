"""Tests for EvalExecuteOptimizer."""

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from app_operator.dspy_integration.config import DSPyConfig, DSPyOptimizationConfig
from app_operator.dspy_integration.eval_execute import EvalExecuteOptimizer, _TrajectoryEvidence
from app_operator.run_classifier import RunClassification


def _make_optimizer(
    tmp_path,
    *,
    n_candidates=2,
    selection_mode="hybrid",
    selection_top_k=3,
    phase_signal_weight=0.35,
):
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)
    config = DSPyConfig(
        optimization=DSPyOptimizationConfig(
            optimizer="BootstrapFewShot",
            teacher_model="gemini-2.5-pro",
            n_candidates=n_candidates,
            selection_mode=selection_mode,
            selection_top_k=selection_top_k,
            phase_signal_weight=phase_signal_weight,
        )
    )
    return EvalExecuteOptimizer(
        config=config,
        prompts_dir=prompts_dir,
        project_root=tmp_path,
        n_candidates=n_candidates,
    )


def test_build_recent_trajectory_context_uses_subagent_and_error_signals(tmp_path):
    """Context builder should include subagent analyses and concrete failure lines."""
    optimizer = _make_optimizer(tmp_path)
    work_dir = tmp_path / "workdir"
    traj_dir = work_dir / "iter1_c1_demoapp" / ".sds" / "trajectories"
    traj_dir.mkdir(parents=True, exist_ok=True)

    trajectory = {
        "metadata": {"status": "failed"},
        "deployment": [
            {
                "messages": [
                    {
                        "role": "assistant",
                        "content": (
                            "[Subagent trajectory analyst]\n"
                            "Repeated retries are changing deploy.sh without validating paths."
                        ),
                    },
                    {
                        "role": "assistant",
                        "content": ("[Subagent error_log analyst]\nPrimary failure is docker-compose.yml not found."),
                    },
                    {
                        "role": "assistant",
                        "content": ("[Subagent script analyst]\ndeploy.sh uses a stale relative path to compose file."),
                    },
                    {
                        "role": "assistant",
                        "content": (
                            "[Subagent repo analyst]\nRepository compose file lives under compose/docker-compose.yml."
                        ),
                    },
                    {
                        "role": "assistant",
                        "content": (
                            "[RLM Auto-Validation]\n[AUTO-VALIDATION WARNING] deploy.sh references missing paths."
                        ),
                    },
                    {
                        "role": "tool_call",
                        "stderr": "ERROR: No such file or directory: docker-compose.yml",
                    },
                ]
            }
        ],
    }
    (traj_dir / "trajectory_20260304-000000.json").write_text(json.dumps(trajectory))

    prompt_names = [
        "subagent_trajectory_analyst",
        "subagent_error_log_analyst",
        "subagent_script_analyst",
        "subagent_repo_analyst",
        "subagent_root_synthesis",
        "deployer_fix_error",
    ]
    ctx = optimizer._build_recent_trajectory_context(
        work_dir=work_dir,
        iteration=2,
        prompt_names=prompt_names,
    )

    assert "Repeated retries are changing deploy.sh" in ctx["subagent_trajectory_analyst"]
    assert "docker-compose.yml not found" in ctx["subagent_error_log_analyst"]
    assert "AUTO-VALIDATION WARNING" in ctx["subagent_script_analyst"]
    assert "compose/docker-compose.yml" in ctx["subagent_repo_analyst"]
    assert "status=failed" in ctx["deployer_fix_error"]


def test_generate_instruction_variants_includes_trajectory_context(monkeypatch, tmp_path):
    """Variant generator should inject trajectory evidence into the teacher prompt."""
    optimizer = _make_optimizer(tmp_path)
    captured: dict = {}

    def _fake_completion(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='["variant a", "variant b"]'))])

    monkeypatch.setattr(
        "app_operator.dspy_integration.eval_execute.litellm.completion",
        _fake_completion,
    )

    variants = optimizer._generate_instruction_variants(
        prompt_name="subagent_error_log_analyst",
        current_instruction="Current instruction text",
        n=2,
        trajectory_context="Recent failures show docker-compose path mismatch.",
    )

    assert variants == ["variant a", "variant b"]
    user_msg = captured["messages"][1]["content"]
    assert "Evidence from prior SDS runs (compressed):" in user_msg
    assert "docker-compose path mismatch" in user_msg


def test_write_sds_toml_inserts_model_override_when_model_key_missing(tmp_path):
    """model_override should be written even when [agent] lacks a model field."""
    optimizer = _make_optimizer(tmp_path)
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "sds.toml").write_text(
        "\n".join(
            [
                "[agent]",
                'provider = "gemini"',
                "",
                "[runtime]",
                'impl = "cli_agent"',
                "",
            ]
        )
    )

    optimizer._write_sds_toml(
        app_dir=app_dir,
        optimized_version="v1",
        model_override="gemini-2.5-pro",
    )

    content = (app_dir / "sds.toml").read_text()
    assert re.search(r"\[agent\][\s\S]*provider = \"gemini\"[\s\S]*model = \"gemini-2.5-pro\"", content)
    assert content.index('model = "gemini-2.5-pro"') < content.index("[runtime]")
    assert "[dspy]" in content
    assert 'optimized_version = "v1"' in content


def test_build_recent_trajectory_context_prioritizes_failed_and_recent(monkeypatch, tmp_path):
    """Weighted ranking should prioritize failed/recent evidence."""
    optimizer = _make_optimizer(tmp_path)

    class _Ev:
        def __init__(
            self,
            *,
            run_name,
            app_name,
            run_type="training",
            status,
            attempts,
            weight,
            trajectory,
        ):
            self.run_name = run_name
            self.app_name = app_name
            self.run_type = run_type
            self.status = status
            self.attempts = attempts
            self.weight = weight
            self.trajectory_insights = trajectory
            self.error_insights = []
            self.script_insights = []
            self.repo_insights = []
            self.error_signals = []
            self.classification_label = ""
            self.classification_reasons = []

    # Intentionally pass lower-priority evidence first; weighted ranking should reorder it.
    monkeypatch.setattr(
        optimizer,
        "_collect_recent_trajectory_evidence",
        lambda work_dir, iteration: [
            _Ev(
                run_name="iter1_c1_old_ok",
                app_name="old_ok",
                status="completed",
                attempts=1,
                weight=0.60,
                trajectory=["older successful run insight"],
            ),
            _Ev(
                run_name="iter2_c2_recent_fail",
                app_name="recent_fail",
                status="failed",
                attempts=2,
                weight=1.30,
                trajectory=["recent failure pattern should be first"],
            ),
        ],
    )

    ctx = optimizer._build_recent_trajectory_context(
        work_dir=tmp_path / "workdir",
        iteration=3,
        prompt_names=["subagent_trajectory_analyst"],
    )["subagent_trajectory_analyst"]

    assert ctx.index("recent_fail via iter2_c2_recent_fail") < ctx.index("old_ok via iter1_c1_old_ok")
    assert ctx.index("recent failure pattern should be first") < ctx.index("older successful run insight")


def test_build_recent_trajectory_context_includes_validation_failure_signal(tmp_path):
    """Validation failures should be fed back as labeled generalization evidence."""
    optimizer = _make_optimizer(tmp_path)
    work_dir = tmp_path / "workdir"

    train_traj_dir = work_dir / "iter1_c1_trainapp" / ".sds" / "trajectories"
    train_traj_dir.mkdir(parents=True, exist_ok=True)
    train_trajectory = {
        "metadata": {"status": "failed"},
        "deployment": [
            {
                "messages": [
                    {
                        "role": "assistant",
                        "content": "[Subagent trajectory analyst]\nTRAIN_ONLY_MARKER",
                    }
                ]
            }
        ],
    }
    (train_traj_dir / "trajectory_20260304-000001.json").write_text(json.dumps(train_trajectory))

    val_traj_dir = work_dir / "fleetcast_iter1_val" / ".sds" / "trajectories"
    val_traj_dir.mkdir(parents=True, exist_ok=True)
    val_trajectory = {
        "metadata": {"status": "failed"},
        "deployment": [
            {
                "messages": [
                    {
                        "role": "assistant",
                        "content": "[Subagent trajectory analyst]\nVAL_ONLY_MARKER",
                    },
                    {
                        "role": "tool_call",
                        "stderr": "ERROR: VAL_ERROR_MARKER from validation app",
                    },
                ]
            }
        ],
    }
    (val_traj_dir / "trajectory_20260304-000002.json").write_text(json.dumps(val_trajectory))

    ctx = optimizer._build_recent_trajectory_context(
        work_dir=work_dir,
        iteration=2,
        prompt_names=["subagent_trajectory_analyst"],
    )["subagent_trajectory_analyst"]

    assert "TRAIN_ONLY_MARKER" in ctx
    assert "VAL_ONLY_MARKER" in ctx
    assert "VAL_ERROR_MARKER" in ctx
    assert "Validation failure (generalization signal):" in ctx


def test_summarize_context_sections_uses_teacher_model_for_long_context(monkeypatch, tmp_path):
    """Long contexts should go through structured teacher-model summarization."""
    optimizer = _make_optimizer(tmp_path)
    captured: dict[str, str] = {}

    def _fake_completion(**kwargs):
        captured["system"] = kwargs["messages"][0]["content"]
        captured["user"] = kwargs["messages"][1]["content"]
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=(
                            "Recent run outcomes:\n"
                            "- summarized training signal\n"
                            "Validation failure (generalization signal):\n"
                            "- summarized validation signal"
                        )
                    )
                )
            ]
        )

    monkeypatch.setattr(
        "app_operator.dspy_integration.eval_execute.litellm.completion",
        _fake_completion,
    )

    long_items = [f"very long failure detail {idx} " + ("x" * 260) for idx in range(10)]
    context = optimizer._summarize_context_sections(
        "deployer_fix_error",
        [
            ("Recent run outcomes", long_items),
            ("Validation failure (generalization signal)", long_items),
        ],
    )

    assert "summarized training signal" in context
    assert "summarized validation signal" in context
    assert "Summarize the evidence below" in captured["user"]


def test_maybe_inject_recombined_candidate_replaces_last_candidate(monkeypatch, tmp_path):
    """Recombination should replace one candidate using top historical parents."""
    optimizer = _make_optimizer(tmp_path, n_candidates=3)
    candidates = [
        {"deployer_fix_error": "candidate-1"},
        {"deployer_fix_error": "candidate-2"},
        {"deployer_fix_error": "candidate-3"},
    ]
    historical_population = [
        {
            "candidate_version": "eval_1_c1",
            "score": 1.0,
            "instructions": {"deployer_fix_error": "parent-a"},
        },
        {
            "candidate_version": "eval_1_c2",
            "score": 0.8,
            "instructions": {"deployer_fix_error": "parent-b"},
        },
    ]

    monkeypatch.setattr(
        optimizer,
        "_compose_recombined_candidate",
        lambda **kwargs: {"deployer_fix_error": "recombined-candidate"},
    )

    updated, info = optimizer._maybe_inject_recombined_candidate(
        candidates=candidates,
        prompt_names=["deployer_fix_error"],
        trajectory_context={"deployer_fix_error": "evidence"},
        historical_population=historical_population,
        iteration=2,
    )

    assert updated[-1]["deployer_fix_error"] == "recombined-candidate"
    assert info["used"] is True
    assert set(info["parent_candidates"]) == {"eval_1_c1", "eval_1_c2"}


def test_judge_receives_full_instructions_for_all_prompts(monkeypatch, tmp_path):
    """LLM judge should receive complete instruction text, not truncated previews."""
    optimizer = _make_optimizer(tmp_path, n_candidates=2, selection_mode="hybrid")
    captured: dict = {}

    long_instruction = "Detailed deployment instruction. " * 20  # ~640 chars

    candidates = [
        {"deployer_fix_error": long_instruction, "deployer_system": "system prompt A"},
        {"deployer_fix_error": "short", "deployer_system": "system prompt B"},
    ]
    candidate_summaries = [
        {
            "candidate_index": 1,
            "score": 0.8,
            "success_rate": 0.8,
            "successful_runs": 4,
            "total_runs": 5,
            "avg_rlm_score": None,
            "run_outcomes": [],
        },
        {
            "candidate_index": 2,
            "score": 0.6,
            "success_rate": 0.6,
            "successful_runs": 3,
            "total_runs": 5,
            "avg_rlm_score": None,
            "run_outcomes": [],
        },
    ]

    def _fake_completion(**kwargs):
        captured["system"] = kwargs["messages"][0]["content"]
        captured["user"] = kwargs["messages"][1]["content"]
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content='{"chosen_candidate": 1, "reason": "better", "confidence": 0.9}')
                )
            ]
        )

    monkeypatch.setattr(
        "app_operator.dspy_integration.eval_execute.litellm.completion",
        _fake_completion,
    )

    choice, info = optimizer._judge_candidates_with_llm(
        candidate_pool=[0, 1],
        prompt_names=["deployer_fix_error", "deployer_system"],
        candidates=candidates,
        candidate_summaries=candidate_summaries,
    )

    assert choice == 0
    user_msg = captured["user"]
    # Full instruction must appear untruncated
    assert long_instruction in user_msg
    # Both prompts should have their own labeled sections
    assert "instruction (deployer_fix_error)" in user_msg
    assert "instruction (deployer_system)" in user_msg
    assert "system prompt A" in user_msg
    assert "system prompt B" in user_msg
    assert "Decision rubric (apply in order)" in user_msg
    assert "classifier_evidence" in user_msg
    assert "Reliability gate" in captured["system"]


def test_judge_prompt_includes_classifier_evidence_details(monkeypatch, tmp_path):
    """Judge prompt should include per-app classifier labels and reasons."""
    optimizer = _make_optimizer(tmp_path, n_candidates=2, selection_mode="hybrid")
    captured: dict = {}

    candidates = [
        {"deployer_fix_error": "candidate one"},
        {"deployer_fix_error": "candidate two"},
    ]
    candidate_summaries = [
        {
            "candidate_index": 1,
            "score": 0.9,
            "success_rate": 1.0,
            "successful_runs": 1,
            "total_runs": 1,
            "avg_rlm_score": None,
            "classification_score": 0.0,
            "classification_counts": {"false_positive": 1},
            "run_outcomes": [
                {
                    "app_name": "hotelreservation",
                    "success": True,
                    "error": "",
                    "classification_label": "false_positive",
                    "classification_reasons": ["monitor summary: 'unhealthy'"],
                }
            ],
        },
        {
            "candidate_index": 2,
            "score": 0.8,
            "success_rate": 1.0,
            "successful_runs": 1,
            "total_runs": 1,
            "avg_rlm_score": None,
            "classification_score": 1.0,
            "classification_counts": {"true_success": 1},
            "run_outcomes": [
                {
                    "app_name": "hotelreservation",
                    "success": True,
                    "error": "",
                    "classification_label": "true_success",
                    "classification_reasons": ["Health check passed cleanly on first attempt"],
                }
            ],
        },
    ]

    def _fake_completion(**kwargs):
        captured["user"] = kwargs["messages"][1]["content"]
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content='{"chosen_candidate": 2, "reason": "safer", "confidence": 0.85}')
                )
            ]
        )

    monkeypatch.setattr(
        "app_operator.dspy_integration.eval_execute.litellm.completion",
        _fake_completion,
    )

    choice, _info = optimizer._judge_candidates_with_llm(
        candidate_pool=[0, 1],
        prompt_names=["deployer_fix_error"],
        candidates=candidates,
        candidate_summaries=candidate_summaries,
    )

    assert choice == 1
    user_msg = captured["user"]
    assert "hotelreservation: false_positive (monitor summary: 'unhealthy')" in user_msg
    assert "hotelreservation: true_success" in user_msg
    assert "run_labels: false_positive=1" in user_msg
    assert "run_labels: true_success=1" in user_msg


def test_cleanup_experiment_containers_handles_hyphenized_compose_labels(monkeypatch, tmp_path):
    """Cleanup should remove containers even when compose label uses hyphenized project names."""
    optimizer = _make_optimizer(tmp_path)
    calls: list[list[str]] = []

    def _fake_run(cmd, capture_output=True, text=True):
        calls.append(cmd)
        if cmd[:4] == ["docker", "ps", "-a", "-q"]:
            label = cmd[-1]
            if label == "label=com.docker.compose.project=iter1-c1-socialnetwork":
                return SimpleNamespace(stdout="abc123\n", returncode=0, stderr="")
            return SimpleNamespace(stdout="", returncode=0, stderr="")
        return SimpleNamespace(stdout="", returncode=0, stderr="")

    monkeypatch.setattr(
        "app_operator.dspy_integration.eval_execute.subprocess.run",
        _fake_run,
    )

    optimizer._cleanup_experiment_containers(Path("iter1_c1_socialNetwork"))

    ps_filters = [cmd[-1] for cmd in calls if cmd[:4] == ["docker", "ps", "-a", "-q"]]
    assert "label=com.docker.compose.project=iter1_c1_socialnetwork" in ps_filters
    assert "label=com.docker.compose.project=iter1-c1-socialnetwork" in ps_filters
    assert any(cmd[:3] == ["docker", "rm", "-f"] and "abc123" in cmd for cmd in calls)


def test_cleanup_experiment_containers_runs_compose_down_when_compose_exists(monkeypatch, tmp_path):
    """Cleanup should run docker compose down to free ports and remove orphans."""
    optimizer = _make_optimizer(tmp_path)
    calls: list[list[str]] = []

    exp_dir = tmp_path / "iter1_c1_demoapp"
    exp_dir.mkdir()
    compose_file = exp_dir / "docker-compose.yml"
    compose_file.write_text("services: {}\n")

    def _fake_run(cmd, capture_output=True, text=True):
        calls.append(cmd)
        return SimpleNamespace(stdout="", returncode=0, stderr="")

    monkeypatch.setattr(
        "app_operator.dspy_integration.eval_execute.subprocess.run",
        _fake_run,
    )

    optimizer._cleanup_experiment_containers(exp_dir)

    down_calls = [
        cmd
        for cmd in calls
        if len(cmd) >= 9 and cmd[:2] == ["docker", "compose"] and "down" in cmd and "--remove-orphans" in cmd
    ]
    assert down_calls, "Expected docker compose down --remove-orphans call"
    assert any(str(compose_file) in cmd for cmd in down_calls)


def test_run_candidate_on_app_archives_stale_dir_when_retry_cleanup_is_blocked(monkeypatch, tmp_path):
    """Rate-limit retries should archive undeletable run dirs instead of aborting the loop."""
    optimizer = _make_optimizer(tmp_path)

    app_path = tmp_path / "hotelReservation"
    app_path.mkdir()
    (app_path / "README.md").write_text("demo")
    work_dir = tmp_path / "workdir"
    work_dir.mkdir()

    subprocess_calls: list[str] = []
    cleanup_calls: list[str] = []
    sleep_delays: list[int] = []
    real_rmtree = __import__("shutil").rmtree
    rmtree_calls = {"count": 0}

    monkeypatch.setattr(optimizer, "_cleanup_experiment_containers", lambda exp_dir: cleanup_calls.append(exp_dir.name))
    monkeypatch.setattr(optimizer, "_clean_exp_dir", lambda exp_dir: None)
    monkeypatch.setattr(optimizer, "_write_sds_toml", lambda *args, **kwargs: None)
    monkeypatch.setattr(optimizer, "_score_rlm_trajectory", lambda exp_dir: None)
    monkeypatch.setattr(optimizer, "_collect_phase_metrics", lambda exp_dir: {})
    monkeypatch.setattr(optimizer, "_classify_run_signal", lambda exp_dir: (None, None, []))
    monkeypatch.setattr("app_operator.dspy_integration.eval_execute.exponential_backoff", lambda attempt: 5)
    monkeypatch.setattr(
        "app_operator.dspy_integration.eval_execute.time.sleep", lambda delay: sleep_delays.append(delay)
    )

    def _fake_rmtree(path):
        if Path(path).name == "iter1_c1_hotelreservation":
            rmtree_calls["count"] += 1
            if rmtree_calls["count"] == 1:
                raise PermissionError("Permission denied: node-id")
        return real_rmtree(path)

    monkeypatch.setattr("app_operator.dspy_integration.eval_execute.shutil.rmtree", _fake_rmtree)

    def _fake_run(cmd):
        subprocess_calls.append(Path(cmd[-1]).name)
        if len(subprocess_calls) == 1:
            return SimpleNamespace(returncode=1, stderr="429 RESOURCE_EXHAUSTED", stdout="")
        return SimpleNamespace(returncode=0, stderr="", stdout="")

    monkeypatch.setattr(optimizer, "_run_candidate_subprocess", _fake_run)

    result = optimizer._run_candidate_on_app(
        app_path=app_path,
        work_dir=work_dir,
        iteration=1,
        c_idx=0,
        candidate_version="candidate-v1",
        provider="gemini",
        max_retries=1,
        rate_limit_backoff=1,
        provider_override=None,
        model_override=None,
        inter_run_delay=0,
    )

    exp_dir = work_dir / "iter1_c1_hotelreservation"
    archived_dir = work_dir / "iter1_c1_hotelreservation.stale_attempt_1"

    assert result["success"] is True
    assert subprocess_calls == ["iter1_c1_hotelreservation", "iter1_c1_hotelreservation"]
    assert cleanup_calls == [
        "iter1_c1_hotelreservation",
        "iter1_c1_hotelreservation",
        "iter1_c1_hotelreservation",
    ]
    assert sleep_delays == [6]
    assert archived_dir.exists()
    assert (archived_dir / "README.md").read_text() == "demo"
    assert (exp_dir / "README.md").read_text() == "demo"


def test_optimize_normalizes_train_app_name_in_experiment_dir(monkeypatch, tmp_path):
    """Candidate run directories should use compose-safe app name tokens."""
    optimizer = _make_optimizer(tmp_path)
    optimizer.n_candidates = 1

    app = tmp_path / "HotelReservation"
    app.mkdir()
    (app / "README.md").write_text("demo")

    work_dir = tmp_path / "workdir"
    work_dir.mkdir()
    output_dir = tmp_path / "prompts" / "optimized" / "v1"

    seen_exp_dir_names: list[str] = []

    monkeypatch.setattr(
        optimizer,
        "_generate_candidates",
        lambda prompt_names, current_instructions, trajectory_context: [{prompt_names[0]: "improved instruction"}],
    )
    monkeypatch.setattr(
        optimizer,
        "_cleanup_experiment_containers",
        lambda exp_dir: seen_exp_dir_names.append(exp_dir.name),
    )
    monkeypatch.setattr(optimizer, "_clean_exp_dir", lambda exp_dir: None)
    monkeypatch.setattr(optimizer, "_write_sds_toml", lambda *args, **kwargs: None)
    monkeypatch.setattr(optimizer, "_score_rlm_trajectory", lambda exp_dir: None)
    monkeypatch.setattr(
        optimizer,
        "_run_candidate_subprocess",
        lambda cmd: SimpleNamespace(returncode=0, stderr="", stdout=""),
    )

    result = optimizer.optimize(
        prompt_names=["deployer_fix_error"],
        train_apps=[app],
        work_dir=work_dir,
        output_dir=output_dir,
        iteration=1,
        current_version=None,
        provider="gemini",
        max_retries=1,
        rate_limit_backoff=1,
        inter_run_delay=0,
    )

    assert result["success"] is True
    assert "iter1_c1_hotelreservation" in seen_exp_dir_names


def test_optimize_uses_seed_only_bootstrap_on_cold_start(monkeypatch, tmp_path):
    """Cold start should run one seed candidate before generating variants."""
    optimizer = _make_optimizer(tmp_path)
    optimizer.n_candidates = 4

    app = tmp_path / "demoapp"
    app.mkdir()
    (app / "README.md").write_text("demo")

    work_dir = tmp_path / "workdir"
    work_dir.mkdir()
    output_dir = tmp_path / "prompts" / "optimized" / "v1"

    monkeypatch.setattr(
        optimizer,
        "_build_recent_trajectory_context",
        lambda work_dir, iteration, prompt_names: dict.fromkeys(prompt_names, ""),
    )

    def _should_not_generate(*args, **kwargs):
        raise AssertionError("Cold-start bootstrap should skip candidate generation")

    monkeypatch.setattr(optimizer, "_generate_candidates", _should_not_generate)
    monkeypatch.setattr(optimizer, "_cleanup_experiment_containers", lambda exp_dir: None)
    monkeypatch.setattr(optimizer, "_clean_exp_dir", lambda exp_dir: None)
    monkeypatch.setattr(optimizer, "_write_sds_toml", lambda *args, **kwargs: None)
    monkeypatch.setattr(optimizer, "_score_rlm_trajectory", lambda exp_dir: None)
    monkeypatch.setattr(
        optimizer,
        "_run_candidate_subprocess",
        lambda cmd: SimpleNamespace(returncode=0, stderr="", stdout=""),
    )

    result = optimizer.optimize(
        prompt_names=["deployer_fix_error"],
        train_apps=[app],
        work_dir=work_dir,
        output_dir=output_dir,
        iteration=1,
        current_version=None,
        provider="gemini",
        max_retries=1,
        rate_limit_backoff=1,
        inter_run_delay=0,
    )

    assert result["success"] is True
    assert result["best_candidate"] == 1
    assert len(result["all_scores"]) == 1

    state = json.loads((output_dir / "deployer_fix_error.dspy.json").read_text())
    assert (
        state["optimized_instruction"] == "A deployment has failed. Analyze the error and fix the deployment scripts."
    )

    metadata = json.loads((output_dir / "metadata.json").read_text())
    assert metadata["n_candidates"] == 1


def test_optimize_hybrid_selection_uses_llm_on_top_k(monkeypatch, tmp_path):
    """Hybrid mode should allow judge to choose among top-k score candidates."""
    optimizer = _make_optimizer(
        tmp_path,
        n_candidates=3,
        selection_mode="hybrid",
        selection_top_k=2,
    )

    app_a = tmp_path / "appa"
    app_b = tmp_path / "appb"
    app_a.mkdir()
    app_b.mkdir()
    (app_a / "README.md").write_text("a")
    (app_b / "README.md").write_text("b")

    work_dir = tmp_path / "workdir"
    work_dir.mkdir()
    output_dir = tmp_path / "prompts" / "optimized" / "v1"

    monkeypatch.setattr(
        optimizer,
        "_generate_candidates",
        lambda prompt_names, current_instructions, trajectory_context: [
            {prompt_names[0]: "candidate one"},
            {prompt_names[0]: "candidate two"},
            {prompt_names[0]: "candidate three"},
        ],
    )
    monkeypatch.setattr(optimizer, "_cleanup_experiment_containers", lambda exp_dir: None)
    monkeypatch.setattr(optimizer, "_clean_exp_dir", lambda exp_dir: None)
    monkeypatch.setattr(optimizer, "_write_sds_toml", lambda *args, **kwargs: None)
    monkeypatch.setattr(optimizer, "_score_rlm_trajectory", lambda exp_dir: None)

    outcomes = {
        "candidate_1_appa": True,
        "candidate_1_appb": True,
        "candidate_2_appa": True,
        "candidate_2_appb": False,
        "candidate_3_appa": True,
        "candidate_3_appb": True,
        "candidate_4_appa": False,
        "candidate_4_appb": False,
    }

    def _fake_run(cmd):
        op = re.sub(r"^iter\d+_c", "candidate_", Path(cmd[-1]).name)
        success = outcomes[op]
        return SimpleNamespace(
            returncode=0 if success else 1,
            stderr="" if success else "simulated failure",
            stdout="",
        )

    monkeypatch.setattr(optimizer, "_run_candidate_subprocess", _fake_run)

    def _fake_completion(**kwargs):
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content='{"chosen_candidate": 3, "reason": "better failure profile", "confidence": 0.77}'
                    )
                )
            ]
        )

    monkeypatch.setattr(
        "app_operator.dspy_integration.eval_execute.litellm.completion",
        _fake_completion,
    )

    result = optimizer.optimize(
        prompt_names=["deployer_fix_error"],
        train_apps=[app_a, app_b],
        work_dir=work_dir,
        output_dir=output_dir,
        iteration=1,
        current_version="v0",
        provider="gemini",
        max_retries=1,
        rate_limit_backoff=1,
        inter_run_delay=0,
    )

    assert result["success"] is True
    assert result["best_candidate"] == 3
    assert result["selection"]["selection_mode"] == "hybrid"
    assert result["selection"]["llm_used"] is True
    assert result["selection"]["fallback_to_score"] is False
    assert result["selection"]["llm_choice"] == 3

    state = json.loads((output_dir / "deployer_fix_error.dspy.json").read_text())
    assert state["optimized_instruction"] == "candidate two"


def test_optimize_hybrid_falls_back_to_score_when_judge_invalid(monkeypatch, tmp_path):
    """Hybrid mode should fall back to score winner when judge output is invalid."""
    optimizer = _make_optimizer(
        tmp_path,
        n_candidates=2,
        selection_mode="hybrid",
        selection_top_k=2,
    )

    app_a = tmp_path / "appa"
    app_b = tmp_path / "appb"
    app_a.mkdir()
    app_b.mkdir()
    (app_a / "README.md").write_text("a")
    (app_b / "README.md").write_text("b")

    work_dir = tmp_path / "workdir"
    work_dir.mkdir()
    output_dir = tmp_path / "prompts" / "optimized" / "v1"

    monkeypatch.setattr(
        optimizer,
        "_generate_candidates",
        lambda prompt_names, current_instructions, trajectory_context: [
            {prompt_names[0]: "candidate one"},
            {prompt_names[0]: "candidate two"},
        ],
    )
    monkeypatch.setattr(optimizer, "_cleanup_experiment_containers", lambda exp_dir: None)
    monkeypatch.setattr(optimizer, "_clean_exp_dir", lambda exp_dir: None)
    monkeypatch.setattr(optimizer, "_write_sds_toml", lambda *args, **kwargs: None)
    monkeypatch.setattr(optimizer, "_score_rlm_trajectory", lambda exp_dir: None)

    outcomes = {
        "candidate_1_appa": True,
        "candidate_1_appb": True,
        "candidate_2_appa": False,
        "candidate_2_appb": False,
        "candidate_3_appa": False,
        "candidate_3_appb": False,
    }

    def _fake_run(cmd):
        op = re.sub(r"^iter\d+_c", "candidate_", Path(cmd[-1]).name)
        success = outcomes[op]
        return SimpleNamespace(
            returncode=0 if success else 1,
            stderr="" if success else "simulated failure",
            stdout="",
        )

    monkeypatch.setattr(optimizer, "_run_candidate_subprocess", _fake_run)
    monkeypatch.setattr(
        "app_operator.dspy_integration.eval_execute.litellm.completion",
        lambda **kwargs: SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="not json"))]),
    )

    result = optimizer.optimize(
        prompt_names=["deployer_fix_error"],
        train_apps=[app_a, app_b],
        work_dir=work_dir,
        output_dir=output_dir,
        iteration=1,
        current_version="v0",
        provider="gemini",
        max_retries=1,
        rate_limit_backoff=1,
        inter_run_delay=0,
    )

    assert result["success"] is True
    assert result["best_candidate"] == 1
    assert result["selection"]["llm_used"] is True
    assert result["selection"]["fallback_to_score"] is True
    assert "JSON" in result["selection"]["llm_reason"]


def test_optimize_score_mode_skips_llm_judge(monkeypatch, tmp_path):
    """Score mode should not invoke judge selection."""
    optimizer = _make_optimizer(
        tmp_path,
        n_candidates=2,
        selection_mode="score",
    )

    app_a = tmp_path / "appa"
    app_b = tmp_path / "appb"
    app_a.mkdir()
    app_b.mkdir()
    (app_a / "README.md").write_text("a")
    (app_b / "README.md").write_text("b")

    work_dir = tmp_path / "workdir"
    work_dir.mkdir()
    output_dir = tmp_path / "prompts" / "optimized" / "v1"

    monkeypatch.setattr(
        optimizer,
        "_generate_candidates",
        lambda prompt_names, current_instructions, trajectory_context: [
            {prompt_names[0]: "candidate one"},
            {prompt_names[0]: "candidate two"},
        ],
    )
    monkeypatch.setattr(optimizer, "_cleanup_experiment_containers", lambda exp_dir: None)
    monkeypatch.setattr(optimizer, "_clean_exp_dir", lambda exp_dir: None)
    monkeypatch.setattr(optimizer, "_write_sds_toml", lambda *args, **kwargs: None)
    monkeypatch.setattr(optimizer, "_score_rlm_trajectory", lambda exp_dir: None)
    monkeypatch.setattr(
        optimizer,
        "_run_candidate_subprocess",
        lambda cmd: SimpleNamespace(returncode=0, stderr="", stdout=""),
    )
    monkeypatch.setattr(
        "app_operator.dspy_integration.eval_execute.litellm.completion",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("Judge should not be called")),
    )

    result = optimizer.optimize(
        prompt_names=["deployer_fix_error"],
        train_apps=[app_a, app_b],
        work_dir=work_dir,
        output_dir=output_dir,
        iteration=1,
        current_version="v0",
        provider="gemini",
        max_retries=1,
        rate_limit_backoff=1,
        inter_run_delay=0,
    )

    assert result["success"] is True
    assert result["best_candidate"] == 1
    assert result["selection"]["selection_mode"] == "score"
    assert result["selection"]["llm_used"] is False


def test_optimize_uses_prompt_aligned_phase_score_to_break_ties(monkeypatch, tmp_path):
    """Score mode should prefer stronger phase handling when base success scores tie."""
    optimizer = _make_optimizer(
        tmp_path,
        n_candidates=2,
        selection_mode="score",
    )

    app_a = tmp_path / "appa"
    app_a.mkdir()
    (app_a / "README.md").write_text("a")

    work_dir = tmp_path / "workdir"
    work_dir.mkdir()
    output_dir = tmp_path / "prompts" / "optimized" / "v1"

    monkeypatch.setattr(
        optimizer,
        "_generate_candidates",
        lambda prompt_names, current_instructions, trajectory_context: [
            {prompt_names[0]: "candidate one"},
            {prompt_names[0]: "candidate two"},
        ],
    )
    monkeypatch.setattr(optimizer, "_cleanup_experiment_containers", lambda exp_dir: None)
    monkeypatch.setattr(optimizer, "_clean_exp_dir", lambda exp_dir: None)
    monkeypatch.setattr(optimizer, "_write_sds_toml", lambda *args, **kwargs: None)
    monkeypatch.setattr(optimizer, "_score_rlm_trajectory", lambda exp_dir: None)
    monkeypatch.setattr(
        optimizer,
        "_run_candidate_subprocess",
        lambda cmd: SimpleNamespace(returncode=0, stderr="", stdout=""),
    )

    monkeypatch.setattr(
        optimizer,
        "_collect_phase_metrics",
        lambda exp_dir: {"error_recovery_quality": 0.9} if "_c3_" in exp_dir.name else {"error_recovery_quality": 0.2},
    )

    result = optimizer.optimize(
        prompt_names=["deployer_fix_error"],
        train_apps=[app_a],
        work_dir=work_dir,
        output_dir=output_dir,
        iteration=1,
        current_version="v0",
        provider="gemini",
        max_retries=1,
        rate_limit_backoff=1,
        inter_run_delay=0,
    )

    assert result["success"] is True
    # Base score ties at 1.0; phase-aligned score should pick mutation c3 over the elite baseline.
    assert result["best_candidate"] == 3


def test_optimize_penalizes_false_positives_with_run_classifier(monkeypatch, tmp_path):
    """Classifier labels should influence ranking even when subprocess success ties."""
    optimizer = _make_optimizer(
        tmp_path,
        n_candidates=2,
        selection_mode="score",
    )

    app_a = tmp_path / "appa"
    app_a.mkdir()
    (app_a / "README.md").write_text("a")
    (app_a / ".sds").mkdir()

    work_dir = tmp_path / "workdir"
    work_dir.mkdir()
    output_dir = tmp_path / "prompts" / "optimized" / "v1"

    monkeypatch.setattr(
        optimizer,
        "_generate_candidates",
        lambda prompt_names, current_instructions, trajectory_context: [
            {prompt_names[0]: "candidate one"},
            {prompt_names[0]: "candidate two"},
        ],
    )
    monkeypatch.setattr(optimizer, "_cleanup_experiment_containers", lambda exp_dir: None)
    monkeypatch.setattr(optimizer, "_clean_exp_dir", lambda exp_dir: None)
    monkeypatch.setattr(optimizer, "_write_sds_toml", lambda *args, **kwargs: None)
    monkeypatch.setattr(optimizer, "_score_rlm_trajectory", lambda exp_dir: None)
    monkeypatch.setattr(optimizer, "_collect_phase_metrics", lambda exp_dir: {})
    monkeypatch.setattr(
        optimizer,
        "_run_candidate_subprocess",
        lambda cmd: SimpleNamespace(returncode=0, stderr="", stdout=""),
    )

    def _fake_classify_run(run_dir: Path) -> RunClassification:
        if "_c1_" in run_dir.name:
            return RunClassification(
                run_dir=run_dir.name,
                label="false_positive",
                trajectory_status="completed",
                log_deploy_attempts=1,
                reasons=["monitor summary: 'unhealthy'"],
            )
        return RunClassification(
            run_dir=run_dir.name,
            label="true_success",
            trajectory_status="completed",
            log_deploy_attempts=1,
            reasons=["Health check passed cleanly on first attempt"],
        )

    monkeypatch.setattr(
        "app_operator.dspy_integration.eval_execute.classify_run",
        _fake_classify_run,
    )

    result = optimizer.optimize(
        prompt_names=["deployer_fix_error"],
        train_apps=[app_a],
        work_dir=work_dir,
        output_dir=output_dir,
        iteration=1,
        current_version="v0",
        provider="gemini",
        max_retries=1,
        rate_limit_backoff=1,
        inter_run_delay=0,
    )

    assert result["success"] is True
    assert result["best_candidate"] == 2


def test_optimize_phase_signal_weight_controls_blend_strength(monkeypatch, tmp_path):
    """phase_signal_weight should control whether base or phase score dominates."""
    app_a = tmp_path / "appa"
    app_a.mkdir()
    (app_a / "README.md").write_text("a")

    work_dir_0 = tmp_path / "workdir_w0"
    work_dir_0.mkdir()
    work_dir_1 = tmp_path / "workdir_w1"
    work_dir_1.mkdir()
    output_dir_0 = tmp_path / "prompts" / "optimized" / "v1_w0"
    output_dir_1 = tmp_path / "prompts" / "optimized" / "v1_w1"

    def _prepare_common_mocks(optimizer):
        monkeypatch.setattr(
            optimizer,
            "_generate_candidates",
            lambda prompt_names, current_instructions, trajectory_context: [
                {prompt_names[0]: "candidate one"},
                {prompt_names[0]: "candidate two"},
            ],
        )
        monkeypatch.setattr(optimizer, "_cleanup_experiment_containers", lambda exp_dir: None)
        monkeypatch.setattr(optimizer, "_clean_exp_dir", lambda exp_dir: None)
        monkeypatch.setattr(optimizer, "_write_sds_toml", lambda *args, **kwargs: None)
        monkeypatch.setattr(optimizer, "_score_rlm_trajectory", lambda exp_dir: None)
        monkeypatch.setattr(
            optimizer,
            "_collect_phase_metrics",
            lambda exp_dir: {"error_recovery_quality": 1.0}
            if "_c2_" in exp_dir.name
            else {"error_recovery_quality": 0.1},
        )
        # Base score favors elite candidate 1 (success), phase score favors mutation candidate 2.
        monkeypatch.setattr(
            optimizer,
            "_run_candidate_subprocess",
            lambda cmd: SimpleNamespace(returncode=0, stderr="", stdout="")
            if "_c1_" in Path(cmd[-1]).name
            else SimpleNamespace(returncode=1, stderr="simulated failure", stdout=""),
        )

    optimizer_weight_zero = _make_optimizer(tmp_path, n_candidates=2, selection_mode="score", phase_signal_weight=0.0)
    _prepare_common_mocks(optimizer_weight_zero)
    result_weight_zero = optimizer_weight_zero.optimize(
        prompt_names=["deployer_fix_error"],
        train_apps=[app_a],
        work_dir=work_dir_0,
        output_dir=output_dir_0,
        iteration=1,
        current_version="v0",
        provider="gemini",
        max_retries=1,
        rate_limit_backoff=1,
        inter_run_delay=0,
    )

    optimizer_weight_one = _make_optimizer(tmp_path, n_candidates=2, selection_mode="score", phase_signal_weight=1.0)
    _prepare_common_mocks(optimizer_weight_one)
    result_weight_one = optimizer_weight_one.optimize(
        prompt_names=["deployer_fix_error"],
        train_apps=[app_a],
        work_dir=work_dir_1,
        output_dir=output_dir_1,
        iteration=1,
        current_version="v0",
        provider="gemini",
        max_retries=1,
        rate_limit_backoff=1,
        inter_run_delay=0,
    )

    assert result_weight_zero["success"] is True
    assert result_weight_zero["best_candidate"] == 1
    assert result_weight_one["success"] is True
    assert result_weight_one["best_candidate"] == 2


def test_build_recent_trajectory_context_includes_classifier_feedback(monkeypatch, tmp_path):
    """Run-classifier labels/reasons should be included in trajectory context."""
    optimizer = _make_optimizer(tmp_path)
    work_dir = tmp_path / "workdir"
    traj_dir = work_dir / "iter1_c1_demoapp" / ".sds" / "trajectories"
    traj_dir.mkdir(parents=True, exist_ok=True)

    trajectory = {
        "metadata": {"status": "completed"},
        "deployment": [
            {
                "messages": [
                    {
                        "role": "assistant",
                        "content": "[Subagent error_log analyst]\nNo obvious stack trace found.",
                    }
                ]
            }
        ],
    }
    (traj_dir / "trajectory_20260304-000000.json").write_text(json.dumps(trajectory))

    monkeypatch.setattr(
        "app_operator.dspy_integration.eval_execute.classify_run",
        lambda run_dir: RunClassification(
            run_dir=run_dir.name,
            label="false_positive",
            trajectory_status="completed",
            log_deploy_attempts=1,
            reasons=["monitor summary: 'unhealthy'"],
        ),
    )

    ctx = optimizer._build_recent_trajectory_context(
        work_dir=work_dir,
        iteration=2,
        prompt_names=["deployer_fix_error"],
    )["deployer_fix_error"]

    assert "classification=false_positive" in ctx
    assert "monitor summary: 'unhealthy'" in ctx


def test_optimize_stores_per_app_scores_in_metadata(monkeypatch, tmp_path):
    """Metadata should contain per-app scores for each candidate."""
    optimizer = _make_optimizer(tmp_path, n_candidates=1, selection_mode="score")

    app_a = tmp_path / "appa"
    app_b = tmp_path / "appb"
    app_a.mkdir()
    app_b.mkdir()
    (app_a / "README.md").write_text("a")
    (app_b / "README.md").write_text("b")

    work_dir = tmp_path / "workdir"
    work_dir.mkdir()
    output_dir = tmp_path / "prompts" / "optimized" / "v1"

    monkeypatch.setattr(
        optimizer,
        "_generate_candidates",
        lambda prompt_names, current_instructions, trajectory_context: [
            {prompt_names[0]: "candidate one"},
        ],
    )
    monkeypatch.setattr(optimizer, "_cleanup_experiment_containers", lambda exp_dir: None)
    monkeypatch.setattr(optimizer, "_clean_exp_dir", lambda exp_dir: None)
    monkeypatch.setattr(optimizer, "_write_sds_toml", lambda *args, **kwargs: None)
    monkeypatch.setattr(optimizer, "_score_rlm_trajectory", lambda exp_dir: None)

    outcomes = {
        "candidate_1_appa": False,
        "candidate_1_appb": False,
        "candidate_2_appa": True,
        "candidate_2_appb": False,
    }

    def _fake_run(cmd):
        op = re.sub(r"^iter\d+_c", "candidate_", Path(cmd[-1]).name)
        success = outcomes[op]
        return SimpleNamespace(
            returncode=0 if success else 1,
            stderr="" if success else "simulated failure",
            stdout="",
        )

    monkeypatch.setattr(optimizer, "_run_candidate_subprocess", _fake_run)

    result = optimizer.optimize(
        prompt_names=["deployer_fix_error"],
        train_apps=[app_a, app_b],
        work_dir=work_dir,
        output_dir=output_dir,
        iteration=1,
        current_version="v0",
        provider="gemini",
        max_retries=1,
        rate_limit_backoff=1,
        inter_run_delay=0,
    )

    assert result["success"] is True
    metadata = json.loads((output_dir / "metadata.json").read_text())
    assert "candidate_app_scores" in metadata
    assert len(metadata["candidate_app_scores"]) == 2
    app_scores = metadata["candidate_app_scores"][1]
    assert app_scores["appa"] == 1.0
    assert app_scores["appb"] == 0.0


def test_load_historical_population_includes_per_app_scores(tmp_path):
    """Historical population should carry per_app_scores from metadata."""
    optimizer = _make_optimizer(tmp_path)
    scoped_dir = tmp_path / "prompts" / "optimized"

    # Write metadata for iteration 1 with candidate_app_scores
    v1_dir = scoped_dir / "v1"
    v1_dir.mkdir(parents=True)
    metadata = {
        "iteration": 1,
        "all_scores": [0.7, 0.5],
        "candidate_app_scores": [
            {"appa": 1.0, "appb": 0.4},
            {"appa": 0.0, "appb": 1.0},
        ],
    }
    (v1_dir / "metadata.json").write_text(json.dumps(metadata))

    # Write candidate dirs with prompt files
    for idx in [1, 2]:
        cand_dir = scoped_dir / f"eval_1_c{idx}"
        cand_dir.mkdir(parents=True)
        state = {"optimized_instruction": f"instruction {idx}"}
        (cand_dir / "deployer_fix_error.dspy.json").write_text(json.dumps(state))

    population = optimizer._load_historical_candidate_population(
        prompt_names=["deployer_fix_error"],
        before_iteration=2,
        output_prefix=None,
        top_k=10,
    )

    assert len(population) == 2
    # Sorted by score descending — c1 (0.7) first
    assert population[0]["per_app_scores"] == {"appa": 1.0, "appb": 0.4}
    assert population[1]["per_app_scores"] == {"appa": 0.0, "appb": 1.0}


def test_optimize_writes_lineage_ledger_and_reproducibility(monkeypatch, tmp_path):
    """Optimization should emit lineage.jsonl events and reproducibility metadata."""
    optimizer = _make_optimizer(tmp_path, n_candidates=1, selection_mode="score")

    app = tmp_path / "hotelreservation"
    app.mkdir()
    (app / "README.md").write_text("demo")

    work_dir = tmp_path / "workdir"
    work_dir.mkdir()
    output_dir = tmp_path / "prompts" / "optimized" / "simple_LLM" / "v1"

    monkeypatch.setattr(
        optimizer,
        "_build_recent_trajectory_context",
        lambda work_dir, iteration, prompt_names: {prompt_names[0]: "prior failure signature"},
    )
    monkeypatch.setattr(
        optimizer,
        "_generate_candidates",
        lambda prompt_names, current_instructions, trajectory_context: [{prompt_names[0]: "candidate one"}],
    )
    monkeypatch.setattr(optimizer, "_cleanup_experiment_containers", lambda exp_dir: None)
    monkeypatch.setattr(optimizer, "_clean_exp_dir", lambda exp_dir: None)
    monkeypatch.setattr(optimizer, "_write_sds_toml", lambda *args, **kwargs: None)
    monkeypatch.setattr(optimizer, "_score_rlm_trajectory", lambda exp_dir: None)
    monkeypatch.setattr(
        optimizer,
        "_run_candidate_subprocess",
        lambda cmd: SimpleNamespace(returncode=0, stderr="", stdout=""),
    )

    result = optimizer.optimize(
        prompt_names=["deployer_fix_error"],
        train_apps=[app],
        work_dir=work_dir,
        output_dir=output_dir,
        iteration=1,
        current_version=None,
        provider="gemini",
        max_retries=1,
        rate_limit_backoff=1,
        inter_run_delay=0,
        output_prefix="simple_LLM",
    )

    assert result["success"] is True
    metadata = json.loads((output_dir / "metadata.json").read_text())
    assert metadata["n_candidates"] == 2
    assert metadata["n_mutation_candidates_requested"] == 1
    assert metadata["lineage"]["selected_candidate_id"] == "simple_LLM/eval_1_c1"
    assert metadata["lineage"]["candidate_nodes"][0]["candidate_id"] == "simple_LLM/eval_1_c1"
    assert metadata["reproducibility"]["training_trajectories_hash"]
    assert len(metadata["reproducibility"]["training_trajectories_hash"]) == 64
    assert metadata["reproducibility"]["n_candidates_requested"] == 1
    assert metadata["reproducibility"]["n_candidates_evaluated"] == 2

    lineage_file = tmp_path / "prompts" / "optimized" / "simple_LLM" / "lineage.jsonl"
    events = [json.loads(line) for line in lineage_file.read_text().splitlines() if line.strip()]
    event_types = [event["event_type"] for event in events]
    assert "candidate_generated" in event_types
    assert "candidate_evaluated" in event_types
    assert "candidate_selected" in event_types
    assert "promoted_to_version" in event_types


def test_load_historical_population_handles_missing_app_scores(tmp_path):
    """Population should gracefully handle metadata without candidate_app_scores."""
    optimizer = _make_optimizer(tmp_path)
    scoped_dir = tmp_path / "prompts" / "optimized"

    v1_dir = scoped_dir / "v1"
    v1_dir.mkdir(parents=True)
    # Old-format metadata without candidate_app_scores
    metadata = {"iteration": 1, "all_scores": [0.9]}
    (v1_dir / "metadata.json").write_text(json.dumps(metadata))

    cand_dir = scoped_dir / "eval_1_c1"
    cand_dir.mkdir(parents=True)
    (cand_dir / "deployer_fix_error.dspy.json").write_text(json.dumps({"optimized_instruction": "inst"}))

    population = optimizer._load_historical_candidate_population(
        prompt_names=["deployer_fix_error"],
        before_iteration=2,
        output_prefix=None,
        top_k=10,
    )

    assert len(population) == 1
    assert population[0]["per_app_scores"] == {}


def test_select_recombination_parents_is_stochastic(tmp_path):
    """Parent selection should favor higher-scoring candidates but sample stochastically."""
    import random as _random

    optimizer = _make_optimizer(tmp_path)
    population = [
        {"candidate_version": "c1", "score": 1.0, "instructions": {}},
        {"candidate_version": "c2", "score": 0.5, "instructions": {}},
        {"candidate_version": "c3", "score": 0.01, "instructions": {}},
    ]

    _random.seed(42)
    selections = {"c1": 0, "c2": 0, "c3": 0}
    for _ in range(200):
        a, b = optimizer._select_recombination_parents(population)
        selections[a["candidate_version"]] += 1
        assert a is not b

    # c1 (score=1.0) should be selected most often
    assert selections["c1"] > selections["c2"] > selections["c3"]
    # All candidates should have some selections (stochastic)
    assert selections["c3"] > 0


def test_select_recombination_parents_with_two_candidates(tmp_path):
    """With exactly 2 candidates, both should always be selected (one each)."""
    optimizer = _make_optimizer(tmp_path)
    population = [
        {"candidate_version": "c1", "score": 1.0, "instructions": {}},
        {"candidate_version": "c2", "score": 0.5, "instructions": {}},
    ]

    for _ in range(20):
        a, b = optimizer._select_recombination_parents(population)
        assert a is not b
        assert {a["candidate_version"], b["candidate_version"]} == {"c1", "c2"}


# ---------------------------------------------------------------------------
# Tests for newly extracted helper methods (refactored from deep nesting)
# ---------------------------------------------------------------------------


def _make_notes(*texts):
    return list(texts)


def test_context_sections_for_prompt_trajectory_analyst():
    sections = EvalExecuteOptimizer._context_sections_for_prompt(
        "subagent_trajectory_analyst",
        run_summaries=_make_notes("run1"),
        validation_failure_notes=_make_notes("val1"),
        trajectory_notes=_make_notes("traj1"),
        error_notes=_make_notes("err1"),
        script_notes=_make_notes("script1"),
        repo_notes=_make_notes("repo1"),
    )
    titles = [s[0] for s in sections]
    assert "Trajectory analyst findings" in titles
    assert "Error-log analyst findings" not in titles
    assert "Repository analyst findings" not in titles


def test_context_sections_for_prompt_error_log_analyst():
    sections = EvalExecuteOptimizer._context_sections_for_prompt(
        "subagent_error_log_analyst",
        run_summaries=_make_notes("run1"),
        validation_failure_notes=_make_notes("val1"),
        trajectory_notes=_make_notes("traj1"),
        error_notes=_make_notes("err1"),
        script_notes=[],
        repo_notes=[],
    )
    titles = [s[0] for s in sections]
    assert "Error-log analyst findings" in titles
    assert "Trajectory analyst findings" not in titles
    assert len(sections) == 3


def test_context_sections_for_prompt_root_synthesis_includes_all():
    sections = EvalExecuteOptimizer._context_sections_for_prompt(
        "subagent_root_synthesis",
        run_summaries=_make_notes("run1"),
        validation_failure_notes=_make_notes("val1"),
        trajectory_notes=_make_notes("traj1"),
        error_notes=_make_notes("err1"),
        script_notes=_make_notes("script1"),
        repo_notes=_make_notes("repo1"),
    )
    titles = [s[0] for s in sections]
    assert "Trajectory findings" in titles
    assert "Error findings" in titles
    assert "Script findings" in titles
    assert "Repository findings" in titles


def test_context_sections_for_prompt_default_fallback():
    sections = EvalExecuteOptimizer._context_sections_for_prompt(
        "deployer_fix_error",
        run_summaries=_make_notes("run1"),
        validation_failure_notes=_make_notes("val1"),
        trajectory_notes=[],
        error_notes=_make_notes("err1"),
        script_notes=_make_notes("script1"),
        repo_notes=[],
    )
    titles = [s[0] for s in sections]
    assert "Recent run outcomes" in titles
    assert "Recurring error patterns" in titles
    assert "Script findings" in titles
    assert "Trajectory analyst findings" not in titles


def test_classify_assistant_snippet_trajectory():
    evidence = _TrajectoryEvidence(run_name="r", app_name="a", run_type="train", status="ok", attempts=1)
    EvalExecuteOptimizer._classify_assistant_snippet(
        "deploy.sh is re-run repeatedly",
        "[subagent trajectory analyst] deploy.sh is re-run repeatedly",
        evidence,
    )
    assert evidence.trajectory_insights == ["deploy.sh is re-run repeatedly"]
    assert evidence.error_insights == []


def test_classify_assistant_snippet_error_log():
    evidence = _TrajectoryEvidence(run_name="r", app_name="a", run_type="train", status="ok", attempts=1)
    EvalExecuteOptimizer._classify_assistant_snippet(
        "docker-compose.yml not found",
        "[subagent error_log analyst] docker-compose.yml not found",
        evidence,
    )
    assert evidence.error_insights == ["docker-compose.yml not found"]
    assert evidence.trajectory_insights == []


def test_classify_assistant_snippet_script():
    evidence = _TrajectoryEvidence(run_name="r", app_name="a", run_type="train", status="ok", attempts=1)
    EvalExecuteOptimizer._classify_assistant_snippet(
        "stale relative path",
        "[subagent script analyst] stale relative path",
        evidence,
    )
    assert evidence.script_insights == ["stale relative path"]


def test_classify_assistant_snippet_auto_validation_warning():
    evidence = _TrajectoryEvidence(run_name="r", app_name="a", run_type="train", status="ok", attempts=1)
    EvalExecuteOptimizer._classify_assistant_snippet(
        "deploy.sh references missing paths",
        "[rlm auto-validation]\nauto-validation warning detected",
        evidence,
    )
    assert evidence.script_insights == ["deploy.sh references missing paths"]


def test_classify_assistant_snippet_repo():
    evidence = _TrajectoryEvidence(run_name="r", app_name="a", run_type="train", status="ok", attempts=1)
    EvalExecuteOptimizer._classify_assistant_snippet(
        "compose lives under compose/",
        "[subagent repo analyst] compose lives under compose/",
        evidence,
    )
    assert evidence.repo_insights == ["compose lives under compose/"]


def test_classify_assistant_snippet_unrecognized_is_ignored():
    evidence = _TrajectoryEvidence(run_name="r", app_name="a", run_type="train", status="ok", attempts=1)
    EvalExecuteOptimizer._classify_assistant_snippet(
        "some unrelated content",
        "some unrelated content",
        evidence,
    )
    assert not evidence.trajectory_insights
    assert not evidence.error_insights
    assert not evidence.script_insights
    assert not evidence.repo_insights


def test_pad_variants_extends_short_list():
    result = EvalExecuteOptimizer._pad_variants(["a", "b"], n=4, fallback="default")
    assert result == ["a", "b", "default", "default"]


def test_pad_variants_truncates_long_list():
    result = EvalExecuteOptimizer._pad_variants(["a", "b", "c", "d"], n=2, fallback="default")
    assert result == ["a", "b"]


def test_pad_variants_exact_length():
    result = EvalExecuteOptimizer._pad_variants(["x", "y", "z"], n=3, fallback="fb")
    assert result == ["x", "y", "z"]


def test_load_optimized_instruction_returns_none_when_no_resolved(tmp_path):
    """Returns None when version cannot be resolved."""
    optimizer = _make_optimizer(tmp_path)
    result = optimizer._load_optimized_instruction("deployer_fix_error", "nonexistent_version")
    assert result is None


def test_load_optimized_instruction_returns_none_when_file_missing(tmp_path):
    """Returns None when version resolves but dspy.json file is absent."""
    optimizer = _make_optimizer(tmp_path)
    # Create a bare version directory with no .dspy.json files
    version_dir = optimizer.optimized_dir / "v1"
    version_dir.mkdir(parents=True)
    result = optimizer._load_optimized_instruction("deployer_fix_error", "v1")
    assert result is None


def test_load_optimized_instruction_returns_instruction_when_present(tmp_path):
    """Returns the saved instruction when it exists."""
    optimizer = _make_optimizer(tmp_path)
    version_dir = optimizer.optimized_dir / "v1"
    version_dir.mkdir(parents=True)
    state = {"optimized_instruction": "Be concise and check paths before executing."}
    (version_dir / "deployer_fix_error.dspy.json").write_text(json.dumps(state))
    result = optimizer._load_optimized_instruction("deployer_fix_error", "v1")
    assert result == "Be concise and check paths before executing."


def test_list_compose_container_ids(monkeypatch):
    """Should parse docker ps output into a list of container ID strings."""

    def _fake_run(cmd, capture_output=True, text=True):
        return SimpleNamespace(stdout="abc123\ndef456\n\n", returncode=0, stderr="")

    monkeypatch.setattr(
        "app_operator.dspy_integration.eval_execute.subprocess.run",
        _fake_run,
    )
    ids = EvalExecuteOptimizer._list_compose_container_ids("my-project")
    assert ids == ["abc123", "def456"]


def test_list_compose_container_ids_empty(monkeypatch):
    """Returns empty list when no containers found."""

    def _fake_run(cmd, capture_output=True, text=True):
        return SimpleNamespace(stdout="", returncode=0, stderr="")

    monkeypatch.setattr(
        "app_operator.dspy_integration.eval_execute.subprocess.run",
        _fake_run,
    )
    ids = EvalExecuteOptimizer._list_compose_container_ids("empty-project")
    assert ids == []


def test_process_trajectory_message_assistant_classified(tmp_path):
    """_process_trajectory_message routes assistant snippets to the right list."""
    optimizer = _make_optimizer(tmp_path)
    evidence = _TrajectoryEvidence(run_name="r", app_name="a", run_type="train", status="ok", attempts=1)
    msg = {
        "role": "assistant",
        "content": "[Subagent error_log analyst]\ndocker-compose.yml not found in working dir.",
    }
    optimizer._process_trajectory_message(msg, evidence)
    assert evidence.error_insights
    assert "docker-compose.yml" in evidence.error_insights[0]


def test_process_trajectory_message_tool_call_extracts_error_signals(tmp_path):
    """_process_trajectory_message collects error-line signals from tool_call messages."""
    optimizer = _make_optimizer(tmp_path)
    evidence = _TrajectoryEvidence(run_name="r", app_name="a", run_type="train", status="ok", attempts=1)
    msg = {
        "role": "tool_call",
        "stderr": "ERROR: no such file or directory: deploy.sh\nsome normal output",
    }
    optimizer._process_trajectory_message(msg, evidence)
    assert any("error" in sig.lower() for sig in evidence.error_signals)


def test_elitism_first_candidate_is_unchanged_baseline(tmp_path, monkeypatch):
    """When elitism is active, the unchanged current instructions should be prepended."""
    optimizer = _make_optimizer(tmp_path, n_candidates=3, selection_mode="score")

    app_a = tmp_path / "appa"
    app_a.mkdir()
    (app_a / "README.md").write_text("a")

    work_dir = tmp_path / "workdir"
    work_dir.mkdir()
    output_dir = tmp_path / "prompts" / "optimized" / "v1"

    # _generate_candidates returns only mutations — elitism should prepend
    # the unchanged current instruction (the seed).
    monkeypatch.setattr(
        optimizer,
        "_generate_candidates",
        lambda prompt_names, current_instructions, trajectory_context: [
            {prompt_names[0]: "mutation A"},
            {prompt_names[0]: "mutation B"},
            {prompt_names[0]: "mutation C"},
        ],
    )
    monkeypatch.setattr(optimizer, "_cleanup_experiment_containers", lambda exp_dir: None)
    monkeypatch.setattr(optimizer, "_clean_exp_dir", lambda exp_dir: None)
    monkeypatch.setattr(optimizer, "_write_sds_toml", lambda *args, **kwargs: None)
    monkeypatch.setattr(optimizer, "_score_rlm_trajectory", lambda exp_dir: None)
    monkeypatch.setattr(
        optimizer,
        "_compute_training_trajectories_fingerprint",
        lambda *a, **kw: {"sha256": "test", "count": 0, "sample_files": []},
    )
    # Provide trajectory evidence so the optimizer doesn't enter cold start mode
    monkeypatch.setattr(
        optimizer,
        "_build_recent_trajectory_context",
        lambda **kw: {"deployer_fix_error": "prior trajectory evidence"},
    )

    # Track written candidates to verify elitism
    written_candidates = []
    original_write = optimizer._write_candidate

    def tracking_write(candidate, version):
        written_candidates.append(dict(candidate))
        return original_write(candidate, version)

    monkeypatch.setattr(optimizer, "_write_candidate", tracking_write)

    # All candidates succeed — the first (elitist) should win by selection
    monkeypatch.setattr(
        optimizer,
        "_run_candidate_subprocess",
        lambda cmd: SimpleNamespace(returncode=0, stderr="", stdout=""),
    )

    result = optimizer.optimize(
        prompt_names=["deployer_fix_error"],
        train_apps=[app_a],
        work_dir=work_dir,
        output_dir=output_dir,
        iteration=2,
        current_version=None,
        provider="test",
        max_retries=1,
        rate_limit_backoff=1,
        inter_run_delay=0,
    )

    assert result["success"] is True
    # The first written candidate should be the seed (elitist baseline),
    # and the generated mutations should keep their original order after it.
    seed_instruction = optimizer._seed_instruction("deployer_fix_error")
    assert written_candidates[0]["deployer_fix_error"] == seed_instruction
    assert written_candidates[1]["deployer_fix_error"] == "mutation A"
    assert written_candidates[2]["deployer_fix_error"] == "mutation B"
    assert written_candidates[3]["deployer_fix_error"] == "mutation C"


def test_previous_winner_is_retained_as_historical_metadata(tmp_path, monkeypatch):
    """Previous best should be tracked separately while c1 is rerun."""
    optimizer = _make_optimizer(tmp_path, n_candidates=2, selection_mode="score")

    app_a = tmp_path / "appa"
    app_a.mkdir()
    (app_a / "README.md").write_text("a")

    work_dir = tmp_path / "workdir"
    work_dir.mkdir()

    # Write a fake previous version with a known best_score
    prev_version_dir = tmp_path / "prompts" / "optimized" / "simple_LLM" / "v1"
    prev_version_dir.mkdir(parents=True)
    prev_metadata = {
        "best_score": 0.865,
        "best_candidate_index": 1,
        "candidate_app_scores": [{"appa": 0.865}],
        "candidate_run_labels": [{"true_success": 1}],
    }
    (prev_version_dir / "metadata.json").write_text(json.dumps(prev_metadata))

    output_dir = tmp_path / "prompts" / "optimized" / "simple_LLM" / "v2"

    monkeypatch.setattr(
        optimizer,
        "_generate_candidates",
        lambda prompt_names, current_instructions, trajectory_context: [
            {prompt_names[0]: "mutation A"},
            {prompt_names[0]: "mutation B"},
        ],
    )
    monkeypatch.setattr(optimizer, "_cleanup_experiment_containers", lambda exp_dir: None)
    monkeypatch.setattr(optimizer, "_clean_exp_dir", lambda exp_dir: None)
    monkeypatch.setattr(optimizer, "_write_sds_toml", lambda *args, **kwargs: None)
    monkeypatch.setattr(optimizer, "_score_rlm_trajectory", lambda exp_dir: None)
    monkeypatch.setattr(
        optimizer,
        "_compute_training_trajectories_fingerprint",
        lambda *a, **kw: {"sha256": "test", "count": 0, "sample_files": []},
    )
    monkeypatch.setattr(
        optimizer,
        "_build_recent_trajectory_context",
        lambda **kw: {"deployer_fix_error": "prior trajectory evidence"},
    )

    # Track which candidates trigger a real deployment run
    # operation_name is "candidate_{c_idx+1}_{app_name}", so parse c_idx from it
    deployed_candidates: list[int] = []

    def _fake_run(cmd):
        # cmd[-1] is str(exp_dir), name looks like "iter2_c2_appa" → c_idx = 1
        m = re.match(r"iter\d+_c(\d+)_", Path(cmd[-1]).name)
        c_idx = (int(m.group(1)) - 1) if m else -1
        deployed_candidates.append(c_idx)
        return SimpleNamespace(returncode=0, stderr="", stdout="")

    monkeypatch.setattr(optimizer, "_run_candidate_subprocess", _fake_run)

    result = optimizer.optimize(
        prompt_names=["deployer_fix_error"],
        train_apps=[app_a],
        work_dir=work_dir,
        output_dir=output_dir,
        iteration=2,
        current_version="simple_LLM/v1",
        provider="test",
        max_retries=1,
        rate_limit_backoff=1,
        inter_run_delay=0,
        output_prefix="simple_LLM",
    )

    assert result["success"] is True
    assert 0 in deployed_candidates, "Incumbent c1 should be reevaluated in the current iteration"
    assert 1 in deployed_candidates, "Mutation c2 should have been deployed"
    assert 2 in deployed_candidates, "Mutation c3 should have been deployed"
    metadata = json.loads((output_dir / "metadata.json").read_text())
    assert metadata["historical_best"]["score"] == pytest.approx(0.865)
    assert metadata["historical_best"]["per_app_scores"] == {"appa": 0.865}
    assert metadata["historical_best"]["classification_counts"] == {"true_success": 1}
    assert metadata["historical_best"]["source_version"] == "simple_LLM/v1"
    assert metadata["historical_best"]["best_candidate_index"] == 1


def test_historical_best_metadata_with_llm_selection_does_not_crash(tmp_path, monkeypatch):
    """Historical-best metadata should remain compatible with LLM selection formatting."""
    optimizer = _make_optimizer(tmp_path, n_candidates=2, selection_mode="llm")

    app_a = tmp_path / "appa"
    app_a.mkdir()
    (app_a / "README.md").write_text("a")

    work_dir = tmp_path / "workdir"
    work_dir.mkdir()

    prev_version_dir = tmp_path / "prompts" / "optimized" / "simple_LLM" / "v1"
    prev_version_dir.mkdir(parents=True)
    prev_metadata = {
        "best_score": 0.865,
        "best_candidate_index": 1,
        "candidate_app_scores": [{"appa": 0.865}],
        "candidate_run_labels": [{"true_success": 1}],
    }
    (prev_version_dir / "metadata.json").write_text(json.dumps(prev_metadata))

    output_dir = tmp_path / "prompts" / "optimized" / "simple_LLM" / "v2"

    monkeypatch.setattr(
        optimizer,
        "_generate_candidates",
        lambda prompt_names, current_instructions, trajectory_context: [
            {prompt_names[0]: "mutation A"},
            {prompt_names[0]: "mutation B"},
        ],
    )
    monkeypatch.setattr(optimizer, "_cleanup_experiment_containers", lambda exp_dir: None)
    monkeypatch.setattr(optimizer, "_clean_exp_dir", lambda exp_dir: None)
    monkeypatch.setattr(optimizer, "_write_sds_toml", lambda *args, **kwargs: None)
    monkeypatch.setattr(optimizer, "_score_rlm_trajectory", lambda exp_dir: None)
    monkeypatch.setattr(
        optimizer,
        "_compute_training_trajectories_fingerprint",
        lambda *a, **kw: {"sha256": "test", "count": 0, "sample_files": []},
    )
    monkeypatch.setattr(
        optimizer,
        "_build_recent_trajectory_context",
        lambda **kw: {"deployer_fix_error": "prior trajectory evidence"},
    )

    deployed_candidates: list[int] = []

    def _fake_run(cmd):
        m = re.match(r"iter\d+_c(\d+)_", Path(cmd[-1]).name)
        c_idx = (int(m.group(1)) - 1) if m else -1
        deployed_candidates.append(c_idx)
        return SimpleNamespace(returncode=0, stderr="", stdout="")

    monkeypatch.setattr(optimizer, "_run_candidate_subprocess", _fake_run)
    monkeypatch.setattr(
        "app_operator.dspy_integration.eval_execute.litellm.completion",
        lambda **kwargs: SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content='{"chosen_candidate": 2, "reason": "better", "confidence": 0.9}')
                )
            ]
        ),
    )

    result = optimizer.optimize(
        prompt_names=["deployer_fix_error"],
        train_apps=[app_a],
        work_dir=work_dir,
        output_dir=output_dir,
        iteration=2,
        current_version="simple_LLM/v1",
        provider="test",
        max_retries=1,
        rate_limit_backoff=1,
        inter_run_delay=0,
        output_prefix="simple_LLM",
    )

    assert result["success"] is True
    assert 0 in deployed_candidates, "Incumbent c1 should be reevaluated in the current iteration"
    assert 1 in deployed_candidates, "Mutation c2 should have been deployed"
    assert 2 in deployed_candidates, "Mutation c3 should have been deployed"
    metadata = json.loads((output_dir / "metadata.json").read_text())
    assert metadata["historical_best"]["score"] == pytest.approx(0.865)


def test_load_historical_best_summary_ignores_malformed_metadata_shapes(tmp_path):
    """Malformed prior-best metadata should degrade gracefully without raising."""
    optimizer = _make_optimizer(tmp_path, n_candidates=2, selection_mode="score")

    prev_version_dir = tmp_path / "prompts" / "optimized" / "simple_LLM" / "v1"
    prev_version_dir.mkdir(parents=True)
    prev_metadata = {
        "best_score": 0.5,
        "best_candidate_index": 1,
        "candidate_app_scores": [0.5],  # malformed: expected dict entry
        "candidate_run_labels": ["true_success"],  # malformed: expected dict entry
    }
    (prev_version_dir / "metadata.json").write_text(json.dumps(prev_metadata))

    cached = optimizer._load_historical_best_summary("simple_LLM/v1", "simple_LLM")

    assert cached is not None
    assert cached["score"] == pytest.approx(0.5)
    assert cached["per_app_scores"] == {}
    assert cached["classification_counts"] == {}
    assert cached["source_version"] == "simple_LLM/v1"
