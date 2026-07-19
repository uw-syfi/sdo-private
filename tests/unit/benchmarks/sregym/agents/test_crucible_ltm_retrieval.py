# pyright: reportPrivateUsage=false
"""Unit tests for the LTM retrieval subagent (search_prior_incidents)."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

if TYPE_CHECKING:
    from pathlib import Path

import pytest
from pydantic_ai.models.test import TestModel

from benchmarks.sregym.agents.crucible._prompts import PromptRenderer
from benchmarks.sregym.agents.crucible.config import CrucibleConfig
from benchmarks.sregym.agents.crucible.tools import (
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
from libs.pydantic_agent import UsageCollector


def _make_run_subagent_routing(responses: dict[str, Any]) -> AsyncMock:
    """Create a run_subagent mock that routes calls by agent_name.

    ``responses`` maps agent_name to the typed output value. If the value is
    a ``BaseException``, it is raised.
    """
    call_log: list[dict[str, Any]] = []

    async def _run_subagent(
        *,
        prompt: str = "",
        output_type: type | None = None,
        tools: list[Any] | None = None,
        agent_name: str = "",
        model_settings: dict[str, Any] | None = None,
        usage_collector: Any | None = None,
    ) -> Any:
        call_log.append(
            {
                "agent_name": agent_name,
                "output_type": output_type,
                "tools": tools,
                "prompt": prompt,
            }
        )
        result = responses[agent_name]
        if isinstance(result, BaseException):
            raise result
        return result

    mock = AsyncMock(side_effect=_run_subagent)
    mock._call_log = call_log  # type: ignore[attr-defined]
    return mock


def _make_sre_ctx(deps: SREDeps) -> MagicMock:
    ctx = MagicMock()
    ctx.deps = deps
    return ctx


def _make_deps(
    tmp_path: Path,
    lt_summary_file: Path | None = None,
    incidents_dir: Path | None = None,
    model_id: TestModel | None = None,
    run_subagent: AsyncMock | None = None,
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
        model_id=model_id if model_id is not None else TestModel(),
        run_subagent=run_subagent,
    )


# ---------------------------------------------------------------------------
# CrucibleConfig (behavior flags subset)
# ---------------------------------------------------------------------------


def test_crucible_config_defaults() -> None:
    cfg = CrucibleConfig()
    assert cfg.enable_judge is True
    assert cfg.enable_ltm_retrieval is False


def test_crucible_config_from_dict_like() -> None:
    raw = {"enable_judge": False, "enable_ltm_retrieval": True}
    cfg = CrucibleConfig(
        enable_judge=raw["enable_judge"],
        enable_ltm_retrieval=raw["enable_ltm_retrieval"],
    )
    assert cfg.enable_judge is False
    assert cfg.enable_ltm_retrieval is True


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
    deps = _make_deps(tmp_path, lt_summary_file=summary, model_id=TestModel())
    ctx = _make_sre_ctx(deps)

    result = asyncio.run(search_prior_incidents(ctx, observed_symptoms="   "))

    assert "Error" in result
    assert "observed_symptoms" in result


# ---------------------------------------------------------------------------
# search_prior_incidents — subagent is called correctly (0 candidates)
# ---------------------------------------------------------------------------


def test_search_calls_subagent(tmp_path: Path) -> None:
    """Retrieval subagent returns verified diagnosis structure."""
    summary = tmp_path / "summary.md"
    summary.write_text("# Summary\n### Symptom: OOM kills")
    incidents = tmp_path / "incidents"
    incidents.mkdir()

    diagnosis = DifferentialDiagnosis(candidate_root_causes=[], novel_cause_signals="", caveats="none")
    mock_run_subagent = AsyncMock(return_value=diagnosis)
    deps = _make_deps(
        tmp_path,
        lt_summary_file=summary,
        incidents_dir=incidents,
        run_subagent=mock_run_subagent,
    )
    ctx = _make_sre_ctx(deps)

    with patch.object(PromptRenderer, "render", return_value="rendered prompt"):
        result = asyncio.run(search_prior_incidents(ctx, observed_symptoms="pods are OOMKilled"))

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
    deps = _make_deps(tmp_path, lt_summary_file=summary, model_id=TestModel())
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

    diagnosis = DifferentialDiagnosis(candidate_root_causes=[], novel_cause_signals="", caveats="none")
    mock_run_subagent = AsyncMock(return_value=diagnosis)
    deps = _make_deps(
        tmp_path,
        lt_summary_file=summary,
        incidents_dir=incidents,
        run_subagent=mock_run_subagent,
    )
    deps.ltm_call_budget = 2  # use budget of 2 so we can test exhaustion after 2 calls
    ctx = _make_sre_ctx(deps)

    with patch.object(PromptRenderer, "render", return_value="rendered prompt"):
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


# ---------------------------------------------------------------------------
# Orchestrator flag wiring
# ---------------------------------------------------------------------------


@pytest.fixture
def shared_file(tmp_path: Path) -> SharedFile:
    f = tmp_path / "session.md"
    f.write_text("# Session\n")
    return SharedFile(f)


def test_flag_false_injects_summary(shared_file: SharedFile, tmp_path: Path) -> None:
    """When enable_ltm_retrieval=False, orchestrator passes lt_summary_content to prompt."""
    from benchmarks.sregym.agents.crucible.agents.base import AgentResult
    from benchmarks.sregym.agents.crucible.knowledge_base import InjectedKB
    from benchmarks.sregym.agents.crucible.orchestrator import _run_stage_loop
    from benchmarks.sregym.agents.crucible.tools import SharedState, SRESubmission

    lt_file = tmp_path / "summary.md"
    lt_file.write_text("Prior incident summary content")

    # Mock SRE agent that captures the call kwargs
    mock_sre = MagicMock()
    mock_sre._config = MagicMock()
    mock_sre._config.stage_outputs_file = None
    sre_call_kwargs: list[dict[str, Any]] = []

    async def fake_sre_run(**kwargs: Any):
        sre_call_kwargs.append(kwargs)
        state = SharedState()
        state.answer = "some answer"
        result = AgentResult(
            output=SRESubmission(answer="some answer", justification=""),
            completed=True,
        )
        result.state = state  # type: ignore[attr-defined]
        return result

    mock_sre.run = AsyncMock(side_effect=fake_sre_run)

    # Mock judge (unused — no-judge mode)
    mock_judge = MagicMock()

    mock_renderer = MagicMock(spec=PromptRenderer)
    mock_renderer.render.return_value = "rendered"

    with patch(
        "benchmarks.sregym.agents.crucible.orchestrator.submit_to_benchmark",
        new_callable=AsyncMock,
        return_value=(True, "ok", None),
    ):
        asyncio.run(
            _run_stage_loop(
                sre_agent=mock_sre,
                judge_agent=mock_judge,
                app_info={"app_name": "myapp", "namespace": "default"},
                stage="diagnosis",
                max_iters=1,
                shared_file=shared_file,
                submit_mcp_url="http://localhost:9954/submit/sse",
                injected_kb=InjectedKB(summary=lt_file),
                renderer=mock_renderer,
                usage_collector=UsageCollector(),
                crucible_config=CrucibleConfig(enable_judge=False, enable_ltm_retrieval=False),
            )
        )

    # SRE agent was called — check that lt_summary_content was passed
    assert len(sre_call_kwargs) == 1
    assert sre_call_kwargs[0]["lt_summary_content"] == "Prior incident summary content"


def test_flag_true_omits_summary(shared_file: SharedFile, tmp_path: Path) -> None:
    """When enable_ltm_retrieval=True, orchestrator passes empty lt_summary_content."""
    from benchmarks.sregym.agents.crucible.agents.base import AgentResult
    from benchmarks.sregym.agents.crucible.knowledge_base import InjectedKB
    from benchmarks.sregym.agents.crucible.orchestrator import _run_stage_loop
    from benchmarks.sregym.agents.crucible.tools import SharedState, SRESubmission

    lt_file = tmp_path / "summary.md"
    lt_file.write_text("Prior incident summary content")
    inc_dir = tmp_path / "incidents"
    inc_dir.mkdir()

    mock_sre = MagicMock()
    mock_sre._config = MagicMock()
    mock_sre._config.stage_outputs_file = None
    sre_call_kwargs: list[dict[str, Any]] = []

    async def fake_sre_run(**kwargs: Any):
        sre_call_kwargs.append(kwargs)
        state = SharedState()
        state.answer = "some answer"
        result = AgentResult(
            output=SRESubmission(answer="some answer", justification=""),
            completed=True,
        )
        result.state = state  # type: ignore[attr-defined]
        return result

    mock_sre.run = AsyncMock(side_effect=fake_sre_run)

    mock_judge = MagicMock()

    mock_renderer = MagicMock(spec=PromptRenderer)
    mock_renderer.render.return_value = "rendered"

    with patch(
        "benchmarks.sregym.agents.crucible.orchestrator.submit_to_benchmark",
        new_callable=AsyncMock,
        return_value=(True, "ok", None),
    ):
        asyncio.run(
            _run_stage_loop(
                sre_agent=mock_sre,
                judge_agent=mock_judge,
                app_info={"app_name": "myapp", "namespace": "default"},
                stage="diagnosis",
                max_iters=1,
                shared_file=shared_file,
                submit_mcp_url="http://localhost:9954/submit/sse",
                injected_kb=InjectedKB(summary=lt_file, incidents_dir=inc_dir),
                renderer=mock_renderer,
                usage_collector=UsageCollector(),
                crucible_config=CrucibleConfig(enable_judge=False, enable_ltm_retrieval=True),
            )
        )

    # The SRE agent should have received empty lt_summary_content
    assert len(sre_call_kwargs) == 1
    assert sre_call_kwargs[0]["lt_summary_content"] == ""


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

    candidates = _make_candidates()

    # Retrieval agent returns 2 candidates
    retrieval_output = DifferentialDiagnosis(
        candidate_root_causes=candidates,
        novel_cause_signals="Check network policies",
        caveats="Verify specifics",
    )

    # Verification agents return structured results
    verify_output_0 = CandidateVerification(
        candidate_index=0,
        root_cause_class="missing Kubernetes Service",
        root_cause="Service user-service missing in ns",
        applies=True,
        causal_chain="Service deleted \u2192 503",
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

    mock_run_subagent = _make_run_subagent_routing(
        {
            "ltm-search": retrieval_output,
            "ltm-verify-0": verify_output_0,
            "ltm-verify-1": verify_output_1,
        }
    )

    deps = _make_deps(
        tmp_path,
        lt_summary_file=summary,
        incidents_dir=incidents,
        run_subagent=mock_run_subagent,
    )
    ctx = _make_sre_ctx(deps)

    with patch.object(PromptRenderer, "render", return_value="rendered prompt"):
        result = asyncio.run(search_prior_incidents(ctx, observed_symptoms="pods crashing"))

    parsed = json.loads(result)

    # Should have 3 calls total: 1 retrieval + 2 verification
    assert mock_run_subagent.call_count == 3

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

    retrieval_output = DifferentialDiagnosis(
        candidate_root_causes=[],
        novel_cause_signals="novel signals",
        caveats="none",
    )

    mock_run_subagent = AsyncMock(return_value=retrieval_output)

    deps = _make_deps(
        tmp_path,
        lt_summary_file=summary,
        incidents_dir=incidents,
        run_subagent=mock_run_subagent,
    )
    ctx = _make_sre_ctx(deps)

    with patch.object(PromptRenderer, "render", return_value="rendered prompt"):
        result = asyncio.run(search_prior_incidents(ctx, observed_symptoms="pods crashing"))

    # Only 1 call (retrieval), no verification calls
    assert mock_run_subagent.call_count == 1

    parsed = json.loads(result)
    assert parsed["verified_candidates"] == []
    assert parsed["novel_cause_signals"] == "novel signals"


def test_search_verification_failure_graceful(tmp_path: Path) -> None:
    """When a verification subagent fails, the error is captured gracefully."""
    summary = tmp_path / "summary.md"
    summary.write_text("# Summary")
    incidents = tmp_path / "incidents"
    incidents.mkdir()

    candidates = _make_candidates()
    retrieval_output = DifferentialDiagnosis(
        candidate_root_causes=candidates,
        novel_cause_signals="",
        caveats="",
    )

    # Second verification agent will fail
    verify_output_0 = CandidateVerification(
        candidate_index=0,
        root_cause_class="missing Kubernetes Service",
        root_cause="Service user-service missing in ns",
        applies=True,
        causal_chain="Service deleted \u2192 503",
        evidence=["svc not found"],
        reasoning="Confirmed.",
    )

    mock_run_subagent = _make_run_subagent_routing(
        {
            "ltm-search": retrieval_output,
            "ltm-verify-0": verify_output_0,
            "ltm-verify-1": RuntimeError("LLM timeout"),
        }
    )

    deps = _make_deps(
        tmp_path,
        lt_summary_file=summary,
        incidents_dir=incidents,
        run_subagent=mock_run_subagent,
    )
    ctx = _make_sre_ctx(deps)

    with patch.object(PromptRenderer, "render", return_value="rendered prompt"):
        result = asyncio.run(search_prior_incidents(ctx, observed_symptoms="pods crashing"))

    parsed = json.loads(result)

    # Both candidates should be present in verified_candidates
    assert len(parsed["verified_candidates"]) == 2

    # One confirmed, one failed gracefully
    assert len(parsed["confirmed_candidates"]) == 1
    failed = [v for v in parsed["verified_candidates"] if not v["applies"]]
    assert len(failed) == 1
    assert "LLM timeout" in failed[0]["reasoning"]


def test_verification_subagent_tools_passed(tmp_path: Path) -> None:
    """Verification subagents receive investigation tools but NOT search_prior_incidents."""
    summary = tmp_path / "summary.md"
    summary.write_text("# Summary")
    incidents = tmp_path / "incidents"
    incidents.mkdir()

    candidates = [_make_candidates()[0]]
    retrieval_output = DifferentialDiagnosis(
        candidate_root_causes=candidates,
        novel_cause_signals="",
        caveats="",
    )

    verify_output = CandidateVerification(
        candidate_index=0,
        root_cause_class="missing Kubernetes Service",
        root_cause="Service user-service missing in ns",
        applies=False,
        reasoning="Not found.",
    )

    mock_run_subagent = _make_run_subagent_routing(
        {
            "ltm-search": retrieval_output,
            "ltm-verify-0": verify_output,
        }
    )

    deps = _make_deps(
        tmp_path,
        lt_summary_file=summary,
        incidents_dir=incidents,
        run_subagent=mock_run_subagent,
    )
    ctx = _make_sre_ctx(deps)

    with patch.object(PromptRenderer, "render", return_value="rendered prompt"):
        asyncio.run(search_prior_incidents(ctx, observed_symptoms="pods crashing"))

    # Check that verification call was made with tools
    call_log = mock_run_subagent._call_log  # type: ignore[attr-defined]
    verify_calls = [c for c in call_log if c["agent_name"] == "ltm-verify-0"]
    assert len(verify_calls) == 1
    tool_names = {t.__name__ for t in verify_calls[0]["tools"]}

    # Should have investigation tools
    assert "read_file" in tool_names
    assert "exec_bash_any" in tool_names
    assert "grep" in tool_names
    assert "write_file" in tool_names
    assert "str_replace_file" in tool_names

    # Should NOT have search_prior_incidents
    assert "search_prior_incidents" not in tool_names


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
    deps = _make_deps(tmp_path, lt_summary_file=summary, model_id=TestModel())
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
    deps = _make_deps(tmp_path, lt_summary_file=summary, model_id=TestModel())
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

    mock_diag = DifferentialDiagnosis(candidate_root_causes=[], novel_cause_signals="", caveats="none")
    mock_mit = MitigationSearchResult(strategies=[], novel_cause=False)

    mock_run_subagent = _make_run_subagent_routing(
        {
            "ltm-search": mock_diag,
            "ltm-mitigation": mock_mit,
        }
    )

    deps = _make_deps(
        tmp_path,
        lt_summary_file=summary,
        incidents_dir=incidents,
        run_subagent=mock_run_subagent,
    )
    deps.ltm_call_budget = 2
    ctx = _make_sre_ctx(deps)

    with patch.object(PromptRenderer, "render", return_value="rendered prompt"):
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
    """Retrieval subagent returns mitigation strategies."""
    summary = tmp_path / "summary.md"
    summary.write_text("# Summary\n### Symptom: OOM kills")
    incidents = tmp_path / "incidents"
    incidents.mkdir()

    mock_result = MitigationSearchResult(
        strategies=[
            MitigationStrategy(root_cause_class="memory limit too low", mitigation_approach="Increase memory limits")
        ],
        novel_cause=False,
    )
    mock_run_subagent = AsyncMock(return_value=mock_result)
    deps = _make_deps(
        tmp_path,
        lt_summary_file=summary,
        incidents_dir=incidents,
        run_subagent=mock_run_subagent,
    )
    ctx = _make_sre_ctx(deps)

    with patch.object(PromptRenderer, "render", return_value="rendered prompt"):
        result = asyncio.run(search_prior_mitigations(ctx, root_cause="memory limit too low"))

    parsed = json.loads(result)
    assert len(parsed["strategies"]) == 1
    assert parsed["strategies"][0]["root_cause_class"] == "memory limit too low"
