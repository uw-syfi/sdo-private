"""Tests verifying custom exceptions are raised at their natural sites.

Each exception class in app_operator/exceptions.py must be raised somewhere
in production code.  These tests exercise the raise sites for ProcessError,
DeploymentError, and FileSystemError.
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app_operator.core import (
    DeploymentError,
    FileSystemError,
    ProcessError,
    SdsOperatorError,
)

# ---------------------------------------------------------------------------
# ProcessError — raised by script_runner.run_script on subprocess failures
# ---------------------------------------------------------------------------


class TestProcessErrorInScriptRunner:
    """ProcessError is raised on timeout and OS/subprocess errors."""

    def test_timeout_raises_process_error(self, tmp_path):
        from app_operator.core import RealFilesystem
        from app_operator.script_runner import run_script

        repo = tmp_path / "repo"
        repo.mkdir()

        with pytest.raises(ProcessError) as exc_info:
            run_script(
                repo,
                RealFilesystem(),
                "sleep 60",
                timeout=0,
            )

        assert exc_info.value.timeout is True
        assert exc_info.value.exit_code == -1
        assert isinstance(exc_info.value, SdsOperatorError)

    def test_missing_command_raises_process_error(self, tmp_path):
        from app_operator.core import RealFilesystem
        from app_operator.script_runner import run_script

        repo = tmp_path / "nonexistent_dir_abc123"

        with pytest.raises(ProcessError) as exc_info:
            run_script(
                repo,
                RealFilesystem(),
                "echo hello",
            )

        assert exc_info.value.exit_code == -1
        assert exc_info.value.timeout is False

    def test_successful_run_returns_command_result(self, tmp_path):
        from app_operator.core import RealFilesystem
        from app_operator.script_runner import run_script

        repo = tmp_path / "repo"
        repo.mkdir()

        result = run_script(
            repo,
            RealFilesystem(),
            "echo hello",
        )

        assert result["success"] is True
        assert result["exit_code"] == 0
        assert "hello" in result["stdout"]

    def test_nonzero_exit_returns_result_not_exception(self, tmp_path):
        """Non-zero exit code is a normal failure, not an exception."""
        from app_operator.core import RealFilesystem
        from app_operator.script_runner import run_script

        repo = tmp_path / "repo"
        repo.mkdir()

        result = run_script(
            repo,
            RealFilesystem(),
            "exit 42",
        )

        assert result["success"] is False
        assert result["exit_code"] == 42

    def test_log_file_still_written_on_process_error(self, tmp_path):
        """Even when ProcessError is raised, the log file should be written."""
        from app_operator.core import RealFilesystem
        from app_operator.script_runner import run_script

        repo = tmp_path / "repo"
        repo.mkdir()
        log_file = tmp_path / "logs" / "test.log"

        with pytest.raises(ProcessError):
            run_script(
                repo,
                RealFilesystem(),
                "sleep 60",
                log_file_path=log_file,
                timeout=0,
            )

        assert log_file.exists()
        content = log_file.read_text()
        assert "timed out" in content


# ---------------------------------------------------------------------------
# DeploymentError — raised by deployer.run() on terminal deployment failure
# ---------------------------------------------------------------------------


class TestDeploymentErrorInDeployer:
    """DeploymentError is raised when the deployment loop exhausts attempts."""

    def test_all_attempts_exhausted_raises_deployment_error(self, tmp_path):
        from app_operator.cli_agent.agents.deployer import DeploymentAgent
        from tests.fixtures.agents import StubAgent

        repo = tmp_path / "repo"
        repo.mkdir()
        sds_dir = repo / ".sds"
        sds_dir.mkdir()

        deploy_script = sds_dir / "deploy.sh"
        deploy_script.write_text("#!/bin/bash\nexit 1\n")
        deploy_script.chmod(0o755)

        health_script = sds_dir / "health_check.sh"
        health_script.write_text("#!/bin/bash\nexit 1\n")
        health_script.chmod(0o755)

        agent = StubAgent()
        deployer = DeploymentAgent(repo, agent)

        with pytest.raises(DeploymentError) as exc_info:
            deployer.run(max_attempts=1, check_shutdown=lambda: False)

        assert exc_info.value.attempt is not None
        assert isinstance(exc_info.value, SdsOperatorError)

    def test_deployment_error_preserves_attempt_number(self, tmp_path):
        from app_operator.cli_agent.agents.deployer import DeploymentAgent
        from tests.fixtures.agents import StubAgent

        repo = tmp_path / "repo"
        repo.mkdir()
        sds_dir = repo / ".sds"
        sds_dir.mkdir()

        deploy_script = sds_dir / "deploy.sh"
        deploy_script.write_text("#!/bin/bash\nexit 1\n")
        deploy_script.chmod(0o755)

        health_script = sds_dir / "health_check.sh"
        health_script.write_text("#!/bin/bash\nexit 1\n")
        health_script.chmod(0o755)

        agent = StubAgent()
        deployer = DeploymentAgent(repo, agent)

        with pytest.raises(DeploymentError) as exc_info:
            deployer.run(max_attempts=2, check_shutdown=lambda: False)

        assert exc_info.value.attempt == 2

    def test_successful_deployment_returns_true(self, tmp_path):
        from app_operator.cli_agent.agents.deployer import DeploymentAgent
        from tests.fixtures.agents import StubAgent

        repo = tmp_path / "repo"
        repo.mkdir()
        sds_dir = repo / ".sds"
        sds_dir.mkdir()

        deploy_script = sds_dir / "deploy.sh"
        deploy_script.write_text("#!/bin/bash\nexit 0\n")
        deploy_script.chmod(0o755)

        health_script = sds_dir / "health_check.sh"
        health_script.write_text("#!/bin/bash\nexit 0\n")
        health_script.chmod(0o755)

        agent = StubAgent()
        deployer = DeploymentAgent(repo, agent)

        result = deployer.run(max_attempts=1, check_shutdown=lambda: False)
        assert result is True

    def test_shutdown_returns_false_not_exception(self, tmp_path):
        """Shutdown requested → returns False, not DeploymentError."""
        from app_operator.cli_agent.agents.deployer import DeploymentAgent
        from tests.fixtures.agents import StubAgent

        repo = tmp_path / "repo"
        repo.mkdir()
        sds_dir = repo / ".sds"
        sds_dir.mkdir()

        deploy_script = sds_dir / "deploy.sh"
        deploy_script.write_text("#!/bin/bash\nexit 1\n")
        deploy_script.chmod(0o755)

        health_script = sds_dir / "health_check.sh"
        health_script.write_text("#!/bin/bash\nexit 0\n")
        health_script.chmod(0o755)

        agent = StubAgent()
        deployer = DeploymentAgent(repo, agent)

        result = deployer.run(max_attempts=5, check_shutdown=lambda: True)
        assert result is False


# ---------------------------------------------------------------------------
# FileSystemError — raised wrapping OSError on filesystem operations
# ---------------------------------------------------------------------------


class TestFileSystemErrorInScriptRunner:
    """FileSystemError wraps OSError in write_log_file."""

    def test_write_log_file_raises_filesystem_error(self):
        from app_operator.script_runner import write_log_file

        fs = MagicMock()
        fs.mkdir.side_effect = OSError("disk full")

        with pytest.raises(FileSystemError, match="disk full"):
            write_log_file(fs, Path("/fake/log.txt"), "content")


class TestFileSystemErrorInScriptGenerator:
    """FileSystemError wraps OSError in script_generator_agent."""

    def test_mkdir_failure_raises_filesystem_error(self, tmp_path):
        from app_operator.cli_agent.agents.context import AgentContext
        from app_operator.cli_agent.agents.script_generator_agent import (
            ScriptGeneratorAgent,
        )
        from tests.fixtures.agents import StubAgent

        repo = tmp_path / "repo"
        repo.mkdir()

        fs = MagicMock()
        fs.exists.return_value = True
        fs.is_dir.return_value = True
        fs.mkdir.side_effect = OSError("permission denied")

        agent = StubAgent()
        ctx = AgentContext(
            repo_path=repo,
            coding_agent=agent,
            filesystem=fs,
        )
        gen = ScriptGeneratorAgent(ctx)

        with pytest.raises(FileSystemError, match="permission denied"):
            gen.generate_scripts()
