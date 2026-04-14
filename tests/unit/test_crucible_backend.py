"""Unit tests for the Crucible backend abstractions (Phase 1).

Tests the new ``backend/`` package without modifying any existing code.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from sregym_agents.crucible.agents.base import (
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
        assert r.state is None

    def test_state_field(self):
        sentinel = {"key": "value"}
        r = AgentResult(output="ok", state=sentinel)
        assert r.state is sentinel

    def test_state_default_none(self):
        r = AgentResult(output="ok")
        assert r.state is None

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

    def test_unwrap_success(self):
        r = AgentResult(output="hello", completed=True)
        assert r.unwrap() == "hello"

    def test_unwrap_with_agent_name(self):
        r = AgentResult(output=42, completed=True)
        assert r.unwrap("my-agent") == 42

    def test_unwrap_not_completed(self):
        r = AgentResult(completed=False)
        with pytest.raises(RuntimeError, match="did not produce output"):
            r.unwrap()

    def test_unwrap_no_output(self):
        r: AgentResult[str] = AgentResult(completed=True, output=None)
        with pytest.raises(RuntimeError, match="did not produce output"):
            r.unwrap()

    def test_unwrap_error_includes_agent_name(self):
        r = AgentResult(completed=False)
        with pytest.raises(RuntimeError, match="my-agent"):
            r.unwrap("my-agent")


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
        from sregym_agents.crucible.agents.drivers.pydantic_ai_driver import _middleware_for_agent

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
        from sregym_agents.crucible.agents.drivers.pydantic_ai_driver import _middleware_for_agent

        mw = _middleware_for_agent("recovery-diagnosis")
        names = [type(m).__name__ for m in mw]
        assert "ThinkingRepetitionMiddleware" in names
        assert "StallDetectionMiddleware" in names

    def test_judge_middleware_medium(self):
        from sregym_agents.crucible.agents.drivers.pydantic_ai_driver import _middleware_for_agent

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

    def test_mitigation_middleware_includes_search_reminder(self):
        from sregym_agents.crucible.agents.drivers.pydantic_ai_driver import _middleware_for_agent

        mw = _middleware_for_agent("sre-mitigation")
        names = [type(m).__name__ for m in mw]
        assert "SearchPriorMitigationsReminderMiddleware" in names

    def test_subagent_middleware_light(self):
        from sregym_agents.crucible.agents.drivers.pydantic_ai_driver import _middleware_for_agent

        mw = _middleware_for_agent("triage-coordinator")
        names = [type(m).__name__ for m in mw]
        assert names == ["TurnLoggingMiddleware", "RetryMiddleware"]

    def test_trajectory_inserted_first_for_sre(self):
        from pathlib import Path

        from sregym_agents.crucible.agents.drivers.pydantic_ai_driver import _middleware_for_agent

        mw = _middleware_for_agent("sre-diagnosis", trajectory_path=Path("/tmp/traj.jsonl"))
        assert type(mw[0]).__name__ == "TrajectoryMiddleware"

    def test_trajectory_appended_for_subagent(self):
        from pathlib import Path

        from sregym_agents.crucible.agents.drivers.pydantic_ai_driver import _middleware_for_agent

        mw = _middleware_for_agent("triage-coordinator", trajectory_path=Path("/tmp/traj.jsonl"))
        assert type(mw[-1]).__name__ == "TrajectoryMiddleware"


# ── Context window detection ─────────────────────────────────────────────


class TestContextWindowDetection:
    def test_claude_model(self):
        from sregym_agents.crucible.agents.drivers.pydantic_ai_driver import _context_window_for

        assert _context_window_for("anthropic:claude-sonnet-4-6") == 200_000

    def test_gemini_model(self):
        from sregym_agents.crucible.agents.drivers.pydantic_ai_driver import _context_window_for

        assert _context_window_for("gemini-2.5-pro") == 1_000_000

    def test_unknown_model_default(self):
        from sregym_agents.crucible.agents.drivers.pydantic_ai_driver import _context_window_for

        assert _context_window_for("some-unknown-model") == 128_000


# ── RecoveryAgent._extract_benchmark_reasoning ───────────────────────────


class TestExtractBenchmarkReasoning:
    def test_extracts_diagnosis(self):
        from sregym_agents.crucible.agents import RecoveryAgent

        block = (
            "<benchmark_result>\nsuccess: False\n"
            '<oracle>\n{"Diagnosis": {"reasoning": "the real cause"}}\n</oracle>\n'
            "</benchmark_result>\n"
        )
        assert RecoveryAgent._extract_benchmark_reasoning(block) == "the real cause"

    def test_extracts_mitigation(self):
        from sregym_agents.crucible.agents import RecoveryAgent

        block = (
            "<benchmark_result>\nsuccess: False\n"
            '<oracle>\n{"Mitigation": {"reasoning": "apply the fix"}}\n</oracle>\n'
            "</benchmark_result>\n"
        )
        assert RecoveryAgent._extract_benchmark_reasoning(block, stage="mitigation") == "apply the fix"

    def test_empty_on_no_oracle(self):
        from sregym_agents.crucible.agents import RecoveryAgent

        assert RecoveryAgent._extract_benchmark_reasoning("no oracle here") == ""

    def test_empty_on_invalid_json(self):
        from sregym_agents.crucible.agents import RecoveryAgent

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
        from sregym_agents.crucible.agents import SREAgent, SREAgentConfig
        from sregym_agents.crucible.config import CrucibleConfig

        agent = SREAgent(
            driver=self._mock_driver(),
            model_id="test-model",
            renderer=self._mock_renderer(),
            config=SREAgentConfig(config=CrucibleConfig()),
        )
        assert agent._driver is not None
        assert agent._model_id == "test-model"

    def test_sre_agent_tool_assembly_diagnosis(self):
        from sregym_agents.crucible.agents import SREAgent

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
        from sregym_agents.crucible.agents import SREAgent

        agent = SREAgent(
            driver=self._mock_driver(),
            model_id="test-model",
            renderer=self._mock_renderer(),
        )
        tools = agent._assemble_tools("mitigation")
        tool_names = [t.__name__ if hasattr(t, "__name__") else str(t) for t in tools]
        assert "exec_bash" in tool_names
        assert "triage_cluster" not in tool_names
        assert "search_prior_incidents" not in tool_names
        assert "check_hypothesis_coverage" not in tool_names

    def test_judge_agent_construction(self):
        from sregym_agents.crucible.agents import JudgeAgent

        agent = JudgeAgent(
            driver=self._mock_driver(),
            model_id="test-model",
            renderer=self._mock_renderer(),
        )
        assert agent.MAX_SUBMIT_REMINDERS == 3

    def test_judge_agent_tool_assembly(self):
        from sregym_agents.crucible.agents import JudgeAgent

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
        from sregym_agents.crucible.agents import RecoveryAgent

        agent = RecoveryAgent(
            driver=self._mock_driver(),
            model_id="test-model",
            renderer=self._mock_renderer(),
        )
        assert agent._driver is not None


# ── Package re-exports ───────────────────────────────────────────────────


# ── AgentCLIDriver ──────────────────────────────────────────────────────


class TestAgentCLIDriverConstruction:
    def test_valid_provider(self):
        from sregym_agents.crucible.agents.drivers.agent_cli_driver import AgentCLIDriver

        driver = AgentCLIDriver(provider="claude", model="test-model")
        assert driver._provider == "claude"
        assert driver._model == "test-model"

    def test_invalid_provider_raises(self):
        from sregym_agents.crucible.agents.drivers.agent_cli_driver import AgentCLIDriver

        with pytest.raises(ValueError, match="currently only supports Claude Code"):
            AgentCLIDriver(provider="unsupported-provider")

    def test_aliases_accepted(self):
        from sregym_agents.crucible.agents.drivers.agent_cli_driver import AgentCLIDriver

        AgentCLIDriver(provider="claude-code")
        AgentCLIDriver(provider="anthropic")


class TestAgentCLIDriverToolRole:
    def test_sre_deps(self):
        from sregym_agents.crucible.agents.drivers.agent_cli_driver import AgentCLIDriver

        class FakeSREDeps:
            __name__ = "SREDeps"

        FakeSREDeps.__qualname__ = "SREDeps"
        # Use the actual class name check
        obj = type("SREDeps", (), {})()
        assert AgentCLIDriver._determine_tool_role(obj) == "sre"

    def test_judge_deps(self):
        from sregym_agents.crucible.agents.drivers.agent_cli_driver import AgentCLIDriver

        obj = type("JudgeDeps", (), {})()
        assert AgentCLIDriver._determine_tool_role(obj) == "judge"

    def test_none_deps(self):
        from sregym_agents.crucible.agents.drivers.agent_cli_driver import AgentCLIDriver

        assert AgentCLIDriver._determine_tool_role(None) is None

    def test_unknown_deps(self):
        from sregym_agents.crucible.agents.drivers.agent_cli_driver import AgentCLIDriver

        obj = type("SomethingElse", (), {})()
        assert AgentCLIDriver._determine_tool_role(obj) is None


class TestAgentCLIDriverMCPArgs:
    def _make_driver(self):
        from sregym_agents.crucible.agents.drivers.agent_cli_driver import AgentCLIDriver

        return AgentCLIDriver(provider="claude", model="test-model")

    def _make_sre_deps(self):
        """Build a minimal SREDeps-like object for arg construction."""
        renderer = MagicMock()
        renderer.version = "v3"
        deps = type(
            "SREDeps",
            (),
            {
                "namespace": "test-ns",
                "stage": "diagnosis",
                "shared_file": "/tmp/shared.md",
                "model_id": "test-model",
                "iteration": 2,
                "renderer": renderer,
                "kb_view_dir": "/tmp/kb-view",
                "ltm_call_budget": 3,
                "enable_ltm_verified_direct_submit": True,
            },
        )()
        return deps

    def test_basic_args(self):
        driver = self._make_driver()
        deps = self._make_sre_deps()
        args = driver._build_mcp_server_args(deps, "sre")

        from sregym_agents.crucible.agents.drivers.agent_cli_driver import _REPO_ROOT

        assert args[:2] == ["--directory", str(_REPO_ROOT)]
        assert args[2:5] == ["run", "python", "-m"]
        assert "--tools" in args
        assert args[args.index("--tools") + 1] == "sre"
        assert "--namespace" in args
        assert args[args.index("--namespace") + 1] == "test-ns"
        assert "--stage" in args
        assert args[args.index("--stage") + 1] == "diagnosis"
        assert "--prompt-version" in args
        assert args[args.index("--prompt-version") + 1] == "v3"

    def test_sre_specific_args(self):
        driver = self._make_driver()
        deps = self._make_sre_deps()
        args = driver._build_mcp_server_args(deps, "sre")

        assert "--kb-view-dir" in args
        assert args[args.index("--kb-view-dir") + 1] == "/tmp/kb-view"
        assert "--ltm-call-budget" in args
        assert args[args.index("--ltm-call-budget") + 1] == "3"
        assert "--enable-ltm-verified-direct-submit" in args

    def test_ipc_args(self):
        driver = self._make_driver()
        deps = self._make_sre_deps()
        args = driver._build_mcp_server_args(
            deps,
            "sre",
            signal_socket_path="/tmp/signal.sock",
            result_file_path="/tmp/result.json",
        )

        assert "--signal-socket" in args
        assert args[args.index("--signal-socket") + 1] == "/tmp/signal.sock"
        assert "--result-file" in args
        assert args[args.index("--result-file") + 1] == "/tmp/result.json"

    def test_backend_args(self):
        driver = self._make_driver()
        deps = self._make_sre_deps()
        args = driver._build_mcp_server_args(deps, "sre")

        assert "--backend" in args
        assert args[args.index("--backend") + 1] == "agent-cli"
        assert "--provider" in args
        assert args[args.index("--provider") + 1] == "claude"


class TestAgentCLIDriverPrompt:
    def _make_driver(self):
        from sregym_agents.crucible.agents.drivers.agent_cli_driver import AgentCLIDriver

        return AgentCLIDriver(provider="claude", model="test-model")

    def test_prompt_with_system(self):
        driver = self._make_driver()
        result = driver._build_prompt("user prompt", "system prompt", str, False)
        assert "<system>" in result
        assert "system prompt" in result
        assert "user prompt" in result

    def test_prompt_no_system(self):
        driver = self._make_driver()
        result = driver._build_prompt("user prompt", "", str, False)
        assert "<system>" not in result
        assert "user prompt" in result

    def test_prompt_structured_with_result_file(self):
        driver = self._make_driver()
        result = driver._build_prompt("user prompt", "", int, True)
        assert "submit_answer" in result

    def test_prompt_structured_without_result_file(self):
        driver = self._make_driver()
        result = driver._build_prompt("user prompt", "", int, False)
        assert "<output_format>" in result
        assert "<json>" in result

    def test_prompt_str_no_output_format(self):
        driver = self._make_driver()
        result = driver._build_prompt("user prompt", "", str, False)
        assert "<output_format>" not in result
        assert "submit_answer" not in result


class TestAgentCLIDriverJsonParsing:
    def test_json_tags(self):
        from pydantic import BaseModel

        from sregym_agents.crucible.agents.drivers.agent_cli_driver import AgentCLIDriver

        class MyModel(BaseModel):
            name: str
            value: int

        text = 'Some text\n<json>{"name": "test", "value": 42}</json>\nMore text'
        result = AgentCLIDriver._parse_json_from_text(text, MyModel)
        assert result is not None
        assert result.name == "test"
        assert result.value == 42

    def test_raw_json_fallback(self):
        from pydantic import BaseModel

        from sregym_agents.crucible.agents.drivers.agent_cli_driver import AgentCLIDriver

        class MyModel(BaseModel):
            name: str

        text = 'Here is the result: {"name": "fallback"}'
        result = AgentCLIDriver._parse_json_from_text(text, MyModel)
        assert result is not None
        assert result.name == "fallback"

    def test_deeply_nested_json(self):
        from pydantic import BaseModel

        from sregym_agents.crucible.agents.drivers.agent_cli_driver import AgentCLIDriver

        class MyModel(BaseModel):
            name: str
            meta: dict

        text = 'Result: {"name": "deep", "meta": {"inner": {"level": 3}}}'
        result = AgentCLIDriver._parse_json_from_text(text, MyModel)
        assert result is not None
        assert result.name == "deep"
        assert result.meta == {"inner": {"level": 3}}

    def test_no_json_returns_none(self):
        from sregym_agents.crucible.agents.drivers.agent_cli_driver import AgentCLIDriver

        result = AgentCLIDriver._parse_json_from_text("no json here", str)
        assert result is None


class TestAgentCLIDriverInterruptReconstruction:
    def test_diagnosis_short_circuit(self):
        from sregym_agents.crucible.agents.drivers.agent_cli_driver import AgentCLIDriver
        from sregym_agents.crucible.tools._kb_tools import LTMShortCircuit

        signal = {
            "short_circuit": True,
            "confirmed": ["root-cause-1"],
            "confirmed_slugs": ["slug-1"],
            "iteration": 3,
        }
        result = AgentCLIDriver._reconstruct_interrupt(signal)
        assert isinstance(result, LTMShortCircuit)
        assert result.confirmed == ["root-cause-1"]
        assert result.confirmed_slugs == ["slug-1"]
        assert result.iteration == 3

    def test_unknown_signal_passthrough(self):
        from sregym_agents.crucible.agents.drivers.agent_cli_driver import AgentCLIDriver

        signal = {"some": "data"}
        result = AgentCLIDriver._reconstruct_interrupt(signal)
        assert result == signal


class TestAgentCLIDriverResultFile:
    def test_read_valid(self, tmp_path):
        from sregym_agents.crucible.agents.drivers.agent_cli_driver import AgentCLIDriver

        path = tmp_path / "result.json"
        path.write_text('{"type": "answer", "data": {"answer": "test"}}')
        result = AgentCLIDriver._read_result_file(str(path))
        assert result is not None
        assert result["type"] == "answer"

    def test_read_missing(self):
        from sregym_agents.crucible.agents.drivers.agent_cli_driver import AgentCLIDriver

        result = AgentCLIDriver._read_result_file("/nonexistent/path")
        assert result is None

    def test_read_empty(self, tmp_path):
        from sregym_agents.crucible.agents.drivers.agent_cli_driver import AgentCLIDriver

        path = tmp_path / "result.json"
        path.write_text("")
        result = AgentCLIDriver._read_result_file(str(path))
        assert result is None

    def test_read_invalid_json(self, tmp_path):
        from sregym_agents.crucible.agents.drivers.agent_cli_driver import AgentCLIDriver

        path = tmp_path / "result.json"
        path.write_text("not json")
        result = AgentCLIDriver._read_result_file(str(path))
        assert result is None


class TestAgentCLIDriverParseResultData:
    def test_parse_sre_submission(self):
        from sregym_agents.crucible.agents.drivers.agent_cli_driver import AgentCLIDriver
        from sregym_agents.crucible.tools._deps import SRESubmission

        data = {
            "type": "answer",
            "data": {
                "answer": "misconfigured CPU limits",
                "justification": "evidence here",
                "causal_chain": "A -> B -> C",
            },
        }
        result = AgentCLIDriver._parse_result_data(data, SRESubmission)
        assert result.answer == "misconfigured CPU limits"
        assert result.justification == "evidence here"
        assert result.causal_chain == "A -> B -> C"


class TestAgentCLIDriverGetJsonSchema:
    def test_pydantic_model(self):
        from pydantic import BaseModel

        from sregym_agents.crucible.agents.drivers.agent_cli_driver import AgentCLIDriver

        class M(BaseModel):
            name: str
            value: int

        schema = AgentCLIDriver._get_json_schema(M)
        assert "properties" in schema
        assert "name" in schema["properties"]

    def test_dataclass(self):
        import dataclasses

        from sregym_agents.crucible.agents.drivers.agent_cli_driver import AgentCLIDriver

        @dataclasses.dataclass
        class D:
            x: str
            y: int
            z: float
            flag: bool
            items: list

        schema = AgentCLIDriver._get_json_schema(D)
        assert schema["type"] == "object"
        props = schema["properties"]
        assert props["x"] == {"type": "string"}
        assert props["y"] == {"type": "integer"}
        assert props["z"] == {"type": "number"}
        assert props["flag"] == {"type": "boolean"}
        assert props["items"] == {"type": "array"}

    def test_fallback(self):
        from sregym_agents.crucible.agents.drivers.agent_cli_driver import AgentCLIDriver

        schema = AgentCLIDriver._get_json_schema(str)
        assert schema["type"] == "string"


# ── create_driver factory ───────────────────────────────────────────────


class TestCreateDriverFactory:
    def test_pydantic_ai_backend(self):
        from sregym_agents.crucible.config import CrucibleConfig
        from sregym_agents.crucible.driver import create_driver

        config = CrucibleConfig(prompt_version="v2", backend="pydantic-ai")
        driver = create_driver("test-model", config)
        assert type(driver).__name__ == "PydanticAIDriver"

    def test_agent_cli_backend(self):
        from sregym_agents.crucible.config import CrucibleConfig
        from sregym_agents.crucible.driver import create_driver

        config = CrucibleConfig(prompt_version="v2", backend="agent-cli")
        driver = create_driver("test-model", config)
        assert type(driver).__name__ == "AgentCLIDriver"


# ── MCP server IPC helpers ──────────────────────────────────────────────


class TestMCPServerIPC:
    def test_write_and_read_result_file(self, tmp_path):
        from sregym_agents.crucible.tools.mcp_server import _write_result_file

        path = str(tmp_path / "result.json")
        _write_result_file(path, {"type": "answer", "data": {"answer": "test"}})

        import json

        with open(path) as f:
            data = json.load(f)
        assert data["type"] == "answer"
        assert data["data"]["answer"] == "test"

    def test_send_signal_no_server(self):
        """Sending to a non-existent socket should log a warning, not crash."""
        from sregym_agents.crucible.tools.mcp_server import _send_signal

        # Should not raise
        _send_signal("/tmp/nonexistent-crucible-test.sock", {"test": True})


# ── Package re-exports ───────────────────────────────────────────────────


class TestPackageExports:
    def test_all_exports_importable(self):
        from sregym_agents.crucible import agents

        assert hasattr(agents, "AgentDriver")
        assert hasattr(agents, "AgentResult")
        assert hasattr(agents, "RunSubagent")
        assert hasattr(agents, "ShortCircuitSignal")
        assert hasattr(agents, "PydanticAIDriver")
        assert hasattr(agents, "AgentCLIDriver")
        assert hasattr(agents, "SREAgent")
        assert hasattr(agents, "JudgeAgent")
        assert hasattr(agents, "RecoveryAgent")
        assert hasattr(agents, "SREAgentConfig")
