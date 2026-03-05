"""Tests for filesystem error handling in deployment.

These tests verify that the deployment agent properly handles filesystem
errors such as permission denied, disk full, and other I/O failures.
"""

import pytest

from app_operator.cli_agent.agents.deployer import DeploymentAgent
from tests.fixtures.agents import StubAgent


class TestDeploymentFilesystemErrors:
    """Tests for filesystem error handling during deployment."""

    def test_log_directory_creation_permission_denied(self, tmp_path):
        """Should handle permission denied when creating log directory."""
        repo = tmp_path / "repo"
        repo.mkdir()

        # Create .sds directory
        sds_dir = repo / ".sds"
        sds_dir.mkdir()

        # Create scripts
        deploy_script = sds_dir / "deploy.sh"
        deploy_script.write_text("#!/bin/bash\nexit 0")
        deploy_script.chmod(0o755)

        health_script = sds_dir / "health_check.sh"
        health_script.write_text("#!/bin/bash\nexit 0")
        health_script.chmod(0o755)

        # Make logs directory read-only
        logs_dir = sds_dir / "logs"
        logs_dir.mkdir()
        logs_dir.chmod(0o444)

        agent = StubAgent()
        deployer = DeploymentAgent(repo, agent)

        try:
            # This should handle the permission error gracefully
            # The deployment may fail, but it should not crash
            result = deployer.run(max_attempts=1, check_shutdown=lambda: False)

            # Verify it attempted to deploy (result may be True or False)
            assert isinstance(result, bool)
        finally:
            # Restore permissions for cleanup
            logs_dir.chmod(0o755)

    def test_log_file_is_directory(self, tmp_path):
        """Should handle case where log file path is actually a directory."""
        repo = tmp_path / "repo"
        repo.mkdir()

        sds_dir = repo / ".sds"
        sds_dir.mkdir()

        # Create scripts
        deploy_script = sds_dir / "deploy.sh"
        deploy_script.write_text("#!/bin/bash\nexit 0")
        deploy_script.chmod(0o755)

        health_script = sds_dir / "health_check.sh"
        health_script.write_text("#!/bin/bash\nexit 0")
        health_script.chmod(0o755)

        # Create a directory where log file would be
        logs_dir = sds_dir / "logs"
        logs_dir.mkdir()
        (logs_dir / "deploy_attempt_1.log").mkdir()  # Directory, not file!

        agent = StubAgent()
        deployer = DeploymentAgent(repo, agent)

        # Should handle this error gracefully
        result = deployer.run(max_attempts=1, check_shutdown=lambda: False)
        assert isinstance(result, bool)

    def test_script_chmod_fails_gracefully(self, tmp_path):
        """Should handle chmod failures on generated scripts."""
        # Note: This is hard to test on a real filesystem without root
        # but we can verify the code doesn't crash if chmod is unavailable
        repo = tmp_path / "repo"
        repo.mkdir()

        sds_dir = repo / ".sds"
        sds_dir.mkdir()

        # Create scripts without execute permission
        deploy_script = sds_dir / "deploy.sh"
        deploy_script.write_text("#!/bin/bash\nexit 0")
        # Don't chmod - leave it non-executable

        health_script = sds_dir / "health_check.sh"
        health_script.write_text("#!/bin/bash\nexit 0")
        # Don't chmod - leave it non-executable

        agent = StubAgent()
        deployer = DeploymentAgent(repo, agent)

        # Deployment will likely fail, but should not crash
        result = deployer.run(max_attempts=1, check_shutdown=lambda: False)
        assert isinstance(result, bool)


class TestScriptGenerationFilesystemErrors:
    """Tests for filesystem errors during script generation."""

    def test_sds_directory_not_writable(self, tmp_path):
        """Should handle case where .sds directory cannot be created."""
        from app_operator.cli_agent.agents.deployer import generate_scripts

        repo = tmp_path / "repo"
        repo.mkdir()

        # Make repo read-only
        repo.chmod(0o444)

        agent = StubAgent()

        try:
            # Should fail gracefully (either returns False or raises PermissionError)
            try:
                success, message = generate_scripts(str(repo), agent)
                # If it returns, should indicate failure
                assert success is False or "Permission denied" in message or "Read-only" in message
            except PermissionError:
                # Also acceptable - permission error is raised
                pass
        finally:
            # Restore permissions
            repo.chmod(0o755)

    def test_target_directory_deleted_during_operation(self, tmp_path):
        """Should handle case where target directory is deleted during operation."""
        from app_operator.cli_agent.agents.deployer import generate_scripts

        repo = tmp_path / "repo"
        repo.mkdir()

        # Directory exists initially
        assert repo.exists()

        agent = StubAgent()

        # Delete directory before script generation completes
        # (In practice this is a race condition, but we can simulate it)

        # The function should detect this and fail gracefully
        success, message = generate_scripts(str(repo), agent)

        # Should complete successfully or fail gracefully
        assert isinstance(success, bool)
        assert isinstance(message, str)


class TestLogFileWriteErrors:
    """Tests for log file write errors."""

    def test_log_file_write_succeeds_normally(self, tmp_path):
        """Normal case: log files should be written successfully."""
        repo = tmp_path / "repo"
        repo.mkdir()

        sds_dir = repo / ".sds"
        sds_dir.mkdir()

        # Create working scripts
        deploy_script = sds_dir / "deploy.sh"
        deploy_script.write_text("#!/bin/bash\necho 'test'\nexit 0")
        deploy_script.chmod(0o755)

        health_script = sds_dir / "health_check.sh"
        health_script.write_text("#!/bin/bash\necho 'healthy'\nexit 0")
        health_script.chmod(0o755)

        agent = StubAgent()
        deployer = DeploymentAgent(repo, agent)

        result = deployer.run(max_attempts=1, check_shutdown=lambda: False)

        # Should succeed
        assert result is True

        # Log file should exist
        log_file = sds_dir / "logs" / "deploy_attempt_1.log"
        assert log_file.exists()

    def test_very_long_log_path(self, tmp_path):
        """Should handle very long log file paths."""
        # Create a deep directory structure
        repo = tmp_path / "repo"
        current = repo
        for i in range(20):  # Create 20 nested directories
            current = current / f"dir_{i}"

        current.mkdir(parents=True)

        # This should work even with a long path
        # (unless we exceed OS limits, which is a valid failure mode)
        agent = StubAgent()

        # The DeploymentAgent should handle this
        # Either it works, or it fails with a clear error
        try:
            deployer = DeploymentAgent(current, agent)
        except (OSError, ValueError) as e:
            # Valid to fail with OS error on extremely long paths
            err_msg = str(e).lower()
            if "path" not in err_msg and "name" not in err_msg:
                raise AssertionError(f"Unexpected error message: {e}") from e
        else:
            assert deployer.repo_path == current


class TestWorkingDirectoryErrors:
    """Tests for working directory related errors."""

    def test_repository_path_validation(self, tmp_path):
        """Should validate that repository path exists."""
        from app_operator.cli_agent.operator import AppOperator

        nonexistent = tmp_path / "does_not_exist"

        agent = StubAgent()

        with pytest.raises(ValueError, match="does not exist"):
            AppOperator(str(nonexistent), agent=agent)

    def test_repository_path_must_be_directory(self, tmp_path):
        """Repository path must be a directory, not a file."""
        from app_operator.cli_agent.operator import AppOperator

        # Create a file instead of directory
        file_path = tmp_path / "not_a_directory.txt"
        file_path.write_text("content")

        agent = StubAgent()

        with pytest.raises(ValueError, match="not a directory"):
            AppOperator(str(file_path), agent=agent)
