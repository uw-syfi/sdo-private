"""Tests for fix_errors node function."""

from pathlib import Path
from unittest.mock import Mock, patch

from app_operator.config import AgentConfig, Config, OperatorConfig, OperatorPhaseConfig
from app_operator.constants import DEPLOYMENT_PROGRESS_FILENAME
from app_operator.filesystem import InMemoryFilesystem
from app_operator.langgraph.context import NodeContext
from app_operator.langgraph.nodes.deployer import FixSummaryResponse, fix_errors
from app_operator.langgraph.state import OperatorState
from app_operator.langgraph.utils import AgentResult
from app_operator.trajectory import NullTrajectoryRecorder
from libs.model_config import ModelConfig


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


class TestFixErrors:
    """Test fix_errors node function."""

    def test_fix_errors_with_deploy_failure(self):
        """Test fix_errors processes deployment errors."""
        state = OperatorState(  # type: ignore[call-overload]
            attempt=1,
            max_attempts=3,
            messages=[],
            scripts_done=True,
            deploy_result={"exit_code": 1, "stderr": "Error occurred"},
            health_verdict=None,
        )

        repo_path = Path("/test/repo")
        filesystem = InMemoryFilesystem()
        config = Config(
            agent=AgentConfig(backend="codex", model_config=ModelConfig(provider="openai", model="test-model"))
        )
        loader = Mock()
        agent = Mock()
        check_shutdown = Mock(return_value=False)
        recorder = Mock(spec=NullTrajectoryRecorder())
        recorder.end_phase = Mock()
        ctx = _make_ctx(repo_path, filesystem, config, loader, check_shutdown, recorder)
        ctx.invoke = Mock(
            return_value=AgentResult(text="", messages=[], structured=FixSummaryResponse(summary="Fixed the issue"))
        )

        with patch("app_operator.langgraph.nodes.deployer.prepare_error_context") as mock_prepare:
            mock_prepare.return_value = {"error": "Error occurred"}

            result_state = fix_errors(state, ctx, agent)

        assert result_state["attempt"] == 2  # Incremented
        recorder.end_phase.assert_called_once_with("needs_retry")

    def test_fix_errors_extracts_summary(self):
        """Test fix_errors writes summary to log file from structured response."""
        state = OperatorState(  # type: ignore[call-overload]
            attempt=1,
            max_attempts=3,
            messages=[],
            scripts_done=True,
            deploy_result={"exit_code": 1},
            health_verdict=None,
        )

        repo_path = Path("/test/repo")
        filesystem = InMemoryFilesystem()
        config = Config(
            agent=AgentConfig(backend="codex", model_config=ModelConfig(provider="openai", model="test-model"))
        )
        ctx = _make_ctx(repo_path, filesystem, config)
        ctx.invoke = Mock(
            return_value=AgentResult(text="", messages=[], structured=FixSummaryResponse(summary="This is the summary"))
        )

        with patch("app_operator.langgraph.nodes.deployer.prepare_error_context") as mock_prepare:
            mock_prepare.return_value = {}

            fix_errors(state, ctx, None)

        log_path = repo_path / ".sds" / "logs" / "fix_summary_1.log"
        assert filesystem.exists(log_path)
        assert "This is the summary" in filesystem.read_text(log_path)

    def test_fix_errors_no_structured_response(self):
        """Test fix_errors when agent returns no structured response."""
        state = OperatorState(  # type: ignore[call-overload]
            attempt=1,
            max_attempts=3,
            messages=[],
            scripts_done=True,
            deploy_result={"exit_code": 1},
            health_verdict=None,
        )

        repo_path = Path("/test/repo")
        filesystem = InMemoryFilesystem()
        config = Config(
            agent=AgentConfig(backend="codex", model_config=ModelConfig(provider="openai", model="test-model"))
        )
        ctx = _make_ctx(repo_path, filesystem, config)
        ctx.invoke = Mock(return_value=AgentResult(text="No summary here", messages=[], structured=None))

        with patch("app_operator.langgraph.nodes.deployer.prepare_error_context") as mock_prepare:
            mock_prepare.return_value = {}

            fix_errors(state, ctx, None)

        # No log file should be written when there's no structured response
        log_path = repo_path / ".sds" / "logs" / "fix_summary_1.log"
        assert not filesystem.exists(log_path)

    def test_fix_errors_respects_shutdown(self):
        """Test fix_errors respects shutdown flag.

        Verify observable outcome: state is unchanged when shutdown is signaled.
        """
        state = OperatorState(  # type: ignore[call-overload]
            attempt=1,
            max_attempts=3,
            messages=[],
            scripts_done=True,
            deploy_result={"exit_code": 1},
            health_verdict=None,
        )

        repo_path = Path("/test/repo")
        filesystem = InMemoryFilesystem()
        config = Config(
            agent=AgentConfig(backend="codex", model_config=ModelConfig(provider="openai", model="test-model"))
        )
        ctx = _make_ctx(repo_path, filesystem, config, check_shutdown=Mock(return_value=True))

        result_state = fix_errors(state, ctx, None)

        # Verify observable outcome: state should be unchanged
        assert result_state == state
        # Attempt should not be incremented
        assert result_state["attempt"] == 1

    def test_fix_errors_writes_summary_to_log_file(self):
        """Test fix_errors writes summary to log file.

        Verify observable outcome: summary is written to the expected log file.
        """
        state = OperatorState(  # type: ignore[call-overload]
            attempt=1,
            max_attempts=3,
            messages=[],
            scripts_done=True,
            deploy_result={"exit_code": 1},
            health_verdict=None,
        )

        repo_path = Path("/test/repo")
        filesystem = InMemoryFilesystem()
        filesystem.mkdir(repo_path / ".sds" / "logs", parents=True)
        config = Config(
            agent=AgentConfig(backend="codex", model_config=ModelConfig(provider="openai", model="test-model"))
        )
        ctx = _make_ctx(repo_path, filesystem, config)
        ctx.invoke = Mock(
            return_value=AgentResult(text="", messages=[], structured=FixSummaryResponse(summary="Summary text"))
        )

        with patch("app_operator.langgraph.nodes.deployer.prepare_error_context") as mock_prepare:
            mock_prepare.return_value = {}

            fix_errors(state, ctx, None)

            # Verify observable outcome: log file exists with expected content
            log_path = repo_path / ".sds" / "logs" / "fix_summary_1.log"
            assert filesystem.exists(log_path)
            log_content = filesystem.read_text(log_path)
            assert "Summary text" in log_content

    def test_fix_errors_includes_health_result_context(self):
        """Test fix_errors properly handles health check failures.

        Verify observable outcome: fix summary is set correctly for health failures.
        """
        state = OperatorState(  # type: ignore[call-overload]
            attempt=1,
            max_attempts=3,
            messages=[],
            scripts_done=True,
            deploy_result={"exit_code": 0},
            health_verdict={
                "healthy": False,
                "assessment": "Health check failed",
                "diagnosis": "service down",
                "script_was_fixed": False,
            },
        )

        repo_path = Path("/test/repo")
        filesystem = InMemoryFilesystem()
        config = Config(
            agent=AgentConfig(backend="codex", model_config=ModelConfig(provider="openai", model="test-model"))
        )
        ctx = _make_ctx(repo_path, filesystem, config)
        ctx.invoke = Mock(
            return_value=AgentResult(text="", messages=[], structured=FixSummaryResponse(summary="Fixed health check"))
        )

        with patch("app_operator.langgraph.nodes.deployer.prepare_error_context") as mock_prepare:
            mock_prepare.return_value = {"health_error": "Health check failed"}

            result_state = fix_errors(state, ctx, None)

            # Verify observable outcome: attempt should be incremented
            assert result_state["attempt"] == 2

    def test_fix_errors_passes_deployment_progress_path_when_enabled(self):
        """Test fix_errors passes deployment_progress_path to create_fix_prompt when flag enabled."""
        state = OperatorState(  # type: ignore[call-overload]
            attempt=1,
            max_attempts=3,
            messages=[],
            scripts_done=True,
            deploy_result={"exit_code": 1},
            health_verdict=None,
        )

        repo_path = Path("/test/repo")
        filesystem = InMemoryFilesystem()
        phase = OperatorPhaseConfig(fix_summary_consolidation=True)
        config = Config(
            agent=AgentConfig(backend="codex", model_config=ModelConfig(provider="openai", model="test-model")),
            operator=OperatorConfig(phase=phase),
        )
        ctx = _make_ctx(repo_path, filesystem, config)
        ctx.invoke = Mock(
            return_value=AgentResult(text="", messages=[], structured=FixSummaryResponse(summary="Fixed"))
        )

        with patch("app_operator.langgraph.nodes.deployer.prepare_error_context", return_value={}):
            with patch("app_operator.langgraph.nodes.deployer.create_fix_prompt", return_value="prompt") as mock_prompt:
                fix_errors(state, ctx, None)

        call_kwargs = mock_prompt.call_args[1]
        assert call_kwargs["deployment_progress_path"] == repo_path / ".sds" / DEPLOYMENT_PROGRESS_FILENAME

    def test_fix_errors_does_not_pass_progress_path_when_disabled(self):
        """Test fix_errors passes None for deployment_progress_path when flag disabled."""
        state = OperatorState(  # type: ignore[call-overload]
            attempt=1,
            max_attempts=3,
            messages=[],
            scripts_done=True,
            deploy_result={"exit_code": 1},
            health_verdict=None,
        )

        repo_path = Path("/test/repo")
        filesystem = InMemoryFilesystem()
        phase = OperatorPhaseConfig(fix_summary_consolidation=False)
        config = Config(
            agent=AgentConfig(backend="codex", model_config=ModelConfig(provider="openai", model="test-model")),
            operator=OperatorConfig(phase=phase),
        )
        ctx = _make_ctx(repo_path, filesystem, config)
        ctx.invoke = Mock(
            return_value=AgentResult(text="", messages=[], structured=FixSummaryResponse(summary="Fixed"))
        )

        with patch("app_operator.langgraph.nodes.deployer.prepare_error_context", return_value={}):
            with patch("app_operator.langgraph.nodes.deployer.create_fix_prompt", return_value="prompt") as mock_prompt:
                fix_errors(state, ctx, None)

        call_kwargs = mock_prompt.call_args[1]
        assert call_kwargs["deployment_progress_path"] is None
