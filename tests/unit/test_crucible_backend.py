"""Unit tests for the Crucible backend abstractions (Phase 1).

Tests the new ``backend/`` package without modifying any existing code.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from sregym_agents.crucible.backend.base import (
    AgentDriver,
    AgentResult,
    RunSubagent,
    ShortCircuitSignal,
)

# ── AgentResult ──────────────────────────────────────────────────────────


class TestAgentResult:
    def test_defaults(self):
        r: AgentResult[str] = AgentResult()
        assert r.output is None
        assert r.completed is True
        assert r.interrupt_data is None
        assert r.messages == []

    def test_typed_output(self):
        r: AgentResult[int] = AgentResult(output=42)
        assert r.output == 42

    def test_interrupted(self):
        exc = RuntimeError("short-circuit")
        r = AgentResult(completed=False, interrupt_data=exc)
        assert not r.completed
        assert r.interrupt_data is exc

    def test_with_messages(self):
        msgs = [{"role": "user", "content": "hello"}]
        r = AgentResult(output="ok", messages=msgs)
        assert r.messages == msgs


# ── ShortCircuitSignal ───────────────────────────────────────────────────


class TestShortCircuitSignal:
    def test_diagnosis(self):
        sig = ShortCircuitSignal(
            confirmed=["root-cause-1"],
            iteration=2,
            confirmed_slugs=["slug-1"],
            stage="diagnosis",
        )
        assert sig.confirmed == ["root-cause-1"]
        assert sig.iteration == 2
        assert sig.confirmed_slugs == ["slug-1"]
        assert sig.stage == "diagnosis"

    def test_mitigation_defaults(self):
        sig = ShortCircuitSignal(confirmed=["fix-1"], iteration=1, stage="mitigation")
        assert sig.confirmed_slugs == []

    def test_frozen(self):
        sig = ShortCircuitSignal(confirmed=["a"], iteration=1)
        with pytest.raises(AttributeError):
            sig.iteration = 5  # type: ignore[misc]


# ── AgentDriver ABC ─────────────────────────────────────────────────────


class TestAgentDriverABC:
    def test_cannot_instantiate(self):
        with pytest.raises(TypeError):
            AgentDriver()  # type: ignore[abstract]

    @pytest.mark.asyncio
    async def test_concrete_subclass(self):
        class DummyDriver(AgentDriver):
            async def run(self, **kwargs: Any) -> AgentResult[Any]:
                return AgentResult(output="hello")

        driver = DummyDriver()
        result = await driver.run(prompt="test")
        assert result.output == "hello"


# ── RunSubagent Protocol ─────────────────────────────────────────────────


class TestRunSubagentProtocol:
    def test_callable_matches_protocol(self):
        async def my_subagent(
            *,
            prompt: str,
            output_type: type,
            tools: list | None = None,
            agent_name: str = "",
            model_settings: dict | None = None,
            usage_collector: Any | None = None,
        ) -> Any:
            return "result"

        assert isinstance(my_subagent, RunSubagent)


# ── PydanticAIDriver middleware selection ─────────────────────────────────


class TestMiddlewareSelection:
    def test_sre_middleware_heavy(self):
        from sregym_agents.crucible.backend.pydantic_ai_driver import _middleware_for_agent

        mw = _middleware_for_agent("sre-diagnosis")
        names = [type(m).__name__ for m in mw]
        assert "TurnLoggingMiddleware" in names
        assert "RetryMiddleware" in names
        assert "ThinkingRepetitionMiddleware" in names
        assert "LoopDetectionMiddleware" in names
        assert "StallDetectionMiddleware" in names
        assert "TimeoutMiddleware" in names
        assert "SoftLimitExtension" in names

    def test_recovery_middleware_heavy(self):
        from sregym_agents.crucible.backend.pydantic_ai_driver import _middleware_for_agent

        mw = _middleware_for_agent("recovery-diagnosis")
        names = [type(m).__name__ for m in mw]
        assert "ThinkingRepetitionMiddleware" in names
        assert "StallDetectionMiddleware" in names

    def test_judge_middleware_medium(self):
        from sregym_agents.crucible.backend.pydantic_ai_driver import _middleware_for_agent

        mw = _middleware_for_agent("judge-diagnosis")
        names = [type(m).__name__ for m in mw]
        assert "TurnLoggingMiddleware" in names
        assert "RetryMiddleware" in names
        assert "LoopDetectionMiddleware" in names
        assert "TimeoutMiddleware" in names
        assert "SoftLimitExtension" in names
        # Judge should NOT have these
        assert "ThinkingRepetitionMiddleware" not in names
        assert "StallDetectionMiddleware" not in names

    def test_subagent_middleware_light(self):
        from sregym_agents.crucible.backend.pydantic_ai_driver import _middleware_for_agent

        mw = _middleware_for_agent("triage-coordinator")
        names = [type(m).__name__ for m in mw]
        assert names == ["TurnLoggingMiddleware", "RetryMiddleware"]

    def test_trajectory_inserted_first_for_sre(self):
        from pathlib import Path

        from sregym_agents.crucible.backend.pydantic_ai_driver import _middleware_for_agent

        mw = _middleware_for_agent("sre-diagnosis", trajectory_path=Path("/tmp/traj.jsonl"))
        assert type(mw[0]).__name__ == "TrajectoryMiddleware"

    def test_trajectory_appended_for_subagent(self):
        from pathlib import Path

        from sregym_agents.crucible.backend.pydantic_ai_driver import _middleware_for_agent

        mw = _middleware_for_agent("triage-coordinator", trajectory_path=Path("/tmp/traj.jsonl"))
        assert type(mw[-1]).__name__ == "TrajectoryMiddleware"


# ── Context window detection ─────────────────────────────────────────────


class TestContextWindowDetection:
    def test_claude_model(self):
        from sregym_agents.crucible.backend.pydantic_ai_driver import _context_window_for

        assert _context_window_for("anthropic:claude-sonnet-4-6") == 200_000

    def test_gemini_model(self):
        from sregym_agents.crucible.backend.pydantic_ai_driver import _context_window_for

        assert _context_window_for("gemini-2.5-pro") == 1_000_000

    def test_unknown_model_default(self):
        from sregym_agents.crucible.backend.pydantic_ai_driver import _context_window_for

        assert _context_window_for("some-unknown-model") == 128_000


# ── RecoveryAgent._extract_benchmark_reasoning ───────────────────────────


class TestExtractBenchmarkReasoning:
    def test_extracts_diagnosis(self):
        from sregym_agents.crucible.backend.agents import RecoveryAgent

        block = (
            "<benchmark_result>\nsuccess: False\n"
            '<oracle>\n{"Diagnosis": {"reasoning": "the real cause"}}\n</oracle>\n'
            "</benchmark_result>\n"
        )
        assert RecoveryAgent._extract_benchmark_reasoning(block) == "the real cause"

    def test_extracts_mitigation(self):
        from sregym_agents.crucible.backend.agents import RecoveryAgent

        block = (
            "<benchmark_result>\nsuccess: False\n"
            '<oracle>\n{"Mitigation": {"reasoning": "apply the fix"}}\n</oracle>\n'
            "</benchmark_result>\n"
        )
        assert RecoveryAgent._extract_benchmark_reasoning(block, stage="mitigation") == "apply the fix"

    def test_empty_on_no_oracle(self):
        from sregym_agents.crucible.backend.agents import RecoveryAgent

        assert RecoveryAgent._extract_benchmark_reasoning("no oracle here") == ""

    def test_empty_on_invalid_json(self):
        from sregym_agents.crucible.backend.agents import RecoveryAgent

        block = "<oracle>\nnot json\n</oracle>"
        assert RecoveryAgent._extract_benchmark_reasoning(block) == ""


# ── Role agent construction ──────────────────────────────────────────────


class TestRoleAgentConstruction:
    """Verify that role agents can be constructed with a mock driver."""

    def _mock_driver(self) -> AgentDriver:
        class MockDriver(AgentDriver):
            async def run(self, **kwargs: Any) -> AgentResult[Any]:
                return AgentResult(output=None, completed=False)

        return MockDriver()

    def _mock_renderer(self) -> Any:
        renderer = MagicMock()
        renderer.render.return_value = "mock prompt"
        return renderer

    def test_sre_agent_construction(self):
        from sregym_agents.crucible.backend.agents import SREAgent, SREAgentConfig

        agent = SREAgent(
            driver=self._mock_driver(),
            model_id="test-model",
            renderer=self._mock_renderer(),
            config=SREAgentConfig(),
        )
        assert agent._driver is not None
        assert agent._model_id == "test-model"

    def test_sre_agent_tool_assembly_diagnosis(self):
        from sregym_agents.crucible.backend.agents import SREAgent

        agent = SREAgent(
            driver=self._mock_driver(),
            model_id="test-model",
            renderer=self._mock_renderer(),
        )
        tools = agent._assemble_tools("diagnosis")
        tool_names = [t.__name__ if hasattr(t, "__name__") else str(t) for t in tools]
        assert "exec_bash" in tool_names
        assert "read_file" in tool_names
        assert "triage_cluster" in tool_names
        assert "search_prior_incidents" in tool_names
        assert "check_hypothesis_coverage" in tool_names
        assert "search_prior_mitigations" not in tool_names

    def test_sre_agent_tool_assembly_mitigation(self):
        from sregym_agents.crucible.backend.agents import SREAgent

        agent = SREAgent(
            driver=self._mock_driver(),
            model_id="test-model",
            renderer=self._mock_renderer(),
        )
        tools = agent._assemble_tools("mitigation")
        tool_names = [t.__name__ if hasattr(t, "__name__") else str(t) for t in tools]
        assert "exec_bash" in tool_names
        assert "search_prior_mitigations" in tool_names
        assert "triage_cluster" not in tool_names
        assert "search_prior_incidents" not in tool_names
        assert "check_hypothesis_coverage" not in tool_names

    def test_judge_agent_construction(self):
        from sregym_agents.crucible.backend.agents import JudgeAgent

        agent = JudgeAgent(
            driver=self._mock_driver(),
            model_id="test-model",
            renderer=self._mock_renderer(),
        )
        assert agent.MAX_SUBMIT_REMINDERS == 3

    def test_judge_agent_tool_assembly(self):
        from sregym_agents.crucible.backend.agents import JudgeAgent

        agent = JudgeAgent(
            driver=self._mock_driver(),
            model_id="test-model",
            renderer=self._mock_renderer(),
        )
        tools = agent._assemble_tools()
        tool_names = [t.__name__ if hasattr(t, "__name__") else str(t) for t in tools]
        assert "exec_bash_any" in tool_names
        assert "submit_verdict" in tool_names
        assert "submit_independent_findings" in tool_names
        assert "reveal_agent_hypothesis" in tool_names

    def test_recovery_agent_construction(self):
        from sregym_agents.crucible.backend.agents import RecoveryAgent

        agent = RecoveryAgent(
            driver=self._mock_driver(),
            model_id="test-model",
            renderer=self._mock_renderer(),
        )
        assert agent._driver is not None


# ── Package re-exports ───────────────────────────────────────────────────


class TestPackageExports:
    def test_all_exports_importable(self):
        from sregym_agents.crucible import backend

        assert hasattr(backend, "AgentDriver")
        assert hasattr(backend, "AgentResult")
        assert hasattr(backend, "RunSubagent")
        assert hasattr(backend, "ShortCircuitSignal")
        assert hasattr(backend, "PydanticAIDriver")
        assert hasattr(backend, "SREAgent")
        assert hasattr(backend, "JudgeAgent")
        assert hasattr(backend, "RecoveryAgent")
        assert hasattr(backend, "SREAgentConfig")
