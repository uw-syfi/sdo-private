"""Unit tests for the verify_hypothesis tool, HypothesisVerdict model, and the
submission gate that blocks diagnosis submissions when verify_hypothesis did not
return accept or accept_partial."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

if TYPE_CHECKING:
    from pathlib import Path

from pydantic_ai.models.test import TestModel

from sregym_agents.crucible._prompts import PromptRenderer
from sregym_agents.crucible.tools import (
    HypothesisVerdict,
    SharedFile,
    SREDeps,
    TriageAnomaly,
    TriageReport,
    verify_hypothesis,
)
from sregym_agents.crucible.tools._kb_tools import verify_hypothesis_impl


def _make_sre_ctx(deps: SREDeps):
    ctx = MagicMock()
    ctx.deps = deps
    return ctx


def _make_deps(
    tmp_path: Path,
    triage_report: TriageReport | None = None,
    model_id: TestModel | None = None,
    run_subagent: AsyncMock | None = None,
    stage: str = "diagnosis",
) -> SREDeps:
    shared_path = tmp_path / "session.md"
    shared_path.write_text("")
    return SREDeps(
        namespace="default",
        shared_file=SharedFile(shared_path),
        iteration=1,
        stage=stage,
        model_id=model_id if model_id is not None else TestModel(),
        triage_report=triage_report,
        run_subagent=run_subagent,
    )


def _make_triage_report() -> TriageReport:
    return TriageReport(
        anomalies=[
            TriageAnomaly(
                category="Non-Running Pods",
                resource_kind="Pod",
                resource_name="geo-abc-123",
                namespace="hotel-reservation",
                observation="CrashLoopBackOff, exit code 1",
            ),
            TriageAnomaly(
                category="Services Without Endpoints",
                resource_kind="Service",
                resource_name="geo",
                namespace="hotel-reservation",
                observation="0 ready endpoints",
            ),
        ],
    )


# ---------------------------------------------------------------------------
# HypothesisVerdict model
# ---------------------------------------------------------------------------


class TestHypothesisVerdictModel:
    def test_accept(self) -> None:
        v = HypothesisVerdict(
            verdict="accept",
            reasoning="every link verified against the live cluster",
            evidence=["kubectl get pod geo-abc-123 → Running"],
        )
        assert v.verdict == "accept"
        assert v.causal_chain_rejection == ""

    def test_reject_requires_causal_chain_rejection_content(self) -> None:
        v = HypothesisVerdict(
            verdict="reject",
            reasoning="chain breaks at step 2",
            causal_chain_rejection=(
                "step 2: claim that configmap key is missing — refuted by `kubectl get cm -o yaml`"
                " showing key is present"
            ),
            evidence=["kubectl get cm geo-config -o yaml | grep GeoMongoAddress → present"],
        )
        assert v.verdict == "reject"
        assert "step 2" in v.causal_chain_rejection

    def test_accept_partial(self) -> None:
        v = HypothesisVerdict(
            verdict="accept_partial",
            reasoning="scoped chain sound; unrelated endpoints issue is separate",
            unexplained_anomalies=["Service/other 0 endpoints"],
            residual_rationale="separate fault, hypothesis targets geo only",
            evidence=["kubectl describe pod geo-abc-123 → OOMKilled"],
        )
        assert v.verdict == "accept_partial"
        assert v.residual_rationale


# ---------------------------------------------------------------------------
# verify_hypothesis — error cases
# ---------------------------------------------------------------------------


class TestVerifyHypothesisErrors:
    def test_no_triage_report(self, tmp_path: Path) -> None:
        deps = _make_deps(tmp_path, triage_report=None, model_id=TestModel())
        ctx = _make_sre_ctx(deps)

        result = asyncio.run(
            verify_hypothesis(
                ctx,
                root_cause_description="port conflict in geo",
                causal_chain="port bound twice → pod crashes",
                root_cause_resources=["deployment/geo"],
            )
        )
        assert "Error" in result
        assert "triage" in result.lower()

    def test_error_path_does_not_set_verified(self, tmp_path: Path) -> None:
        triage = _make_triage_report()
        mock_run_subagent = AsyncMock(side_effect=RuntimeError("model unavailable"))
        deps = _make_deps(tmp_path, triage_report=triage, run_subagent=mock_run_subagent)
        ctx = _make_sre_ctx(deps)

        with patch.object(PromptRenderer, "render", return_value="rendered prompt"):
            result = asyncio.run(
                verify_hypothesis(
                    ctx,
                    root_cause_description="some hypothesis",
                    causal_chain="symptom → cause",
                    root_cause_resources=["pod/x"],
                )
            )

        assert "failed" in result.lower() or "error" in result.lower()
        assert deps.hypothesis_verified is False


# ---------------------------------------------------------------------------
# verify_hypothesis — subagent dispatch and state mutation
# ---------------------------------------------------------------------------


class TestVerifyHypothesisSubagent:
    def test_accept_sets_verified(self, tmp_path: Path) -> None:
        triage = _make_triage_report()
        verdict = HypothesisVerdict(
            verdict="accept",
            reasoning="all links verified",
            evidence=["kubectl describe pod geo-abc → OOMKilled"],
        )
        mock_run_subagent = AsyncMock(return_value=verdict)
        deps = _make_deps(tmp_path, triage_report=triage, run_subagent=mock_run_subagent)
        ctx = _make_sre_ctx(deps)

        with patch.object(PromptRenderer, "render", return_value="rendered prompt"):
            result = asyncio.run(
                verify_hypothesis(
                    ctx,
                    root_cause_description="memory limit too low on geo pod",
                    causal_chain="low memory → OOMKilled → CrashLoopBackOff",
                    root_cause_resources=["deployment/geo in hotel-reservation"],
                )
            )

        parsed = json.loads(result)
        assert parsed["verdict"] == "accept"
        assert deps.hypothesis_verified is True

    def test_accept_partial_sets_verified(self, tmp_path: Path) -> None:
        triage = _make_triage_report()
        verdict = HypothesisVerdict(
            verdict="accept_partial",
            reasoning="primary chain sound; residual noise",
            unexplained_anomalies=["Service/other 0 endpoints"],
            residual_rationale="separate fault, out of scope",
        )
        mock_run_subagent = AsyncMock(return_value=verdict)
        deps = _make_deps(tmp_path, triage_report=triage, run_subagent=mock_run_subagent)
        ctx = _make_sre_ctx(deps)

        with patch.object(PromptRenderer, "render", return_value="rendered prompt"):
            result = asyncio.run(
                verify_hypothesis(
                    ctx,
                    root_cause_description="scoped fault in geo",
                    causal_chain="X → Y → Z",
                    root_cause_resources=["pod/geo-abc"],
                )
            )

        parsed = json.loads(result)
        assert parsed["verdict"] == "accept_partial"
        assert deps.hypothesis_verified is True

    def test_reject_does_not_set_verified(self, tmp_path: Path) -> None:
        triage = _make_triage_report()
        verdict = HypothesisVerdict(
            verdict="reject",
            reasoning="link 2 contradicted by evidence",
            causal_chain_rejection=(
                "step 2: claim pod restart is due to OOM — refuted by `kubectl describe pod`"
                " showing exit code 1 (not 137)"
            ),
            evidence=["kubectl describe pod geo-abc → exit code 1"],
        )
        mock_run_subagent = AsyncMock(return_value=verdict)
        deps = _make_deps(tmp_path, triage_report=triage, run_subagent=mock_run_subagent)
        ctx = _make_sre_ctx(deps)

        with patch.object(PromptRenderer, "render", return_value="rendered prompt"):
            result = asyncio.run(
                verify_hypothesis(
                    ctx,
                    root_cause_description="OOM kill in geo",
                    causal_chain="mem limit low → OOM → CrashLoop",
                    root_cause_resources=["pod/geo-abc"],
                )
            )

        parsed = json.loads(result)
        assert parsed["verdict"] == "reject"
        assert "step 2" in parsed["causal_chain_rejection"]
        assert deps.hypothesis_verified is False

    def test_subagent_called_with_cluster_tools(self, tmp_path: Path) -> None:
        """The verifier subagent must have cluster-probing tools attached — this is what
        distinguishes it from the old pure-classifier coverage check."""
        triage = _make_triage_report()
        verdict = HypothesisVerdict(verdict="accept", reasoning="ok")
        mock_run_subagent = AsyncMock(return_value=verdict)
        deps = _make_deps(tmp_path, triage_report=triage, run_subagent=mock_run_subagent)

        with patch.object(PromptRenderer, "render", return_value="rendered prompt"):
            asyncio.run(
                verify_hypothesis_impl(
                    deps,
                    root_cause_description="x",
                    causal_chain="x→y",
                    root_cause_resources=["pod/x"],
                )
            )

        _, kwargs = mock_run_subagent.call_args
        passed_tools = kwargs["tools"]
        tool_names = {t.__name__ for t in passed_tools}
        assert "exec_bash_any" in tool_names
        assert "read_file" in tool_names
        assert "grep" in tool_names
        assert kwargs["agent_name"] == "hypothesis-verifier"


# ---------------------------------------------------------------------------
# Submission gate — MCP agent-cli path (submit_answer tool)
# ---------------------------------------------------------------------------


class TestSubmissionGateAgentCliPath:
    async def _setup_mcp(self, tmp_path: Path, stage: str = "diagnosis"):
        """Register crucible SRE tools on a FastMCP instance and return the registered
        submit_answer tool + deps + result path."""
        from fastmcp import FastMCP

        from sregym_agents.crucible.tools.mcp_server import register_sre_tools

        result_path = str(tmp_path / "result.json")
        deps = _make_deps(tmp_path, stage=stage)
        mcp = FastMCP("test")
        register_sre_tools(mcp, deps, result_file_path=result_path)
        tool = await mcp.get_tool("submit_answer")
        return deps, tool, result_path

    def test_submit_blocked_when_not_verified(self, tmp_path: Path) -> None:
        async def _run():
            deps, tool, result_path = await self._setup_mcp(tmp_path)
            assert deps.hypothesis_verified is False
            result = tool.fn(answer="my diagnosis", justification="my reasons", causal_chain="x→y")  # type: ignore[union-attr]
            return result, result_path

        result, result_path = asyncio.run(_run())
        assert "Submission blocked" in result
        assert "verify_hypothesis" in result
        import os

        assert not os.path.exists(result_path)

    def test_submit_allowed_when_verified(self, tmp_path: Path) -> None:
        async def _run():
            deps, tool, result_path = await self._setup_mcp(tmp_path)
            deps.hypothesis_verified = True
            result = tool.fn(answer="my diagnosis", justification="my reasons", causal_chain="x→y")  # type: ignore[union-attr]
            return result, result_path

        result, result_path = asyncio.run(_run())
        assert "successfully" in result.lower()
        import os

        assert os.path.exists(result_path)
        with open(result_path) as f:
            data = json.load(f)
        assert data["data"]["answer"] == "my diagnosis"

    def test_mitigation_stage_not_gated(self, tmp_path: Path) -> None:
        """Gate only applies on diagnosis. Mitigation submissions must pass through
        even when hypothesis_verified is False."""

        async def _run():
            deps, tool, result_path = await self._setup_mcp(tmp_path, stage="mitigation")
            assert deps.hypothesis_verified is False
            result = tool.fn(answer="applied fix", justification="rolled out", causal_chain="")  # type: ignore[union-attr]
            return result, result_path

        result, result_path = asyncio.run(_run())
        assert "successfully" in result.lower()
        import os

        assert os.path.exists(result_path)


# ---------------------------------------------------------------------------
# Submission gate — pydantic-ai path (sre_agent.py post-run check)
# ---------------------------------------------------------------------------


class TestSubmissionGatePydanticAIPath:
    def _make_agent_and_run(
        self,
        tmp_path: Path,
        *,
        hypothesis_verified_after_run: bool,
    ):
        """Build an SREAgent with a mock driver that returns a completed SRESubmission
        and flips deps.hypothesis_verified to the requested value during the run."""
        from sregym_agents.crucible.agents import SREAgent
        from sregym_agents.crucible.agents.base import AgentDriver, AgentResult
        from sregym_agents.crucible.tools import SRESubmission

        shared_path = tmp_path / "session.md"
        shared_path.write_text("")
        shared_file = SharedFile(shared_path)

        submission = SRESubmission(answer="diag", justification="reasons", causal_chain="x→y")

        class _MockDriver(AgentDriver):
            async def run(self, **kwargs):  # type: ignore[override]
                deps = kwargs.get("deps")
                if deps is not None:
                    deps.hypothesis_verified = hypothesis_verified_after_run
                return AgentResult(completed=True, output=submission)

        class _MockRenderer:
            def render(self, *args, **kwargs) -> str:
                return "rendered"

            def render_user(self, *args, **kwargs) -> str:
                return "user"

        agent = SREAgent(
            driver=_MockDriver(),
            model_id="test-model",
            renderer=_MockRenderer(),  # type: ignore[arg-type]
        )
        agent._render_prompts = MagicMock(return_value=("sys", "usr"))

        result = asyncio.run(
            agent.run(
                app_info={"namespace": "default"},
                stage="diagnosis",
                iteration=1,
                shared_file=shared_file,
                shared_content="",
            )
        )
        return result, shared_file, submission

    def test_submission_allowed_after_max_gate_rejections(self, tmp_path: Path) -> None:
        """When verify_hypothesis is never called, the gate rejects up to
        MAX_GATE_REJECTIONS times then lets the submission through."""
        result, shared_file, submission = self._make_agent_and_run(tmp_path, hypothesis_verified_after_run=False)
        # After MAX_GATE_REJECTIONS the gate allows submission for forward progress
        assert result.completed is True
        assert result.output == submission
        content = shared_file.read()
        assert "Submission blocked" in content
        assert "verify_hypothesis" in content

    def test_gate_resumes_agent_with_message_history(self, tmp_path: Path) -> None:
        """The gate should resume the agent (pass message_history) rather than
        restarting a fresh orchestrator iteration."""
        from sregym_agents.crucible.agents import SREAgent
        from sregym_agents.crucible.agents.base import AgentDriver, AgentResult
        from sregym_agents.crucible.tools import SRESubmission

        shared_path = tmp_path / "session.md"
        shared_path.write_text("")
        shared_file = SharedFile(shared_path)

        submission = SRESubmission(answer="diag", justification="reasons", causal_chain="x→y")
        fake_messages = [{"role": "assistant", "content": "hello"}]

        call_log: list[dict] = []

        class _TrackingDriver(AgentDriver):
            async def run(self, **kwargs):  # type: ignore[override]
                call_log.append(kwargs)
                deps = kwargs.get("deps")
                if deps is not None:
                    deps.hypothesis_verified = False
                return AgentResult(completed=True, output=submission, messages=fake_messages)

        class _MockRenderer:
            def render(self, *args, **kwargs) -> str:
                return "rendered"

            def render_user(self, *args, **kwargs) -> str:
                return "user"

        agent = SREAgent(
            driver=_TrackingDriver(),
            model_id="test-model",
            renderer=_MockRenderer(),  # type: ignore[arg-type]
        )
        agent._render_prompts = MagicMock(return_value=("sys", "usr"))

        asyncio.run(
            agent.run(
                app_info={"namespace": "default"},
                stage="diagnosis",
                iteration=1,
                shared_file=shared_file,
                shared_content="",
            )
        )

        # Driver called twice: initial + 1 rejection retry (2nd hits MAX and allows)
        assert len(call_log) == 2
        # First call has no message_history
        assert "message_history" not in call_log[0]
        # Second call resumes with message_history from the first run
        assert call_log[1]["message_history"] == fake_messages
        assert "Submission blocked" in call_log[1]["prompt"]

    def test_submission_accepted_if_verified(self, tmp_path: Path) -> None:
        result, shared_file, submission = self._make_agent_and_run(tmp_path, hypothesis_verified_after_run=True)
        assert result.completed is True
        assert result.output == submission
        content = shared_file.read()
        assert "Submission blocked" not in content
        assert "Agent Hypothesis" in content


# ---------------------------------------------------------------------------
# SubmissionGate unit tests
# ---------------------------------------------------------------------------


class TestSubmissionGate:
    def _make_result(self, *, completed: bool = True, has_output: bool = True):
        from sregym_agents.crucible.agents.base import AgentResult
        from sregym_agents.crucible.tools import SRESubmission

        output = SRESubmission(answer="diag", justification="j", causal_chain="c") if has_output else None
        return AgentResult(completed=completed, output=output)

    def test_passes_through_when_verified(self, tmp_path: Path) -> None:
        from sregym_agents.crucible.agents.sre_agent import SubmissionGate

        shared_path = tmp_path / "s.md"
        shared_path.write_text("")
        gate = SubmissionGate()
        result = self._make_result()
        reminder = gate.check(
            result,
            stage="diagnosis",
            hypothesis_verified=True,
            iteration=1,
            shared_file=SharedFile(shared_path),
        )
        assert reminder is None
        assert gate.rejections == 0

    def test_passes_through_for_mitigation(self, tmp_path: Path) -> None:
        from sregym_agents.crucible.agents.sre_agent import SubmissionGate

        shared_path = tmp_path / "s.md"
        shared_path.write_text("")
        gate = SubmissionGate()
        result = self._make_result()
        reminder = gate.check(
            result,
            stage="mitigation",
            hypothesis_verified=False,
            iteration=1,
            shared_file=SharedFile(shared_path),
        )
        assert reminder is None

    def test_rejects_then_allows_on_max(self, tmp_path: Path) -> None:
        from sregym_agents.crucible.agents.sre_agent import SubmissionGate

        shared_path = tmp_path / "s.md"
        shared_path.write_text("")
        sf = SharedFile(shared_path)
        gate = SubmissionGate(max_rejections=2)

        # First attempt — rejected
        r1 = gate.check(
            self._make_result(),
            stage="diagnosis",
            hypothesis_verified=False,
            iteration=1,
            shared_file=sf,
        )
        assert r1 is not None
        assert "Submission blocked" in r1
        assert gate.rejections == 1

        # Second attempt — hits max, allowed through
        r2 = gate.check(
            self._make_result(),
            stage="diagnosis",
            hypothesis_verified=False,
            iteration=1,
            shared_file=sf,
        )
        assert r2 is None
        assert gate.rejections == 2

    def test_passes_through_when_incomplete(self, tmp_path: Path) -> None:
        from sregym_agents.crucible.agents.sre_agent import SubmissionGate

        shared_path = tmp_path / "s.md"
        shared_path.write_text("")
        gate = SubmissionGate()
        result = self._make_result(completed=False)
        reminder = gate.check(
            result,
            stage="diagnosis",
            hypothesis_verified=False,
            iteration=1,
            shared_file=SharedFile(shared_path),
        )
        assert reminder is None
