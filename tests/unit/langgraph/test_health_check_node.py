"""Tests for agent-based health_check node function."""

from pathlib import Path
from unittest.mock import Mock, patch

from app_operator.config import AgentConfig, Config, DeploymentConfig
from app_operator.filesystem import InMemoryFilesystem
from app_operator.langgraph.context import NodeContext
from app_operator.langgraph.nodes.monitor import HealthVerdictResponse, health_check
from app_operator.langgraph.utils import AgentResult
from app_operator.trajectory import NullTrajectoryRecorder


def _make_state(**overrides):
    defaults = {
        "attempt": 1,
        "max_attempts": 3,
        "messages": [],
        "scripts_done": True,
        "deploy_result": {"success": True, "exit_code": 0, "stdout": "", "stderr": ""},
        "health_verdict": None,
        "last_fix_summary": None,
        "agent_token_usage": [],
    }
    defaults.update(overrides)
    return defaults


def _make_ctx(
    repo_path: Path,
    filesystem,
    config: Config,
    loader=None,
    agent=None,
    check_shutdown=None,
    recorder=None,
) -> NodeContext:
    return NodeContext(
        repo_path=repo_path,
        filesystem=filesystem,
        loader=loader or Mock(),
        config=config,
        context_limit=10000,
        recorder=recorder or NullTrajectoryRecorder(),
        health_check_interval=0,
        check_shutdown=check_shutdown,
        health_agent=agent,
    )


