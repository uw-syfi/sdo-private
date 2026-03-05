"""Tests for EvalExecuteOptimizer."""

import json
from pathlib import Path
from types import SimpleNamespace

from app_operator.dspy_integration.config import DSPyConfig, DSPyOptimizationConfig
from app_operator.dspy_integration.eval_execute import EvalExecuteOptimizer


def _make_optimizer(
    tmp_path,
    *,
    n_candidates=2,
    selection_mode="hybrid",
    selection_top_k=3,
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


def test_build_recent_trajectory_context_prioritizes_failed_and_recent(monkeypatch, tmp_path):
    """Weighted ranking should prioritize failed/recent evidence."""
    optimizer = _make_optimizer(tmp_path)

    class _Ev:
        def __init__(
            self,
            *,
            run_name,
            app_name,
            status,
            attempts,
            weight,
            trajectory,
        ):
            self.run_name = run_name
            self.app_name = app_name
            self.status = status
            self.attempts = attempts
            self.weight = weight
            self.trajectory_insights = trajectory
            self.error_insights = []
            self.script_insights = []
            self.repo_insights = []
            self.error_signals = []

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


def test_build_recent_trajectory_context_excludes_validation_runs(tmp_path):
    """Validation trajectories should not be used as optimization evidence."""
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
                    }
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
    assert "VAL_ONLY_MARKER" not in ctx


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
        "app_operator.dspy_integration.eval_execute.run_subprocess_with_rate_limit_handling",
        lambda **kwargs: (SimpleNamespace(returncode=0), True, None),
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
        "app_operator.dspy_integration.eval_execute.run_subprocess_with_rate_limit_handling",
        lambda **kwargs: (SimpleNamespace(returncode=0), True, None),
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
        "candidate_3_appa": False,
        "candidate_3_appb": False,
    }

    def _fake_run(**kwargs):
        op = kwargs["operation_name"]
        success = outcomes[op]
        return (
            SimpleNamespace(returncode=0 if success else 1),
            success,
            None if success else "simulated failure",
        )

    monkeypatch.setattr(
        "app_operator.dspy_integration.eval_execute.run_subprocess_with_rate_limit_handling",
        _fake_run,
    )

    def _fake_completion(**kwargs):
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content='{"chosen_candidate": 2, "reason": "better failure profile", "confidence": 0.77}'
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
    assert result["best_candidate"] == 2
    assert result["selection"]["selection_mode"] == "hybrid"
    assert result["selection"]["llm_used"] is True
    assert result["selection"]["fallback_to_score"] is False
    assert result["selection"]["llm_choice"] == 2

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
    }

    def _fake_run(**kwargs):
        op = kwargs["operation_name"]
        success = outcomes[op]
        return (
            SimpleNamespace(returncode=0 if success else 1),
            success,
            None if success else "simulated failure",
        )

    monkeypatch.setattr(
        "app_operator.dspy_integration.eval_execute.run_subprocess_with_rate_limit_handling",
        _fake_run,
    )
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
        "app_operator.dspy_integration.eval_execute.run_subprocess_with_rate_limit_handling",
        lambda **kwargs: (SimpleNamespace(returncode=0), True, None),
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
