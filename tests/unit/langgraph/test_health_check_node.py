"""Tests for agent-based health_check node function."""

from pathlib import Path
from unittest.mock import Mock, patch

from app_operator.config import AgentConfig, Config, DeploymentConfig, OperatorConfig, OperatorPhaseConfig
from app_operator.constants import DEPLOYMENT_PROGRESS_FILENAME
from app_operator.filesystem import InMemoryFilesystem
from app_operator.langgraph.context import NodeContext
from app_operator.langgraph.nodes.monitor import HealthVerdictResponse, health_check
from app_operator.langgraph.utils import AgentResult
from app_operator.trajectory import NullTrajectoryRecorder
from libs.model_config import ModelConfig


def _make_state(**overrides):
    defaults = {
        "attempt": 1,
        "max_attempts": 3,
        "messages": [],
        "scripts_done": True,
        "deploy_result": {"success": True, "exit_code": 0, "stdout": "", "stderr": ""},
        "health_verdict": None,
        "agent_token_usage": [],
    }
    defaults.update(overrides)
    return defaults


def _make_ctx(
    repo_path: Path,
    filesystem,
    config: Config,
    loader=None,
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
        check_shutdown=check_shutdown,
    )


class TestHealthCheckNode:
    """Test agent-based health_check node."""

    def test_health_check_invokes_agent_with_assess_health_prompt(self):
        """Test that health_check invokes the agent with the assess_health template."""
        state = _make_state()
        repo_path = Path("/test/repo")
        filesystem = InMemoryFilesystem()
        config = Config(
            agent=AgentConfig(backend="codex", model_config=ModelConfig(provider="openai", model="test-model")),
            deployment=DeploymentConfig(platform="docker"),
        )
        loader = Mock()
        loader.render.return_value = "rendered prompt"
        agent = Mock()
        recorder = Mock(spec=NullTrajectoryRecorder())
        recorder.end_phase = Mock()
        ctx = _make_ctx(repo_path, filesystem, config, loader, recorder=recorder)

        verdict = HealthVerdictResponse(
            healthy=True,
            assessment="All services running",
            diagnosis="",
            script_was_fixed=False,
        )

        with patch("app_operator.langgraph.utils.invoke_agent") as mock_invoke:
            mock_invoke.return_value = AgentResult(text="response text", messages=[], structured=verdict)

            health_check(state, ctx, agent)

        assert loader.render.call_count == 2
        render_templates = [c[0][0] for c in loader.render.call_args_list]
        assert "health_judge_agent/system.jinja2" in render_templates
        assert "health_judge_agent/user.jinja2" in render_templates
        # user template receives structured_output
        user_call = next(c for c in loader.render.call_args_list if c[0][0] == "health_judge_agent/user.jinja2")
        assert user_call[1]["structured_output"] is True
        mock_invoke.assert_called_once()

    def test_structured_response_stored_as_dict(self):
        """Test that the structured response is stored in state as a dict."""
        state = _make_state()
        repo_path = Path("/test/repo")
        filesystem = InMemoryFilesystem()
        config = Config(
            agent=AgentConfig(backend="codex", model_config=ModelConfig(provider="openai", model="test-model")),
            deployment=DeploymentConfig(platform="docker"),
        )
        loader = Mock()
        loader.render.return_value = "rendered prompt"
        agent = Mock()
        recorder = Mock(spec=NullTrajectoryRecorder())
        recorder.end_phase = Mock()
        ctx = _make_ctx(repo_path, filesystem, config, loader, recorder=recorder)

        verdict = HealthVerdictResponse(
            healthy=False,
            assessment="mongodb down",
            diagnosis="CrashLoopBackOff",
            script_was_fixed=True,
        )

        with patch("app_operator.langgraph.utils.invoke_agent") as mock_invoke:
            mock_invoke.return_value = AgentResult(text="response text", messages=[], structured=verdict)

            result = health_check(state, ctx, agent)

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
            agent=AgentConfig(backend="codex", model_config=ModelConfig(provider="openai", model="test-model")),
            deployment=DeploymentConfig(platform="docker"),
        )
        loader = Mock()
        loader.render.return_value = "rendered prompt"
        agent = Mock()
        recorder = Mock(spec=NullTrajectoryRecorder())
        recorder.end_phase = Mock()
        ctx = _make_ctx(repo_path, filesystem, config, loader, recorder=recorder)

        verdict = HealthVerdictResponse(
            healthy=True,
            assessment="All OK",
            diagnosis="",
            script_was_fixed=False,
        )

        with patch("app_operator.langgraph.utils.invoke_agent") as mock_invoke:
            mock_invoke.return_value = AgentResult(text="response text", messages=[], structured=verdict)

            health_check(state, ctx, agent)

        recorder.end_phase.assert_called_once_with("success")

    def test_skip_when_deploy_not_successful(self):
        """Test health_check skips when deploy_result is not successful."""
        state = _make_state(deploy_result={"success": False, "exit_code": 1})
        repo_path = Path("/test/repo")
        filesystem = InMemoryFilesystem()
        config = Config(
            agent=AgentConfig(backend="codex", model_config=ModelConfig(provider="openai", model="test-model"))
        )
        loader = Mock()
        agent = Mock()
        ctx = _make_ctx(repo_path, filesystem, config, loader)

        result = health_check(state, ctx, agent)

        assert result["health_verdict"] is None
        agent.stream.assert_not_called()

    def test_shutdown_check(self):
        """Test health_check respects shutdown flag."""
        state = _make_state()
        repo_path = Path("/test/repo")
        filesystem = InMemoryFilesystem()
        config = Config(
            agent=AgentConfig(backend="codex", model_config=ModelConfig(provider="openai", model="test-model"))
        )
        loader = Mock()
        agent = Mock()
        ctx = _make_ctx(repo_path, filesystem, config, loader, check_shutdown=Mock(return_value=True))

        result = health_check(state, ctx, agent)

        assert result is state
        agent.stream.assert_not_called()

    def test_fallback_when_structured_response_is_none(self):
        """Test health_check handles None structured_response gracefully."""
        state = _make_state()
        repo_path = Path("/test/repo")
        filesystem = InMemoryFilesystem()
        config = Config(
            agent=AgentConfig(backend="codex", model_config=ModelConfig(provider="openai", model="test-model")),
            deployment=DeploymentConfig(platform="docker"),
        )
        loader = Mock()
        loader.render.return_value = "rendered prompt"
        agent = Mock()
        recorder = Mock(spec=NullTrajectoryRecorder())
        recorder.end_phase = Mock()
        ctx = _make_ctx(repo_path, filesystem, config, loader, recorder=recorder)

        with patch("app_operator.langgraph.utils.invoke_agent") as mock_invoke:
            mock_invoke.return_value = AgentResult(text="response text", messages=[], structured=None)

            result = health_check(state, ctx, agent)

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
            agent=AgentConfig(backend="codex", model_config=ModelConfig(provider="openai", model="test-model")),
            deployment=DeploymentConfig(platform="docker"),
        )
        loader = Mock()
        loader.render.return_value = "rendered prompt"
        agent = Mock()
        recorder = Mock(spec=NullTrajectoryRecorder())
        recorder.end_phase = Mock()
        ctx = _make_ctx(repo_path, filesystem, config, loader, recorder=recorder)

        verdict = HealthVerdictResponse(
            healthy=True,
            assessment="All services running",
            diagnosis="",
            script_was_fixed=False,
        )

        with patch("app_operator.langgraph.utils.invoke_agent") as mock_invoke:
            mock_invoke.return_value = AgentResult(text="response text", messages=[], structured=verdict)

            health_check(state, ctx, agent)

        log_path = repo_path / ".sds" / "logs" / "health_check_attempt_1.log"
        assert filesystem.exists(log_path)
        content = filesystem.read_text(log_path)
        assert "healthy" in content
        assert "All services running" in content

    def test_health_prompt_receives_deployment_progress_path_when_enabled(self):
        """Test health prompt receives deployment_progress_path when flag enabled."""
        state = _make_state()
        repo_path = Path("/test/repo")
        filesystem = InMemoryFilesystem()
        phase = OperatorPhaseConfig(fix_summary_consolidation=True)
        config = Config(
            agent=AgentConfig(backend="codex", model_config=ModelConfig(provider="openai", model="test-model")),
            deployment=DeploymentConfig(platform="docker"),
            operator=OperatorConfig(phase=phase),
        )
        loader = Mock()
        loader.render.return_value = "rendered prompt"
        agent = Mock()
        recorder = Mock(spec=NullTrajectoryRecorder())
        recorder.end_phase = Mock()
        ctx = _make_ctx(repo_path, filesystem, config, loader, recorder=recorder)

        verdict = HealthVerdictResponse(
            healthy=True,
            assessment="All OK",
            diagnosis="",
            script_was_fixed=False,
        )

        with patch("app_operator.langgraph.utils.invoke_agent") as mock_invoke:
            mock_invoke.return_value = AgentResult(text="response text", messages=[], structured=verdict)
            health_check(state, ctx, agent)

        call_kwargs = loader.render.call_args[1]
        assert call_kwargs["deployment_progress_path"] == repo_path / ".sds" / DEPLOYMENT_PROGRESS_FILENAME
        assert call_kwargs["has_deployment_progress"] is False  # file does not exist

    def test_health_prompt_omits_progress_path_when_disabled(self):
        """Test health prompt receives None deployment_progress_path when flag disabled."""
        state = _make_state()
        repo_path = Path("/test/repo")
        filesystem = InMemoryFilesystem()
        phase = OperatorPhaseConfig(fix_summary_consolidation=False)
        config = Config(
            agent=AgentConfig(backend="codex", model_config=ModelConfig(provider="openai", model="test-model")),
            deployment=DeploymentConfig(platform="docker"),
            operator=OperatorConfig(phase=phase),
        )
        loader = Mock()
        loader.render.return_value = "rendered prompt"
        agent = Mock()
        recorder = Mock(spec=NullTrajectoryRecorder())
        recorder.end_phase = Mock()
        ctx = _make_ctx(repo_path, filesystem, config, loader, recorder=recorder)

        verdict = HealthVerdictResponse(
            healthy=True,
            assessment="All OK",
            diagnosis="",
            script_was_fixed=False,
        )

        with patch("app_operator.langgraph.utils.invoke_agent") as mock_invoke:
            mock_invoke.return_value = AgentResult(text="response text", messages=[], structured=verdict)
            health_check(state, ctx, agent)

        call_kwargs = loader.render.call_args[1]
        assert call_kwargs["deployment_progress_path"] is None
        assert call_kwargs["has_deployment_progress"] is False
