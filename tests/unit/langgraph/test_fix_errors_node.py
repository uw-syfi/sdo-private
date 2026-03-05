"""Tests for fix_errors node function."""
from pathlib import Path
from unittest.mock import Mock, patch

from app_operator.config import AgentConfig, Config
from app_operator.filesystem import InMemoryFilesystem
from app_operator.trajectory import NullTrajectoryRecorder
from app_operator.langgraph.state import OperatorState
from app_operator.langgraph.nodes.deployer import fix_errors


class TestFixErrors:
    """Test fix_errors node function."""

    def test_fix_errors_with_deploy_failure(self):
        """Test fix_errors processes deployment errors."""
        state = OperatorState(
            attempt=1,
            max_attempts=3,
            messages=[],
            scripts_done=True,
            deploy_result={"exit_code": 1, "stderr": "Error occurred"},
            health_result=None,
            last_fix_summary=None
        )

        repo_path = Path("/test/repo")
        filesystem = InMemoryFilesystem()
        config = Config(agent=AgentConfig(provider="codex", model="test-model"))
        loader = Mock()
        agent = Mock()
        check_shutdown = Mock(return_value=False)
        recorder = Mock(spec=NullTrajectoryRecorder())
        recorder.end_phase = Mock()

        with patch("app_operator.langgraph.nodes.deployer.invoke_agent") as mock_invoke:
            mock_invoke.return_value = (
                "<summary>Fixed the issue</summary>", [])

            with patch("app_operator.langgraph.nodes.deployer.prepare_error_context") as mock_prepare:
                mock_prepare.return_value = {"error": "Error occurred"}

                result_state = fix_errors(
                    state, repo_path, filesystem, config, loader,
                    agent, 10000, check_shutdown, recorder
                )

        assert result_state["attempt"] == 2  # Incremented
        assert result_state["last_fix_summary"] == "Fixed the issue"
        recorder.end_phase.assert_called_once_with("needs_retry")

    def test_fix_errors_extracts_summary(self):
        """Test fix_errors extracts summary from response."""
        state = OperatorState(
            attempt=1,
            max_attempts=3,
            messages=[],
            scripts_done=True,
            deploy_result={"exit_code": 1},
            health_result=None,
            last_fix_summary=None
        )

        repo_path = Path("/test/repo")
        filesystem = InMemoryFilesystem()
        config = Config(agent=AgentConfig(provider="codex", model="test-model"))
        loader = Mock()
        agent = Mock()

        with patch("app_operator.langgraph.nodes.deployer.invoke_agent") as mock_invoke:
            mock_invoke.return_value = (
                "Some text\n<summary>This is the summary</summary>\nMore text",
                []
            )

            with patch("app_operator.langgraph.nodes.deployer.prepare_error_context") as mock_prepare:
                mock_prepare.return_value = {}

                result_state = fix_errors(
                    state, repo_path, filesystem, config, loader,
                    agent, 10000, None
                )

        assert result_state["last_fix_summary"] == "This is the summary"

    def test_fix_errors_handles_escaped_characters(self):
        """Test fix_errors handles escaped characters in summary."""
        state = OperatorState(
            attempt=1,
            max_attempts=3,
            messages=[],
            scripts_done=True,
            deploy_result={"exit_code": 1},
            health_result=None,
            last_fix_summary=None
        )

        repo_path = Path("/test/repo")
        filesystem = InMemoryFilesystem()
        config = Config(agent=AgentConfig(provider="codex", model="test-model"))
        loader = Mock()
        agent = Mock()

        with patch("app_operator.langgraph.nodes.deployer.invoke_agent") as mock_invoke:
            mock_invoke.return_value = (
                "<summary>Line1\\nLine2\\tTabbed</summary>",
                []
            )

            with patch("app_operator.langgraph.nodes.deployer.prepare_error_context") as mock_prepare:
                mock_prepare.return_value = {}

                result_state = fix_errors(
                    state, repo_path, filesystem, config, loader,
                    agent, 10000, None
                )

        assert result_state["last_fix_summary"] == "Line1\nLine2\tTabbed"

    def test_fix_errors_no_summary_in_response(self):
        """Test fix_errors when no summary tag in response."""
        state = OperatorState(
            attempt=1,
            max_attempts=3,
            messages=[],
            scripts_done=True,
            deploy_result={"exit_code": 1},
            health_result=None,
            last_fix_summary=None
        )

        repo_path = Path("/test/repo")
        filesystem = InMemoryFilesystem()
        config = Config(agent=AgentConfig(provider="codex", model="test-model"))
        loader = Mock()
        agent = Mock()

        with patch("app_operator.langgraph.nodes.deployer.invoke_agent") as mock_invoke:
            mock_invoke.return_value = ("No summary here", [])

            with patch("app_operator.langgraph.nodes.deployer.prepare_error_context") as mock_prepare:
                mock_prepare.return_value = {}

                result_state = fix_errors(
                    state, repo_path, filesystem, config, loader,
                    agent, 10000, None
                )

        # last_fix_summary should remain None
        assert result_state["last_fix_summary"] is None

    def test_fix_errors_respects_shutdown(self):
        """Test fix_errors respects shutdown flag.

        Verify observable outcome: state is unchanged when shutdown is signaled.
        """
        state = OperatorState(
            attempt=1,
            max_attempts=3,
            messages=[],
            scripts_done=True,
            deploy_result={"exit_code": 1},
            health_result=None,
            last_fix_summary=None
        )

        repo_path = Path("/test/repo")
        filesystem = InMemoryFilesystem()
        config = Config(agent=AgentConfig(provider="codex", model="test-model"))
        loader = Mock()
        agent = Mock()
        check_shutdown = Mock(return_value=True)

        result_state = fix_errors(
            state, repo_path, filesystem, config, loader,
            agent, 10000, check_shutdown
        )

        # Verify observable outcome: state should be unchanged
        assert result_state == state
        # Attempt should not be incremented
        assert result_state["attempt"] == 1
        # No fix summary should be added
        assert result_state["last_fix_summary"] is None

    def test_fix_errors_writes_summary_to_log_file(self):
        """Test fix_errors writes summary to log file.

        Verify observable outcome: summary is written to the expected log file.
        """
        state = OperatorState(
            attempt=1,
            max_attempts=3,
            messages=[],
            scripts_done=True,
            deploy_result={"exit_code": 1},
            health_result=None,
            last_fix_summary=None
        )

        repo_path = Path("/test/repo")
        filesystem = InMemoryFilesystem()
        filesystem.mkdir(repo_path / ".sds" / "logs", parents=True)
        config = Config(agent=AgentConfig(provider="codex", model="test-model"))
        loader = Mock()
        agent = Mock()

        with patch("app_operator.langgraph.nodes.deployer.invoke_agent") as mock_invoke:
            mock_invoke.return_value = ("<summary>Summary text</summary>", [])

            with patch("app_operator.langgraph.nodes.deployer.prepare_error_context") as mock_prepare:
                mock_prepare.return_value = {}

                fix_errors(
                    state, repo_path, filesystem, config, loader,
                    agent, 10000, None
                )

                # Verify observable outcome: log file exists with expected content
                log_path = repo_path / ".sds" / "logs" / "fix_summary_1.log"
                assert filesystem.exists(log_path)
                log_content = filesystem.read_text(log_path)
                assert "Summary text" in log_content

    def test_fix_errors_includes_health_result_context(self):
        """Test fix_errors properly handles health check failures.

        Verify observable outcome: fix summary is set correctly for health failures.
        """
        state = OperatorState(
            attempt=1,
            max_attempts=3,
            messages=[],
            scripts_done=True,
            deploy_result={"exit_code": 0},
            health_result={"exit_code": 1, "stderr": "Health check failed"},
            last_fix_summary=None
        )

        repo_path = Path("/test/repo")
        filesystem = InMemoryFilesystem()
        config = Config(agent=AgentConfig(provider="codex", model="test-model"))
        loader = Mock()
        agent = Mock()

        with patch("app_operator.langgraph.nodes.deployer.invoke_agent") as mock_invoke:
            mock_invoke.return_value = (
                "<summary>Fixed health check</summary>", [])

            with patch("app_operator.langgraph.nodes.deployer.prepare_error_context") as mock_prepare:
                mock_prepare.return_value = {
                    "health_error": "Health check failed"}

                result_state = fix_errors(
                    state, repo_path, filesystem, config, loader,
                    agent, 10000, None
                )

                # Verify observable outcomes:
                # 1. Fix summary should be extracted and set
                assert result_state["last_fix_summary"] == "Fixed health check"
                # 2. Attempt should be incremented
                assert result_state["attempt"] == 2
