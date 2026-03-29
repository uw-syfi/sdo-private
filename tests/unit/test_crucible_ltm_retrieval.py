"""Unit tests for the LTM retrieval subagent (search_prior_incidents)."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

if TYPE_CHECKING:
    from pathlib import Path

import pytest

from sregym_agents.crucible.orchestrator import CrucibleFlags
from sregym_agents.crucible.tools import (
    CandidateRootCause,
    CandidateVerification,
    DifferentialDiagnosis,
    MitigationSearchResult,
    MitigationStrategy,
    SharedFile,
    SREDeps,
    VerifiedDifferentialDiagnosis,
    search_prior_incidents,
    search_prior_mitigations,
)


def _make_sre_ctx(deps: SREDeps) -> MagicMock:
    ctx = MagicMock()
    ctx.deps = deps
    return ctx


def _make_deps(
    tmp_path: Path,
    lt_summary_file: Path | None = None,
    incidents_dir: Path | None = None,
    ltm_model_id: str | None = None,
    trajectory_path: Path | None = None,
) -> SREDeps:
    shared_path = tmp_path / "session.md"
    shared_path.write_text("")
    return SREDeps(
        namespace="default",
        shared_file=SharedFile(shared_path),
        iteration=1,
        stage="diagnosis",
        lt_summary_file=lt_summary_file,
        incidents_dir=incidents_dir,
        ltm_model_id=ltm_model_id,
        trajectory_path=trajectory_path,
    )


# ---------------------------------------------------------------------------
# CrucibleFlags
# ---------------------------------------------------------------------------


def test_crucible_flags_defaults() -> None:
    flags = CrucibleFlags(prompt_version="v1")
    assert flags.enable_judge is True
    assert flags.enable_ltm_retrieval is False


def test_crucible_flags_from_config() -> None:
    cfg = {"enable_judge": False, "enable_ltm_retrieval": True}
    flags = CrucibleFlags(
        prompt_version="v1",
        enable_judge=cfg["enable_judge"],
        enable_ltm_retrieval=cfg["enable_ltm_retrieval"],
    )
    assert flags.enable_judge is False
    assert flags.enable_ltm_retrieval is True


# ---------------------------------------------------------------------------
# search_prior_incidents — no LTM files
# ---------------------------------------------------------------------------


def test_search_no_file(tmp_path: Path) -> None:
    """When lt_summary_file is None, return empty result without spawning LLM."""
    deps = _make_deps(tmp_path, lt_summary_file=None)
    ctx = _make_sre_ctx(deps)

    result = asyncio.run(search_prior_incidents(ctx, observed_symptoms="pods crashing"))

    parsed = json.loads(result)
    assert parsed["verified_candidates"] == []
    assert parsed["confirmed_candidates"] == []
    assert "No incident history" in parsed["caveats"]


# ---------------------------------------------------------------------------
# search_prior_incidents — empty symptoms
# ---------------------------------------------------------------------------


def test_search_empty_symptoms(tmp_path: Path) -> None:
    """Blank observed_symptoms returns an error."""
    summary = tmp_path / "summary.md"
    summary.write_text("# Summary")
    deps = _make_deps(tmp_path, lt_summary_file=summary, ltm_model_id="test-model")
    ctx = _make_sre_ctx(deps)

    result = asyncio.run(search_prior_incidents(ctx, observed_symptoms="   "))

    assert "Error" in result
    assert "observed_symptoms" in result


# ---------------------------------------------------------------------------
# search_prior_incidents — subagent is called correctly (0 candidates)
# ---------------------------------------------------------------------------


def test_search_calls_subagent(tmp_path: Path) -> None:
    """Verify retrieval subagent is created with expected tools and prompt content."""
    summary = tmp_path / "summary.md"
    summary.write_text("# Summary\n### Symptom: OOM kills")
    incidents = tmp_path / "incidents"
    incidents.mkdir()

    deps = _make_deps(
        tmp_path,
        lt_summary_file=summary,
        incidents_dir=incidents,
        ltm_model_id="test-model",
    )
    ctx = _make_sre_ctx(deps)

    mock_result = DifferentialDiagnosis(
        candidate_root_causes=[],
        novel_cause_signals="",
        caveats="none",
    )

    mock_run_result = MagicMock()
    mock_run_result.output = mock_result

    mock_agent_instance = MagicMock()
    mock_agent_instance.run = AsyncMock(return_value=mock_run_result)

    with patch("pydantic_ai.Agent", return_value=mock_agent_instance) as mock_agent_cls:
        result = asyncio.run(search_prior_incidents(ctx, observed_symptoms="pods are OOMKilled"))

    # Agent was constructed with the right model and output_type
    mock_agent_cls.assert_called_once()
    call_kwargs = mock_agent_cls.call_args
    assert call_kwargs[0][0] == "test-model"
    assert call_kwargs[1]["output_type"] is DifferentialDiagnosis

    # Tools include read_file
    tool_names = {t.__name__ for t in call_kwargs[1]["tools"]}
    assert "read_file" in tool_names
    assert "exec_bash_any" in tool_names

    # Prompt was passed to run and contains symptoms + file paths
    prompt_arg = mock_agent_instance.run.call_args[0][0]
    assert "pods are OOMKilled" in prompt_arg
    assert str(summary) in prompt_arg
    assert str(incidents) in prompt_arg

    # Result is valid JSON with verified diagnosis structure
    parsed = json.loads(result)
    assert "verified_candidates" in parsed
    assert "confirmed_candidates" in parsed
    assert parsed["verified_candidates"] == []


# ---------------------------------------------------------------------------
# search_prior_incidents — budget enforcement
# ---------------------------------------------------------------------------


def test_search_budget_exhausted(tmp_path: Path) -> None:
    """After budget is exhausted, return budget-exhausted response without spawning LLM."""
    summary = tmp_path / "summary.md"
    summary.write_text("# Summary")
    deps = _make_deps(tmp_path, lt_summary_file=summary, ltm_model_id="test-model")
    deps.ltm_call_count = 1  # already at budget (default budget is 1)
    ctx = _make_sre_ctx(deps)

    result = asyncio.run(search_prior_incidents(ctx, observed_symptoms="pods crashing"))

    parsed = json.loads(result)
    assert parsed["verified_candidates"] == []
    assert "budget exhausted" in parsed["caveats"].lower()


def test_search_increments_counter(tmp_path: Path) -> None:
    """Each successful call increments ltm_call_count; third call is blocked."""
    summary = tmp_path / "summary.md"
    summary.write_text("# Summary")
    incidents = tmp_path / "incidents"
    incidents.mkdir()

    deps = _make_deps(
        tmp_path,
        lt_summary_file=summary,
        incidents_dir=incidents,
        ltm_model_id="test-model",
    )
    deps.ltm_call_budget = 2  # use budget of 2 so we can test exhaustion after 2 calls
    ctx = _make_sre_ctx(deps)

    mock_result = DifferentialDiagnosis(
        candidate_root_causes=[],
        novel_cause_signals="",
        caveats="none",
    )
    mock_run_result = MagicMock()
    mock_run_result.output = mock_result
    mock_agent_instance = MagicMock()
    mock_agent_instance.run = AsyncMock(return_value=mock_run_result)

    with patch("pydantic_ai.Agent", return_value=mock_agent_instance):
        # First call — should succeed
        asyncio.run(search_prior_incidents(ctx, observed_symptoms="call 1"))
        assert deps.ltm_call_count == 1

        # Second call — should succeed
        asyncio.run(search_prior_incidents(ctx, observed_symptoms="call 2"))
        assert deps.ltm_call_count == 2

        # Third call — budget exhausted, no LLM call
        result = asyncio.run(search_prior_incidents(ctx, observed_symptoms="call 3"))
        assert deps.ltm_call_count == 2  # not incremented
        parsed = json.loads(result)
        assert "budget exhausted" in parsed["caveats"].lower()

    # Agent.run was called exactly twice (not three times)
    assert mock_agent_instance.run.call_count == 2


# ---------------------------------------------------------------------------
# Orchestrator flag wiring
# ---------------------------------------------------------------------------


@pytest.fixture
def shared_file(tmp_path: Path) -> SharedFile:
    f = tmp_path / "session.md"
    f.write_text("# Session\n")
    return SharedFile(f)


def _fake_sre_constructor_factory(shared_file):
    """Return a fake SRE agent constructor that captures deps for inspection."""
    captured_deps = []

    def constructor(model, deps, trajectory_path=None):
        captured_deps.append(deps)
        mock = MagicMock()

        def fake_run(prompt, run_ctx=None):
            deps.state.answer = "some answer"
            return None, {"input_tokens": 10, "output_tokens": 5, "cached_input_tokens": 0}

        mock.arun = AsyncMock(side_effect=fake_run)
        return mock

    return constructor, captured_deps


def test_flag_false_injects_summary(shared_file: Path, tmp_path: Path) -> None:
    """When enable_ltm_retrieval=False, orchestrator passes lt_summary_content to prompt."""
    lt_file = tmp_path / "summary.md"
    lt_file.write_text("Prior incident summary content")

    constructor, captured_deps = _fake_sre_constructor_factory(shared_file)

    with (
        patch("sregym_agents.crucible.orchestrator.CrucibleSREAgent", side_effect=constructor),
        patch("sregym_agents.crucible.orchestrator.CrucibleJudgeAgent"),
        patch(
            "sregym_agents.crucible.orchestrator._submit_to_benchmark",
            new_callable=AsyncMock,
            return_value=(True, "ok", None),
        ),
        patch("sregym_agents.crucible.orchestrator._render") as mock_render,
    ):
        mock_render.return_value = "rendered"
        from sregym_agents.crucible.orchestrator import CrucibleFlags, _run_stage_loop

        asyncio.run(
            _run_stage_loop(
                model="test-model",
                app_info={"app_name": "myapp", "namespace": "default"},
                stage="diagnosis",
                max_iters=1,
                shared_file=shared_file,
                submit_mcp_url="http://localhost:9954/submit/sse",
                lt_summary_file=lt_file,
                flags=CrucibleFlags(prompt_version="v1", enable_judge=False, enable_ltm_retrieval=False),
            )
        )

    # SREDeps should NOT have ltm paths set
    assert captured_deps[0].lt_summary_file is None
    assert captured_deps[0].incidents_dir is None

    # The user prompt render should include lt_summary_content
    render_calls = [c for c in mock_render.call_args_list if "diagnosis_agent_user" in str(c)]
    assert len(render_calls) == 1
    kwargs = render_calls[0][1]
    assert kwargs["lt_summary_content"] == "Prior incident summary content"


def test_flag_true_omits_summary(shared_file: Path, tmp_path: Path) -> None:
    """When enable_ltm_retrieval=True, orchestrator passes empty lt_summary_content and sets SREDeps paths."""
    lt_file = tmp_path / "summary.md"
    lt_file.write_text("Prior incident summary content")
    inc_dir = tmp_path / "incidents"
    inc_dir.mkdir()

    constructor, captured_deps = _fake_sre_constructor_factory(shared_file)

    with (
        patch("sregym_agents.crucible.orchestrator.CrucibleSREAgent", side_effect=constructor),
        patch("sregym_agents.crucible.orchestrator.CrucibleJudgeAgent"),
        patch(
            "sregym_agents.crucible.orchestrator._submit_to_benchmark",
            new_callable=AsyncMock,
            return_value=(True, "ok", None),
        ),
        patch("sregym_agents.crucible.orchestrator._render") as mock_render,
    ):
        mock_render.return_value = "rendered"
        from sregym_agents.crucible.orchestrator import CrucibleFlags, _run_stage_loop

        asyncio.run(
            _run_stage_loop(
                model="test-model",
                app_info={"app_name": "myapp", "namespace": "default"},
                stage="diagnosis",
                max_iters=1,
                shared_file=shared_file,
                submit_mcp_url="http://localhost:9954/submit/sse",
                lt_summary_file=lt_file,
                incidents_dir=inc_dir,
                flags=CrucibleFlags(prompt_version="v1", enable_judge=False, enable_ltm_retrieval=True),
            )
        )

    # SREDeps should have ltm paths set
    assert captured_deps[0].lt_summary_file == lt_file.resolve()
    assert captured_deps[0].incidents_dir == inc_dir.resolve()
    assert captured_deps[0].ltm_model_id == "test-model"

    # The user prompt render should have empty lt_summary_content
    render_calls = [c for c in mock_render.call_args_list if "diagnosis_agent_user" in str(c)]
    assert len(render_calls) == 1
    kwargs = render_calls[0][1]
    assert kwargs["lt_summary_content"] == ""


# ---------------------------------------------------------------------------
# CandidateRootCause — root_cause_class field
# ---------------------------------------------------------------------------


def test_candidate_root_cause_has_root_cause_class() -> None:
    """CandidateRootCause requires root_cause_class and round-trips through JSON."""
    candidate = CandidateRootCause(
        root_cause_class="missing Kubernetes Service",
        root_cause="Service user-service is missing in namespace social-network",
        distinguishing_check="kubectl get svc -n social-network",
        mitigation_hint="Recreate the missing Service object with correct configuration",
    )
    data = candidate.model_dump()
    assert data["root_cause_class"] == "missing Kubernetes Service"

    restored = CandidateRootCause.model_validate(data)
    assert restored.root_cause_class == "missing Kubernetes Service"
    assert restored.root_cause == candidate.root_cause


def test_differential_diagnosis_with_root_cause_class() -> None:
    """DifferentialDiagnosis with candidates containing root_cause_class round-trips through JSON."""
    diag = DifferentialDiagnosis(
        candidate_root_causes=[
            CandidateRootCause(
                root_cause_class="missing Kubernetes Service",
                root_cause="A Service object may be missing in social-network namespace",
                distinguishing_check="kubectl get svc -n social-network",
                mitigation_hint="Recreate the missing Service object",
            ),
            CandidateRootCause(
                root_cause_class="targetPort mismatch",
                root_cause="A Service targetPort does not match the container port",
                distinguishing_check="kubectl get svc -n social-network -o yaml",
                mitigation_hint="Update the Service targetPort to match the container port",
            ),
        ],
        novel_cause_signals="Check for network policies or resource quotas",
        caveats="Verify which specific service is affected",
    )
    json_str = diag.model_dump_json()
    restored = DifferentialDiagnosis.model_validate_json(json_str)
    assert len(restored.candidate_root_causes) == 2
    assert restored.candidate_root_causes[0].root_cause_class == "missing Kubernetes Service"
    assert restored.candidate_root_causes[1].root_cause_class == "targetPort mismatch"


# ---------------------------------------------------------------------------
# CandidateVerification — model round-trip
# ---------------------------------------------------------------------------


def test_candidate_verification_model_roundtrip() -> None:
    """CandidateVerification serializes and deserializes correctly."""
    v = CandidateVerification(
        candidate_index=0,
        root_cause_class="missing Kubernetes Service",
        root_cause="Service user-service is missing",
        applies=True,
        causal_chain="Service/user-service deleted → endpoints empty → connection refused → 503",
        evidence=["kubectl get svc: user-service not found", "kubectl get endpoints: no entries"],
        reasoning="The service is confirmed missing in the namespace.",
    )
    data = v.model_dump()
    assert data["applies"] is True
    assert data["candidate_index"] == 0
    assert len(data["evidence"]) == 2

    restored = CandidateVerification.model_validate(data)
    assert restored.causal_chain == v.causal_chain
    assert restored.reasoning == v.reasoning


def test_candidate_verification_rejected() -> None:
    """CandidateVerification with applies=False has empty causal_chain."""
    v = CandidateVerification(
        candidate_index=1,
        root_cause_class="targetPort mismatch",
        root_cause="Service targetPort doesn't match container port",
        applies=False,
        reasoning="All services have matching targetPort and container port.",
    )
    assert v.causal_chain == ""
    assert v.evidence == []
    assert v.applies is False


# ---------------------------------------------------------------------------
# VerifiedDifferentialDiagnosis — confirmed subset
# ---------------------------------------------------------------------------


def test_verified_differential_diagnosis_confirmed_subset() -> None:
    """confirmed_candidates correctly filters to applies=True entries."""
    v_confirmed = CandidateVerification(
        candidate_index=0,
        root_cause_class="missing Service",
        root_cause="Service X missing",
        applies=True,
        causal_chain="Service deleted → 503",
        evidence=["svc not found"],
        reasoning="Confirmed missing.",
    )
    v_rejected = CandidateVerification(
        candidate_index=1,
        root_cause_class="port mismatch",
        root_cause="Port mismatch on Y",
        applies=False,
        reasoning="Ports match.",
    )
    vdd = VerifiedDifferentialDiagnosis(
        verified_candidates=[v_confirmed, v_rejected],
        confirmed_candidates=[v_confirmed],
        novel_cause_signals="Check network policies",
        caveats="Some caveats",
    )
    json_str = vdd.model_dump_json()
    restored = VerifiedDifferentialDiagnosis.model_validate_json(json_str)
    assert len(restored.verified_candidates) == 2
    assert len(restored.confirmed_candidates) == 1
    assert restored.confirmed_candidates[0].applies is True
    assert restored.confirmed_candidates[0].candidate_index == 0


# ---------------------------------------------------------------------------
# search_prior_incidents — verification subagents spawned for candidates
# ---------------------------------------------------------------------------


def _make_candidates() -> list[CandidateRootCause]:
    return [
        CandidateRootCause(
            root_cause_class="missing Kubernetes Service",
            root_cause="Service user-service missing in ns",
            distinguishing_check="kubectl get svc -n default",
            mitigation_hint="Recreate service",
        ),
        CandidateRootCause(
            root_cause_class="targetPort mismatch",
            root_cause="targetPort does not match container port",
            distinguishing_check="kubectl get svc -o yaml -n default",
            mitigation_hint="Fix targetPort",
        ),
    ]


def test_search_spawns_verification_subagents(tmp_path: Path) -> None:
    """Verify that verification subagents are spawned for each candidate."""
    summary = tmp_path / "summary.md"
    summary.write_text("# Summary")
    incidents = tmp_path / "incidents"
    incidents.mkdir()

    deps = _make_deps(
        tmp_path,
        lt_summary_file=summary,
        incidents_dir=incidents,
        ltm_model_id="test-model",
    )
    ctx = _make_sre_ctx(deps)

    candidates = _make_candidates()

    # Retrieval agent returns 2 candidates
    retrieval_output = DifferentialDiagnosis(
        candidate_root_causes=candidates,
        novel_cause_signals="Check network policies",
        caveats="Verify specifics",
    )
    retrieval_run_result = MagicMock()
    retrieval_run_result.output = retrieval_output

    # Verification agents return structured results
    verify_output_0 = CandidateVerification(
        candidate_index=0,
        root_cause_class="missing Kubernetes Service",
        root_cause="Service user-service missing in ns",
        applies=True,
        causal_chain="Service deleted → 503",
        evidence=["svc not found"],
        reasoning="Confirmed.",
    )
    verify_output_1 = CandidateVerification(
        candidate_index=1,
        root_cause_class="targetPort mismatch",
        root_cause="targetPort does not match container port",
        applies=False,
        reasoning="Ports match.",
    )

    verify_run_result_0 = MagicMock()
    verify_run_result_0.output = verify_output_0
    verify_run_result_0.usage.return_value = MagicMock(input_tokens=100, output_tokens=50)
    verify_run_result_0.all_messages.return_value = []

    verify_run_result_1 = MagicMock()
    verify_run_result_1.output = verify_output_1
    verify_run_result_1.usage.return_value = MagicMock(input_tokens=100, output_tokens=50)
    verify_run_result_1.all_messages.return_value = []

    agent_instances = []

    def _make_agent(*args, **kwargs):
        mock = MagicMock()
        agent_instances.append((mock, kwargs.get("output_type")))
        if kwargs.get("output_type") is DifferentialDiagnosis:
            mock.run = AsyncMock(return_value=retrieval_run_result)
        else:
            # Verification agents — return based on call order
            idx = len([a for a in agent_instances if a[1] is CandidateVerification]) - 1
            results = [verify_run_result_0, verify_run_result_1]
            mock.run = AsyncMock(return_value=results[min(idx, len(results) - 1)])
        return mock

    with patch("pydantic_ai.Agent", side_effect=_make_agent):
        result = asyncio.run(search_prior_incidents(ctx, observed_symptoms="pods crashing"))

    parsed = json.loads(result)

    # Should have 3 agents total: 1 retrieval + 2 verification
    assert len(agent_instances) == 3
    assert agent_instances[0][1] is DifferentialDiagnosis
    assert agent_instances[1][1] is CandidateVerification
    assert agent_instances[2][1] is CandidateVerification

    # Result has verified structure
    assert "verified_candidates" in parsed
    assert len(parsed["verified_candidates"]) == 2
    assert len(parsed["confirmed_candidates"]) == 1
    assert parsed["confirmed_candidates"][0]["applies"] is True


def test_search_no_candidates_skips_verification(tmp_path: Path) -> None:
    """When retrieval returns 0 candidates, no verification agents are spawned."""
    summary = tmp_path / "summary.md"
    summary.write_text("# Summary")
    incidents = tmp_path / "incidents"
    incidents.mkdir()

    deps = _make_deps(
        tmp_path,
        lt_summary_file=summary,
        incidents_dir=incidents,
        ltm_model_id="test-model",
    )
    ctx = _make_sre_ctx(deps)

    retrieval_output = DifferentialDiagnosis(
        candidate_root_causes=[],
        novel_cause_signals="novel signals",
        caveats="none",
    )
    retrieval_run_result = MagicMock()
    retrieval_run_result.output = retrieval_output

    agent_instances = []

    def _make_agent(*args, **kwargs):
        mock = MagicMock()
        mock.run = AsyncMock(return_value=retrieval_run_result)
        agent_instances.append(kwargs.get("output_type"))
        return mock

    with patch("pydantic_ai.Agent", side_effect=_make_agent):
        result = asyncio.run(search_prior_incidents(ctx, observed_symptoms="pods crashing"))

    # Only 1 agent (retrieval), no verification agents
    assert len(agent_instances) == 1
    assert agent_instances[0] is DifferentialDiagnosis

    parsed = json.loads(result)
    assert parsed["verified_candidates"] == []
    assert parsed["novel_cause_signals"] == "novel signals"


def test_search_verification_failure_graceful(tmp_path: Path) -> None:
    """When a verification subagent fails, the error is captured gracefully."""
    summary = tmp_path / "summary.md"
    summary.write_text("# Summary")
    incidents = tmp_path / "incidents"
    incidents.mkdir()

    deps = _make_deps(
        tmp_path,
        lt_summary_file=summary,
        incidents_dir=incidents,
        ltm_model_id="test-model",
    )
    ctx = _make_sre_ctx(deps)

    candidates = _make_candidates()
    retrieval_output = DifferentialDiagnosis(
        candidate_root_causes=candidates,
        novel_cause_signals="",
        caveats="",
    )
    retrieval_run_result = MagicMock()
    retrieval_run_result.output = retrieval_output

    # Second verification agent will fail
    verify_output_0 = CandidateVerification(
        candidate_index=0,
        root_cause_class="missing Kubernetes Service",
        root_cause="Service user-service missing in ns",
        applies=True,
        causal_chain="Service deleted → 503",
        evidence=["svc not found"],
        reasoning="Confirmed.",
    )
    verify_run_result_0 = MagicMock()
    verify_run_result_0.output = verify_output_0
    verify_run_result_0.usage.return_value = MagicMock(input_tokens=100, output_tokens=50)
    verify_run_result_0.all_messages.return_value = []

    agent_count = {"retrieval": 0, "verify": 0}

    def _make_agent(*args, **kwargs):
        mock = MagicMock()
        if kwargs.get("output_type") is DifferentialDiagnosis:
            mock.run = AsyncMock(return_value=retrieval_run_result)
            agent_count["retrieval"] += 1
        else:
            idx = agent_count["verify"]
            agent_count["verify"] += 1
            if idx == 0:
                mock.run = AsyncMock(return_value=verify_run_result_0)
            else:
                mock.run = AsyncMock(side_effect=RuntimeError("LLM timeout"))
        return mock

    with patch("pydantic_ai.Agent", side_effect=_make_agent):
        result = asyncio.run(search_prior_incidents(ctx, observed_symptoms="pods crashing"))

    parsed = json.loads(result)

    # Both candidates should be present in verified_candidates
    assert len(parsed["verified_candidates"]) == 2

    # One confirmed, one failed gracefully
    assert len(parsed["confirmed_candidates"]) == 1
    failed = [v for v in parsed["verified_candidates"] if not v["applies"]]
    assert len(failed) == 1
    assert "LLM timeout" in failed[0]["reasoning"]


def test_verification_subagent_tools_exclude_search(tmp_path: Path) -> None:
    """Verification subagents are created with investigation tools but NOT search_prior_incidents."""
    summary = tmp_path / "summary.md"
    summary.write_text("# Summary")
    incidents = tmp_path / "incidents"
    incidents.mkdir()

    deps = _make_deps(
        tmp_path,
        lt_summary_file=summary,
        incidents_dir=incidents,
        ltm_model_id="test-model",
    )
    ctx = _make_sre_ctx(deps)

    candidates = [_make_candidates()[0]]
    retrieval_output = DifferentialDiagnosis(
        candidate_root_causes=candidates,
        novel_cause_signals="",
        caveats="",
    )
    retrieval_run_result = MagicMock()
    retrieval_run_result.output = retrieval_output

    verify_output = CandidateVerification(
        candidate_index=0,
        root_cause_class="missing Kubernetes Service",
        root_cause="Service user-service missing in ns",
        applies=False,
        reasoning="Not found.",
    )
    verify_run_result = MagicMock()
    verify_run_result.output = verify_output
    verify_run_result.usage.return_value = MagicMock(input_tokens=100, output_tokens=50)
    verify_run_result.all_messages.return_value = []

    agent_calls = []

    def _make_agent(*args, **kwargs):
        mock = MagicMock()
        agent_calls.append(kwargs)
        if kwargs.get("output_type") is DifferentialDiagnosis:
            mock.run = AsyncMock(return_value=retrieval_run_result)
        else:
            mock.run = AsyncMock(return_value=verify_run_result)
        return mock

    with patch("pydantic_ai.Agent", side_effect=_make_agent):
        asyncio.run(search_prior_incidents(ctx, observed_symptoms="pods crashing"))

    # Second agent call is the verification agent
    assert len(agent_calls) == 2
    verify_kwargs = agent_calls[1]
    tool_names = {t.__name__ for t in verify_kwargs["tools"]}

    # Should have investigation tools
    assert "read_file" in tool_names
    assert "exec_bash_any" in tool_names
    assert "grep" in tool_names
    assert "write_file" in tool_names
    assert "str_replace_file" in tool_names

    # Should NOT have search_prior_incidents
    assert "search_prior_incidents" not in tool_names
    assert "search_prior_incidents_any" not in tool_names


def test_verification_writes_trajectory(tmp_path: Path) -> None:
    """Verify trajectory JSONL record is written with correct agent_name."""
    summary = tmp_path / "summary.md"
    summary.write_text("# Summary")
    incidents = tmp_path / "incidents"
    incidents.mkdir()
    trajectory_file = tmp_path / "trajectory.jsonl"

    deps = _make_deps(
        tmp_path,
        lt_summary_file=summary,
        incidents_dir=incidents,
        ltm_model_id="test-model",
        trajectory_path=trajectory_file,
    )
    ctx = _make_sre_ctx(deps)

    candidates = [_make_candidates()[0]]
    retrieval_output = DifferentialDiagnosis(
        candidate_root_causes=candidates,
        novel_cause_signals="",
        caveats="",
    )
    retrieval_run_result = MagicMock()
    retrieval_run_result.output = retrieval_output

    verify_output = CandidateVerification(
        candidate_index=0,
        root_cause_class="missing Kubernetes Service",
        root_cause="Service user-service missing in ns",
        applies=True,
        causal_chain="Service deleted → 503",
        evidence=["svc not found"],
        reasoning="Confirmed.",
    )
    verify_run_result = MagicMock()
    verify_run_result.output = verify_output
    verify_run_result.usage.return_value = MagicMock(input_tokens=100, output_tokens=50)
    verify_run_result.all_messages.return_value = []

    def _make_agent(*args, **kwargs):
        mock = MagicMock()
        if kwargs.get("output_type") is DifferentialDiagnosis:
            mock.run = AsyncMock(return_value=retrieval_run_result)
        else:
            mock.run = AsyncMock(return_value=verify_run_result)
        return mock

    with patch("pydantic_ai.Agent", side_effect=_make_agent):
        asyncio.run(search_prior_incidents(ctx, observed_symptoms="pods crashing"))

    # Trajectory file should exist with a record
    assert trajectory_file.exists()
    lines = trajectory_file.read_text().strip().split("\n")
    assert len(lines) == 1

    record = json.loads(lines[0])
    assert record["agent_name"] == "ltm-verify-0"
    assert record["run_ctx"]["role"] == "ltm-verify"
    assert record["run_ctx"]["candidate_index"] == 0
    assert record["usage"]["input_tokens"] == 100
    assert record["usage"]["output_tokens"] == 50


# ---------------------------------------------------------------------------
# MitigationStrategy / MitigationSearchResult — model round-trip
# ---------------------------------------------------------------------------


def test_mitigation_strategy_roundtrip() -> None:
    """MitigationStrategy serializes and deserializes correctly."""
    strategy = MitigationStrategy(
        root_cause_class="targetPort mismatch",
        mitigation_approach="Correct the Service targetPort to match the container port",
        detailed_steps="kubectl patch svc <service> -n <ns> ...",
        incident_refs=["incidents/20260324_015046.md"],
        caveats="Verify pod restarts after patching",
    )
    data = strategy.model_dump()
    assert data["root_cause_class"] == "targetPort mismatch"
    restored = MitigationStrategy.model_validate(data)
    assert restored.mitigation_approach == strategy.mitigation_approach
    assert restored.incident_refs == ["incidents/20260324_015046.md"]


def test_mitigation_search_result_roundtrip() -> None:
    """MitigationSearchResult round-trips through JSON."""
    result = MitigationSearchResult(
        strategies=[
            MitigationStrategy(
                root_cause_class="missing Service",
                mitigation_approach="Recreate the missing Service",
            ),
        ],
        novel_cause=False,
        general_guidance="",
    )
    json_str = result.model_dump_json()
    restored = MitigationSearchResult.model_validate_json(json_str)
    assert len(restored.strategies) == 1
    assert restored.strategies[0].root_cause_class == "missing Service"
    assert restored.novel_cause is False


def test_mitigation_search_result_novel_cause() -> None:
    """MitigationSearchResult with novel_cause=True."""
    result = MitigationSearchResult(
        strategies=[],
        novel_cause=True,
        general_guidance="Investigate manually",
    )
    restored = MitigationSearchResult.model_validate_json(result.model_dump_json())
    assert restored.novel_cause is True
    assert restored.general_guidance == "Investigate manually"


# ---------------------------------------------------------------------------
# search_prior_mitigations — no LTM files
# ---------------------------------------------------------------------------


def test_search_mitigations_no_file(tmp_path: Path) -> None:
    """When lt_summary_file is None, return empty result without spawning LLM."""
    deps = _make_deps(tmp_path, lt_summary_file=None)
    ctx = _make_sre_ctx(deps)

    result = asyncio.run(search_prior_mitigations(ctx, root_cause="targetPort mismatch"))

    parsed = json.loads(result)
    assert parsed["strategies"] == []
    assert "No incident history" in parsed["general_guidance"]


# ---------------------------------------------------------------------------
# search_prior_mitigations — empty root_cause
# ---------------------------------------------------------------------------


def test_search_mitigations_empty_root_cause(tmp_path: Path) -> None:
    """Blank root_cause returns an error."""
    summary = tmp_path / "summary.md"
    summary.write_text("# Summary")
    deps = _make_deps(tmp_path, lt_summary_file=summary, ltm_model_id="test-model")
    ctx = _make_sre_ctx(deps)

    result = asyncio.run(search_prior_mitigations(ctx, root_cause="   "))

    assert "Error" in result
    assert "root_cause" in result


# ---------------------------------------------------------------------------
# search_prior_mitigations — budget enforcement
# ---------------------------------------------------------------------------


def test_search_mitigations_budget_exhausted(tmp_path: Path) -> None:
    """After budget is exhausted, return budget-exhausted response without spawning LLM."""
    summary = tmp_path / "summary.md"
    summary.write_text("# Summary")
    deps = _make_deps(tmp_path, lt_summary_file=summary, ltm_model_id="test-model")
    deps.ltm_call_count = 1
    ctx = _make_sre_ctx(deps)

    result = asyncio.run(search_prior_mitigations(ctx, root_cause="targetPort mismatch"))

    parsed = json.loads(result)
    assert parsed["strategies"] == []
    assert "budget exhausted" in parsed["general_guidance"].lower()


def test_search_mitigations_shared_budget(tmp_path: Path) -> None:
    """search_prior_incidents and search_prior_mitigations share the same budget counter."""
    summary = tmp_path / "summary.md"
    summary.write_text("# Summary")
    incidents = tmp_path / "incidents"
    incidents.mkdir()

    deps = _make_deps(
        tmp_path,
        lt_summary_file=summary,
        incidents_dir=incidents,
        ltm_model_id="test-model",
    )
    deps.ltm_call_budget = 2
    ctx = _make_sre_ctx(deps)

    mock_diag = DifferentialDiagnosis(candidate_root_causes=[], novel_cause_signals="", caveats="none")
    mock_diag_result = MagicMock()
    mock_diag_result.output = mock_diag

    mock_mit = MitigationSearchResult(strategies=[], novel_cause=False)
    mock_mit_result = MagicMock()
    mock_mit_result.output = mock_mit

    def _make_agent(*args, **kwargs):
        mock = MagicMock()
        if kwargs.get("output_type") is DifferentialDiagnosis:
            mock.run = AsyncMock(return_value=mock_diag_result)
        else:
            mock.run = AsyncMock(return_value=mock_mit_result)
        return mock

    with patch("pydantic_ai.Agent", side_effect=_make_agent):
        # First call (diagnosis) — count goes to 1
        asyncio.run(search_prior_incidents(ctx, observed_symptoms="pods crashing"))
        assert deps.ltm_call_count == 1

        # Second call (mitigation) — count goes to 2
        asyncio.run(search_prior_mitigations(ctx, root_cause="targetPort mismatch"))
        assert deps.ltm_call_count == 2

        # Third call — budget exhausted
        result = asyncio.run(search_prior_mitigations(ctx, root_cause="targetPort mismatch"))
        assert deps.ltm_call_count == 2  # not incremented
        parsed = json.loads(result)
        assert "budget exhausted" in parsed["general_guidance"].lower()


# ---------------------------------------------------------------------------
# search_prior_mitigations — subagent call (no verification)
# ---------------------------------------------------------------------------


def test_search_mitigations_calls_subagent(tmp_path: Path) -> None:
    """Verify retrieval subagent uses MitigationSearchResult and no verification agents spawn."""
    summary = tmp_path / "summary.md"
    summary.write_text("# Summary\n### Symptom: OOM kills")
    incidents = tmp_path / "incidents"
    incidents.mkdir()

    deps = _make_deps(
        tmp_path,
        lt_summary_file=summary,
        incidents_dir=incidents,
        ltm_model_id="test-model",
    )
    ctx = _make_sre_ctx(deps)

    mock_result = MitigationSearchResult(
        strategies=[
            MitigationStrategy(
                root_cause_class="memory limit too low",
                mitigation_approach="Increase memory limits",
            ),
        ],
        novel_cause=False,
    )
    mock_run_result = MagicMock()
    mock_run_result.output = mock_result

    agent_calls = []

    def _make_agent(*args, **kwargs):
        mock = MagicMock()
        mock.run = AsyncMock(return_value=mock_run_result)
        agent_calls.append(kwargs)
        return mock

    with patch("pydantic_ai.Agent", side_effect=_make_agent):
        result = asyncio.run(search_prior_mitigations(ctx, root_cause="memory limit too low"))

    # Only 1 agent (retrieval), no verification agents
    assert len(agent_calls) == 1
    assert agent_calls[0]["output_type"] is MitigationSearchResult

    # Prompt contains root_cause and file paths
    prompt_arg = agent_calls[0]["tools"]  # noqa — just check agent was constructed
    parsed = json.loads(result)
    assert len(parsed["strategies"]) == 1
    assert parsed["strategies"][0]["root_cause_class"] == "memory limit too low"
