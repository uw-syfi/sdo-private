"""Tests for generate_scripts node function."""

from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

from app_operator.config import AgentConfig, Config
from app_operator.filesystem import InMemoryFilesystem
from app_operator.langgraph.context import NodeContext
from app_operator.langgraph.nodes.generator import generate_scripts
from app_operator.langgraph.state import OperatorState
from app_operator.langgraph.utils import AgentResult
from app_operator.trajectory import NullTrajectoryRecorder, Phase


def _make_ctx(
    repo_path: Path,
    config: Config,
    loader=None,
    agent=None,
    recorder=None,
) -> NodeContext:
    return NodeContext(
        repo_path=repo_path,
        filesystem=InMemoryFilesystem(),
        loader=loader or Mock(),
        config=config,
        context_limit=10000,
        recorder=recorder or NullTrajectoryRecorder(),
        health_check_interval=0,
        script_agent=agent,
    )


class TestGenerateScripts:
    """Test generate_scripts node function."""

    def test_generate_scripts_when_not_done(self):
        """Test generate_scripts generates scripts when not done."""
        state = OperatorState(
            attempt=1,
            max_attempts=3,
            messages=[],
            scripts_done=False,
            deploy_result=None,
            health_result=None,
            last_fix_summary=None,
        )

        repo_path = Path("/test/repo")
        config = Config(agent=AgentConfig(provider="codex", model="test-model"))
        loader = Mock()
        agent = Mock()
        recorder = Mock(spec=NullTrajectoryRecorder())
        recorder.phase = Mock(return_value=MagicMock(__enter__=Mock(), __exit__=Mock()))
        ctx = _make_ctx(repo_path, config, loader, agent, recorder)

        with patch("app_operator.langgraph.nodes.generator.invoke_agent") as mock_invoke:
            mock_invoke.return_value = AgentResult(text="Script generated", messages=[], structured=None)

            with patch("app_operator.langgraph.nodes.generator.analyze_repository") as mock_analyze:
                mock_analyze.return_value = {"files": []}

                result_state = generate_scripts(state, ctx)

        assert result_state["scripts_done"] is True
        # Should invoke agent twice: once for deploy.sh, once for health_check.sh
        assert mock_invoke.call_count == 2

    def test_generate_scripts_when_already_done(self):
        """Test generate_scripts skips when already done."""
        state = OperatorState(
            attempt=1,
            max_attempts=3,
            messages=[],
            scripts_done=True,
            deploy_result=None,
            health_result=None,
            last_fix_summary=None,
        )

        repo_path = Path("/test/repo")
        config = Config(agent=AgentConfig(provider="codex", model="test-model"))
        ctx = _make_ctx(repo_path, config)

        with patch("app_operator.langgraph.nodes.generator.invoke_agent") as mock_invoke:
            result_state = generate_scripts(state, ctx)

        # Should not invoke agent when scripts already done
        mock_invoke.assert_not_called()
        assert result_state["scripts_done"] is True

    def test_generate_scripts_analyzes_repository(self):
        """Test generate_scripts analyzes repository before generating."""
        state = OperatorState(
            attempt=1,
            max_attempts=3,
            messages=[],
            scripts_done=False,
            deploy_result=None,
            health_result=None,
            last_fix_summary=None,
        )

        repo_path = Path("/test/repo")
        config = Config(agent=AgentConfig(provider="codex", model="test-model"))
        ctx = _make_ctx(repo_path, config)

        with patch("app_operator.langgraph.nodes.generator.invoke_agent") as mock_invoke:
            mock_invoke.return_value = AgentResult(text="", messages=[], structured=None)

            with patch("app_operator.langgraph.nodes.generator.analyze_repository") as mock_analyze:
                mock_analyze.return_value = {"files": ["file1.py", "file2.py"]}

                generate_scripts(state, ctx)

                # Verify repository was analyzed
                mock_analyze.assert_called_once_with(repo_path)

    def test_generate_scripts_uses_correct_script_names(self):
        """Test generate_scripts generates both deploy.sh and health_check.sh."""
        state = OperatorState(
            attempt=1,
            max_attempts=3,
            messages=[],
            scripts_done=False,
            deploy_result=None,
            health_result=None,
            last_fix_summary=None,
        )

        repo_path = Path("/test/repo")
        config = Config(agent=AgentConfig(provider="codex", model="test-model"))
        ctx = _make_ctx(repo_path, config)

        with patch("app_operator.langgraph.nodes.generator.invoke_agent") as mock_invoke:
            mock_invoke.return_value = AgentResult(text="", messages=[], structured=None)

            with patch("app_operator.langgraph.nodes.generator.analyze_repository") as mock_analyze:
                mock_analyze.return_value = {}

                with patch("app_operator.langgraph.nodes.generator.create_generate_script_prompt") as mock_prompt:
                    mock_prompt.return_value = "prompt"

                    generate_scripts(state, ctx)

                    # Check that prompts were created for both scripts
                    assert mock_prompt.call_count == 2
                    call_args_list = mock_prompt.call_args_list

                    script_names = [call[1]["script_name"] for call in call_args_list]
                    assert "deploy.sh" in script_names
                    assert "health_check.sh" in script_names

    def test_generate_scripts_uses_recorder_phase(self):
        """Test generate_scripts uses recorder phase context."""
        state = OperatorState(
            attempt=1,
            max_attempts=3,
            messages=[],
            scripts_done=False,
            deploy_result=None,
            health_result=None,
            last_fix_summary=None,
        )

        repo_path = Path("/test/repo")
        config = Config(agent=AgentConfig(provider="codex", model="test-model"))
        loader = Mock()
        agent = Mock()
        recorder = Mock(spec=NullTrajectoryRecorder())
        phase_context = MagicMock()
        recorder.phase = Mock(return_value=phase_context)
        ctx = _make_ctx(repo_path, config, loader, agent, recorder)

        with patch("app_operator.langgraph.nodes.generator.invoke_agent") as mock_invoke:
            mock_invoke.return_value = AgentResult(text="", messages=[], structured=None)

            with patch("app_operator.langgraph.nodes.generator.analyze_repository") as mock_analyze:
                mock_analyze.return_value = {}

                generate_scripts(state, ctx)

                # Verify phase context was used
                recorder.phase.assert_called_once_with(Phase.SCRIPT_GENERATION)
                phase_context.__enter__.assert_called_once()
                phase_context.__exit__.assert_called_once()

    def test_generate_scripts_uses_platform_from_config(self):
        """Test generate_scripts uses platform from config."""
        state = OperatorState(
            attempt=1,
            max_attempts=3,
            messages=[],
            scripts_done=False,
            deploy_result=None,
            health_result=None,
            last_fix_summary=None,
        )

        repo_path = Path("/test/repo")
        config = Config(agent=AgentConfig(provider="codex", model="test-model"))
        config.deployment.platform = "kubernetes"
        ctx = _make_ctx(repo_path, config)

        with patch("app_operator.langgraph.nodes.generator.invoke_agent") as mock_invoke:
            mock_invoke.return_value = AgentResult(text="", messages=[], structured=None)

            with patch("app_operator.langgraph.nodes.generator.analyze_repository") as mock_analyze:
                mock_analyze.return_value = {}

                with patch("app_operator.langgraph.nodes.generator.create_generate_script_prompt") as mock_prompt:
                    mock_prompt.return_value = "prompt"

                    generate_scripts(state, ctx)

                    # Verify platform was passed
                    call_args_list = mock_prompt.call_args_list
                    for call in call_args_list:
                        assert call[1]["platform"] == "kubernetes"