class TestHealthCheckNode:
    """Test agent-based health_check node."""

    def test_health_check_invokes_agent_with_assess_health_prompt(self):
        """Test that health_check invokes the agent with the assess_health template."""
        state = _make_state()
        repo_path = Path("/test/repo")
        filesystem = InMemoryFilesystem()
        config = Config(
            agent=AgentConfig(provider="codex", model="test-model"),
            deployment=DeploymentConfig(platform="docker"),
        )
        loader = Mock()
        loader.render.return_value = "rendered prompt"
        agent = Mock()
        recorder = Mock(spec=NullTrajectoryRecorder())
        recorder.end_phase = Mock()
        ctx = _make_ctx(repo_path, filesystem, config, loader, agent, recorder=recorder)

        verdict = HealthVerdictResponse(
            healthy=True,
            assessment="All services running",
            diagnosis="",
            script_was_fixed=False,
        )

        with patch("app_operator.langgraph.utils.invoke_agent") as mock_invoke:
            mock_invoke.return_value = AgentResult(text="response text", messages=[], structured=verdict)

            health_check(state, ctx)

        loader.render.assert_called_once()
        call_args = loader.render.call_args
        assert call_args[0][0] == "deployer/assess_health.jinja2"
        assert call_args[1]["structured_output"] is True
        mock_invoke.assert_called_once()

    def test_structured_response_stored_as_dict(self):
        """Test that the structured response is stored in state as a dict."""
        state = _make_state()
        repo_path = Path("/test/repo")
        filesystem = InMemoryFilesystem()
        config = Config(
            agent=AgentConfig(provider="codex", model="test-model"),
            deployment=DeploymentConfig(platform="docker"),
        )
        loader = Mock()
        loader.render.return_value = "rendered prompt"
        agent = Mock()
        recorder = Mock(spec=NullTrajectoryRecorder())
        recorder.end_phase = Mock()
        ctx = _make_ctx(repo_path, filesystem, config, loader, agent, recorder=recorder)

        verdict = HealthVerdictResponse(
            healthy=False,
            assessment="mongodb down",
            diagnosis="CrashLoopBackOff",
            script_was_fixed=True,
        )

        with patch("app_operator.langgraph.utils.invoke_agent") as mock_invoke:
            mock_invoke.return_value = AgentResult(text="response text", messages=[], structured=verdict)

            result = health_check(state, ctx)

        assert result["health_verdict"] == {
            "healthy": False,
            "assessment": "mongodb down",
            "diagnosis": "CrashLoopBackOff",
            "script_was_fixed": True,
        }

    def test_healthy_verdict_ends_phase_success(self):
        """Test that a healthy verdict calls end_phase with success."""
        state = _make_state()
        repo_path = Path("/test/repo")
        filesystem = InMemoryFilesystem()
        config = Config(
            agent=AgentConfig(provider="codex", model="test-model"),
            deployment=DeploymentConfig(platform="docker"),
        )
        loader = Mock()
        loader.render.return_value = "rendered prompt"
        agent = Mock()
        recorder = Mock(spec=NullTrajectoryRecorder())
        recorder.end_phase = Mock()
        ctx = _make_ctx(repo_path, filesystem, config, loader, agent, recorder=recorder)

        verdict = HealthVerdictResponse(
            healthy=True,
            assessment="All OK",
            diagnosis="",
            script_was_fixed=False,
        )

        with patch("app_operator.langgraph.utils.invoke_agent") as mock_invoke:
            mock_invoke.return_value = AgentResult(text="response text", messages=[], structured=verdict)

            health_check(state, ctx)

        recorder.end_phase.assert_called_once_with("success")

    def test_skip_when_deploy_not_successful(self):
        """Test health_check skips when deploy_result is not successful."""
        state = _make_state(deploy_result={"success": False, "exit_code": 1})
        repo_path = Path("/test/repo")
        filesystem = InMemoryFilesystem()
        config = Config(agent=AgentConfig(provider="codex", model="test-model"))
        loader = Mock()
        agent = Mock()
        ctx = _make_ctx(repo_path, filesystem, config, loader, agent)

        result = health_check(state, ctx)

        assert result["health_verdict"] is None
        agent.stream.assert_not_called()

    def test_shutdown_check(self):
        """Test health_check respects shutdown flag."""
        state = _make_state()
        repo_path = Path("/test/repo")
        filesystem = InMemoryFilesystem()
        config = Config(agent=AgentConfig(provider="codex", model="test-model"))
        loader = Mock()
        agent = Mock()
        ctx = _make_ctx(repo_path, filesystem, config, loader, agent, check_shutdown=Mock(return_value=True))

        result = health_check(state, ctx)

        assert result is state
        agent.stream.assert_not_called()

    def test_fallback_when_structured_response_is_none(self):
        """Test health_check handles None structured_response gracefully."""
        state = _make_state()
        repo_path = Path("/test/repo")
        filesystem = InMemoryFilesystem()
        config = Config(
            agent=AgentConfig(provider="codex", model="test-model"),
            deployment=DeploymentConfig(platform="docker"),
        )
        loader = Mock()
        loader.render.return_value = "rendered prompt"
        agent = Mock()
        recorder = Mock(spec=NullTrajectoryRecorder())
        recorder.end_phase = Mock()
        ctx = _make_ctx(repo_path, filesystem, config, loader, agent, recorder=recorder)

        with patch("app_operator.langgraph.utils.invoke_agent") as mock_invoke:
            mock_invoke.return_value = AgentResult(text="response text", messages=[], structured=None)

            result = health_check(state, ctx)

        assert result["health_verdict"] == {
            "healthy": False,
            "assessment": "Agent did not return structured response",
            "diagnosis": "",
            "script_was_fixed": False,
        }

    def test_saves_assessment_log(self):
        """Test health_check saves assessment log file."""
        state = _make_state()
        repo_path = Path("/test/repo")
        filesystem = InMemoryFilesystem()
        config = Config(
            agent=AgentConfig(provider="codex", model="test-model"),
            deployment=DeploymentConfig(platform="docker"),
        )
        loader = Mock()
        loader.render.return_value = "rendered prompt"
        agent = Mock()
        recorder = Mock(spec=NullTrajectoryRecorder())
        recorder.end_phase = Mock()
        ctx = _make_ctx(repo_path, filesystem, config, loader, agent, recorder=recorder)

        verdict = HealthVerdictResponse(
            healthy=True,
            assessment="All services running",
            diagnosis="",
            script_was_fixed=False,
        )

        with patch("app_operator.langgraph.utils.invoke_agent") as mock_invoke:
            mock_invoke.return_value = AgentResult(text="response text", messages=[], structured=verdict)

            health_check(state, ctx)

        log_path = repo_path / ".sds" / "logs" / "health_check_attempt_1.log"
        assert filesystem.exists(log_path)
        content = filesystem.read_text(log_path)
        assert "healthy" in content
        assert "All services running" in content
