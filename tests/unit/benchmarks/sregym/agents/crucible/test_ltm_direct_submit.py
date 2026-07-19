"""Tests for LTM verified-hypothesis direct submission.

When ``CrucibleConfig.enable_ltm_verified_direct_submit`` is on and
``search_prior_incidents`` produces at least one confirmed candidate, the
SRE main agent is short-circuited via ``LTMShortCircuit`` and the
orchestrator submits the confirmed candidates directly to the benchmark
(bypassing the judge).

Independently — and unconditionally — every ``search_prior_incidents``
call appends a markdown summary of the investigated hypotheses to the
shared session file.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from benchmarks.sregym.agents.crucible._prompts import PromptRenderer
from benchmarks.sregym.agents.crucible.config import CrucibleConfig, crucible_config_from_experiment_agent
from benchmarks.sregym.agents.crucible.tools import (
    LTMShortCircuit,
    SharedFile,
    SharedState,
    SREDeps,
)
from benchmarks.sregym.agents.crucible.tools._judge_tools import MAX_DIAGNOSIS_CANDIDATES
from benchmarks.sregym.agents.crucible.tools._kb_tools import (
    CandidateRootCause,
    CandidateVerification,
    DifferentialDiagnosis,
    VerifiedDifferentialDiagnosis,
    _format_investigated_hypotheses_md,
    search_prior_incidents,
)
from libs.pydantic_agent import UsageCollector

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _candidate_rc(name: str, slug: str | None = None) -> CandidateRootCause:
    return CandidateRootCause(
        root_cause_class=f"class-{name}",
        root_cause=f"pattern-{name}",
        distinguishing_check=f"check-{name}",
        mitigation_hint=f"mit-{name}",
        slug=slug,
    )


def _verification(name: str, applies: bool, idx: int = 0) -> CandidateVerification:
    return CandidateVerification(
        candidate_index=idx,
        root_cause_class=f"class-{name}",
        root_cause=f"pattern-{name}",
        applies=applies,
        causal_chain=f"chain-{name}" if applies else "",
        evidence=[f"evidence-{name}"] if applies else [],
        reasoning=f"reasoning-{name}",
    )


def _make_sre_deps(
    tmp_path: Path,
    *,
    enable_ltm_verified_direct_submit: bool = False,
    iteration: int = 1,
) -> SREDeps:
    from benchmarks.sregym.agents.crucible.config import CrucibleConfig

    shared_path = tmp_path / "shared.md"
    shared_path.write_text("# Session\n")
    return SREDeps(
        namespace="ns",
        shared_file=SharedFile(shared_path),
        iteration=iteration,
        stage="diagnosis",
        renderer=PromptRenderer("v3"),
        state=SharedState(),
        config=CrucibleConfig(enable_ltm_verified_direct_submit=enable_ltm_verified_direct_submit),
        lt_summary_file=tmp_path / "lt_summary.md",
        model_id="test",  # type: ignore[arg-type]
        ltm_call_budget=1,
        usage_collector=UsageCollector(),
    )


def _make_ctx(deps: SREDeps) -> MagicMock:
    ctx = MagicMock()
    ctx.deps = deps
    return ctx


# ---------------------------------------------------------------------------
# _format_investigated_hypotheses_md
# ---------------------------------------------------------------------------


class TestFormatInvestigatedHypotheses:
    def test_empty_verified_candidates(self):
        verified = VerifiedDifferentialDiagnosis(
            verified_candidates=[],
            confirmed_candidates=[],
            novel_cause_signals="",
        )
        out = _format_investigated_hypotheses_md(verified, iteration=2, stage="diagnosis")
        assert "Iteration 2" in out
        assert "LTM Investigated Hypotheses" in out
        assert "diagnosis" in out
        assert "No candidates" in out

    def test_mixed_confirmed_and_ruled_out(self):
        confirmed = _verification("hit", applies=True, idx=0)
        ruled_out = _verification("miss", applies=False, idx=1)
        verified = VerifiedDifferentialDiagnosis(
            verified_candidates=[confirmed, ruled_out],
            confirmed_candidates=[confirmed],
            novel_cause_signals="",
        )
        out = _format_investigated_hypotheses_md(verified, iteration=1, stage="diagnosis")
        assert "Verified 2 candidate(s)" in out
        assert "1 confirmed" in out
        assert "CONFIRMED" in out
        assert "ruled out" in out
        assert "class-hit" in out
        assert "pattern-hit" in out
        assert "class-miss" in out
        assert "reasoning-hit" in out
        assert "reasoning-miss" in out
        # Causal chain only shown for confirmed
        assert "chain-hit" in out
        assert "chain-miss" not in out

    def test_renders_iteration_and_stage(self):
        v = VerifiedDifferentialDiagnosis(
            verified_candidates=[_verification("only", applies=True)],
            confirmed_candidates=[_verification("only", applies=True)],
            novel_cause_signals="",
        )
        out = _format_investigated_hypotheses_md(v, iteration=7, stage="diagnosis")
        assert "Iteration 7" in out
        assert "diagnosis" in out


# ---------------------------------------------------------------------------
# search_prior_incidents — logging + short-circuit
# ---------------------------------------------------------------------------


def _set_run_subagent_for_retrieval(deps: SREDeps, diagnosis: DifferentialDiagnosis):
    """Set deps.run_subagent to return the given diagnosis directly."""
    deps.run_subagent = AsyncMock(return_value=diagnosis)


def _patch_verification(verified: VerifiedDifferentialDiagnosis):
    return patch(
        "benchmarks.sregym.agents.crucible.tools._kb_tools._run_verification_phase",
        AsyncMock(return_value=verified),
    )


class TestSearchPriorIncidentsLogging:
    @pytest.mark.asyncio
    async def test_logs_investigated_hypotheses_when_flag_off(self, tmp_path: Path):
        deps = _make_sre_deps(tmp_path, enable_ltm_verified_direct_submit=False)
        ctx = _make_ctx(deps)

        diag = DifferentialDiagnosis(
            candidate_root_causes=[_candidate_rc("hit"), _candidate_rc("miss")],
            novel_cause_signals="",
            caveats="",
        )
        verified = VerifiedDifferentialDiagnosis(
            verified_candidates=[
                _verification("hit", applies=True, idx=0),
                _verification("miss", applies=False, idx=1),
            ],
            confirmed_candidates=[_verification("hit", applies=True, idx=0)],
            novel_cause_signals="",
        )

        _set_run_subagent_for_retrieval(deps, diag)
        with _patch_verification(verified):
            result = await search_prior_incidents(ctx, "symptom A")

        # Returned the JSON serialization (not raised)
        assert "verified_candidates" in result
        # Shared file gained the investigated-hypotheses block
        shared_text = (tmp_path / "shared.md").read_text()
        assert "LTM Investigated Hypotheses" in shared_text
        assert "class-hit" in shared_text
        assert "class-miss" in shared_text
        assert "CONFIRMED" in shared_text
        assert "ruled out" in shared_text

    @pytest.mark.asyncio
    async def test_short_circuits_when_flag_on_and_confirmed_present(self, tmp_path: Path):
        deps = _make_sre_deps(tmp_path, enable_ltm_verified_direct_submit=True, iteration=3)
        ctx = _make_ctx(deps)

        diag = DifferentialDiagnosis(
            candidate_root_causes=[_candidate_rc("hit"), _candidate_rc("miss")],
            novel_cause_signals="",
            caveats="",
        )
        verified = VerifiedDifferentialDiagnosis(
            verified_candidates=[
                _verification("hit", applies=True, idx=0),
                _verification("miss", applies=False, idx=1),
            ],
            confirmed_candidates=[_verification("hit", applies=True, idx=0)],
            novel_cause_signals="",
        )

        _set_run_subagent_for_retrieval(deps, diag)
        with _patch_verification(verified):
            with pytest.raises(LTMShortCircuit) as excinfo:
                await search_prior_incidents(ctx, "symptom A")

        sig = excinfo.value
        assert sig.confirmed == ["pattern-hit"]
        assert sig.iteration == 3

        # Investigated-hypotheses block was appended *before* the raise
        shared_text = (tmp_path / "shared.md").read_text()
        assert "LTM Investigated Hypotheses" in shared_text
        assert "CONFIRMED" in shared_text
        assert "class-hit" in shared_text

    @pytest.mark.asyncio
    async def test_no_short_circuit_when_flag_on_but_no_confirmed(self, tmp_path: Path):
        deps = _make_sre_deps(tmp_path, enable_ltm_verified_direct_submit=True)
        ctx = _make_ctx(deps)

        diag = DifferentialDiagnosis(
            candidate_root_causes=[_candidate_rc("miss")],
            novel_cause_signals="",
            caveats="",
        )
        verified = VerifiedDifferentialDiagnosis(
            verified_candidates=[_verification("miss", applies=False, idx=0)],
            confirmed_candidates=[],
            novel_cause_signals="",
        )

        _set_run_subagent_for_retrieval(deps, diag)
        with _patch_verification(verified):
            result = await search_prior_incidents(ctx, "symptom A")

        assert "verified_candidates" in result
        shared_text = (tmp_path / "shared.md").read_text()
        assert "LTM Investigated Hypotheses" in shared_text
        assert "ruled out" in shared_text

    @pytest.mark.asyncio
    async def test_logs_when_no_retrieval_candidates(self, tmp_path: Path):
        """Even the early-return path (zero retrieval candidates) should log."""
        deps = _make_sre_deps(tmp_path, enable_ltm_verified_direct_submit=True)
        ctx = _make_ctx(deps)

        diag = DifferentialDiagnosis(
            candidate_root_causes=[],
            novel_cause_signals="signal A",
            caveats="",
        )

        _set_run_subagent_for_retrieval(deps, diag)
        result = await search_prior_incidents(ctx, "symptom A")

        assert "verified_candidates" in result
        shared_text = (tmp_path / "shared.md").read_text()
        assert "LTM Investigated Hypotheses" in shared_text
        assert "No candidates" in shared_text

    @pytest.mark.asyncio
    async def test_short_circuit_caps_candidates_at_max(self, tmp_path: Path):
        deps = _make_sre_deps(tmp_path, enable_ltm_verified_direct_submit=True)
        ctx = _make_ctx(deps)

        diag = DifferentialDiagnosis(
            candidate_root_causes=[_candidate_rc(f"c{i}") for i in range(7)],
            novel_cause_signals="",
            caveats="",
        )
        confirmed_seven = [_verification(f"c{i}", applies=True, idx=i) for i in range(7)]
        verified = VerifiedDifferentialDiagnosis(
            verified_candidates=confirmed_seven,
            confirmed_candidates=confirmed_seven,
            novel_cause_signals="",
        )

        _set_run_subagent_for_retrieval(deps, diag)
        with _patch_verification(verified):
            with pytest.raises(LTMShortCircuit) as excinfo:
                await search_prior_incidents(ctx, "symptom A")

        sig = excinfo.value
        assert len(sig.confirmed) == MAX_DIAGNOSIS_CANDIDATES
        assert sig.confirmed == [f"pattern-c{i}" for i in range(MAX_DIAGNOSIS_CANDIDATES)]


# ---------------------------------------------------------------------------
# CrucibleConfig parsing
# ---------------------------------------------------------------------------


class TestCrucibleConfigFlagParsing:
    def test_default_is_false(self):
        cfg = crucible_config_from_experiment_agent({"prompt_version": "v3"})
        assert cfg.enable_ltm_verified_direct_submit is False

    def test_explicit_true(self):
        cfg = crucible_config_from_experiment_agent({"prompt_version": "v3", "enable_ltm_verified_direct_submit": True})
        assert cfg.enable_ltm_verified_direct_submit is True

    def test_dataclass_default(self):
        assert CrucibleConfig().enable_ltm_verified_direct_submit is False


# ---------------------------------------------------------------------------
# _run_stage_loop short-circuit catch
# ---------------------------------------------------------------------------


class TestRunStageLoopShortCircuit:
    def test_short_circuit_bypasses_judge_and_submits_list(self, tmp_path: Path):
        import asyncio

        from benchmarks.sregym.agents.crucible.agents.base import AgentResult
        from benchmarks.sregym.agents.crucible.orchestrator import _run_stage_loop

        shared_path = tmp_path / "session.md"
        shared_path.write_text("# Session\n")
        shared = SharedFile(shared_path)

        # Mock SRE agent: run raises LTMShortCircuit via interrupt_data
        mock_sre = MagicMock()
        mock_sre._config = MagicMock()
        mock_sre._config.stage_outputs_file = None

        async def fake_sre_run(**kwargs):
            sc = LTMShortCircuit(confirmed=["cand A", "cand B"], iteration=1)
            result = AgentResult(output=None, completed=False, interrupt_data=sc)
            result.state = SharedState()  # type: ignore[attr-defined]
            return result

        mock_sre.run = AsyncMock(side_effect=fake_sre_run)

        # Mock judge agent — should never be called
        mock_judge = MagicMock()
        mock_judge.run = AsyncMock()

        mock_renderer = MagicMock(spec=PromptRenderer)
        mock_renderer.render.return_value = "rendered"

        with patch(
            "benchmarks.sregym.agents.crucible.orchestrator.submit_to_benchmark",
            new_callable=AsyncMock,
            return_value=(True, "ok", {"Diagnosis": {"success": True}}),
        ) as mock_submit:
            result = asyncio.run(
                _run_stage_loop(
                    sre_agent=mock_sre,
                    judge_agent=mock_judge,
                    app_info={"app_name": "myapp", "namespace": "default"},
                    stage="diagnosis",
                    max_iters=3,
                    shared_file=shared,
                    submit_mcp_url="http://localhost:9954/submit/sse",
                    renderer=mock_renderer,
                    usage_collector=UsageCollector(),
                    crucible_config=CrucibleConfig(
                        enable_judge=True,
                        prompt_version="v3",
                        enable_ltm_verified_direct_submit=True,
                    ),
                )
            )

        assert result.approved is True
        assert result.agent_answer == "cand A"
        # Judge was never called
        mock_judge.run.assert_not_called()
        # Benchmark was called with the list
        mock_submit.assert_awaited_once()
        args = mock_submit.call_args.args
        assert args[1] == ["cand A", "cand B"]
        assert args[2] == "diagnosis"
        # Shared file shows the direct-submission and approval entries
        shared_text = shared_path.read_text()
        assert "LTM Direct Submission" in shared_text
        assert "Confirmed candidates" in shared_text
        assert "1. cand A" in shared_text
        assert "2. cand B" in shared_text
        assert "APPROVED" in shared_text
        assert "LTM verified short-circuit" in shared_text

    def test_flag_off_does_not_short_circuit(self, tmp_path: Path):
        """When the flag is off, the SRE agent runs normally and the judge is invoked."""
        import asyncio

        from benchmarks.sregym.agents.crucible.agents.base import AgentResult
        from benchmarks.sregym.agents.crucible.orchestrator import _run_stage_loop
        from benchmarks.sregym.agents.crucible.tools import SRESubmission

        shared_path = tmp_path / "session.md"
        shared_path.write_text("# Session\n")
        shared = SharedFile(shared_path)

        # Mock SRE agent
        mock_sre = MagicMock()
        mock_sre._config = MagicMock()
        mock_sre._config.stage_outputs_file = None

        async def fake_sre_run(**kwargs):
            state = SharedState()
            state.answer = "free-form diagnosis"
            state.answer_justification = "saw X"
            state.answer_causal_chain = "X \u2192 Y"
            result = AgentResult(
                output=SRESubmission(answer="free-form diagnosis", justification="saw X", causal_chain="X \u2192 Y"),
                completed=True,
            )
            result.state = state  # type: ignore[attr-defined]
            return result

        mock_sre.run = AsyncMock(side_effect=fake_sre_run)

        # Mock judge agent
        judge_calls = []

        async def fake_judge_run(**kwargs):
            judge_calls.append(True)
            state = SharedState()
            state.verdict = "APPROVED"
            state.submitted = True
            state.benchmark_block = "<benchmark_result>fake</benchmark_result>"
            result = AgentResult(output="approved", completed=True)
            result.state = state  # type: ignore[attr-defined]
            return result

        mock_judge = MagicMock()
        mock_judge.run = AsyncMock(side_effect=fake_judge_run)

        mock_renderer = MagicMock(spec=PromptRenderer)
        mock_renderer.render.return_value = "rendered"

        with patch(
            "benchmarks.sregym.agents.crucible.orchestrator.submit_to_benchmark",
            new_callable=AsyncMock,
            return_value=(True, "ok", None),
        ):
            result = asyncio.run(
                _run_stage_loop(
                    sre_agent=mock_sre,
                    judge_agent=mock_judge,
                    app_info={"app_name": "myapp", "namespace": "default"},
                    stage="diagnosis",
                    max_iters=3,
                    shared_file=shared,
                    submit_mcp_url="http://localhost:9954/submit/sse",
                    renderer=mock_renderer,
                    usage_collector=UsageCollector(),
                    crucible_config=CrucibleConfig(
                        enable_judge=True,
                        prompt_version="v3",
                        enable_ltm_verified_direct_submit=False,
                    ),
                )
            )

        assert result.approved is True
        assert result.agent_answer == "free-form diagnosis"
        # Judge was called
        assert len(judge_calls) == 1
        # No LTM direct submission block in shared file
        shared_text = shared_path.read_text()
        assert "LTM Direct Submission" not in shared_text
