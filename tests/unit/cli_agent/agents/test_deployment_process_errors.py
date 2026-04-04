"""Tests for process execution errors and timeouts.

These tests verify proper handling of subprocess timeouts, zombie processes,
and edge cases in process execution.
"""

import time

from app_operator.cli_agent.agents.deployer import DeploymentAgent
from tests.fixtures.agents import StubAgent


class TestDeploymentProcessTimeouts:
    """Tests for deployment process timeout handling."""

    def test_deployment_timeout_captures_partial_output(self, tmp_path):
        """When deployment times out, partial output should be captured.

        Uses optimized sleep times to speed up the test.
        """
        repo = tmp_path / "repo"
        repo.mkdir()

        sds_dir = repo / ".sds"
        sds_dir.mkdir()

        # Create a deploy script that runs longer than timeout
        deploy_script = sds_dir / "deploy.sh"
        deploy_script.write_text(
            "#!/bin/bash\n"
            "echo 'Starting deployment'\n"
            "sleep 0.3\n"  # Longer than timeout
            "echo 'This should not appear'\n"
            "exit 0\n"
        )
        deploy_script.chmod(0o755)

        health_script = sds_dir / "health_check.sh"
        health_script.write_text("#!/bin/bash\nexit 0")
        health_script.chmod(0o755)

        agent = StubAgent()
        deployer = DeploymentAgent(repo, agent)

        # Run with very short timeout
        result = deployer.run_deploy_command("start", timeout=0.1)  # type: ignore[arg-type]

        # Should have timed out
        assert result["success"] is False
        assert result["exit_code"] == -1

        # Should have captured partial output
        assert "Starting deployment" in result["stdout"] or "Starting deployment" in result["stderr"]
        assert "timed out" in result["stderr"]

    def test_deployment_completes_just_before_timeout(self, tmp_path):
        """Deployment completing just before timeout should succeed."""
        repo = tmp_path / "repo"
        repo.mkdir()

        sds_dir = repo / ".sds"
        sds_dir.mkdir()

        # Create a deploy script that completes quickly
        deploy_script = sds_dir / "deploy.sh"
        deploy_script.write_text("#!/bin/bash\necho 'Quick deployment'\nsleep 0.2\necho 'Done'\nexit 0\n")
        deploy_script.chmod(0o755)

        agent = StubAgent()
        deployer = DeploymentAgent(repo, agent)

        # Run with generous timeout
        result = deployer.run_deploy_command("start", timeout=1)

        # Should succeed
        assert result["success"] is True
        assert result["exit_code"] == 0
        assert "Done" in result["stdout"]

    def test_process_cleanup_after_timeout(self, tmp_path):
        """Process should be properly terminated after timeout."""
        repo = tmp_path / "repo"
        repo.mkdir()

        sds_dir = repo / ".sds"
        sds_dir.mkdir()

        # Create a script that tries to keep running
        deploy_script = sds_dir / "deploy.sh"
        deploy_script.write_text(
            "#!/bin/bash\n"
            "handler() {\n"
            "    echo 'Caught signal'\n"
            '    if [ -n "$PID" ]; then kill $PID; fi\n'
            "    exit 1\n"
            "}\n"
            "trap handler TERM INT\n"
            "echo 'Starting'\n"
            "sleep 100 &\n"
            "PID=$!\n"
            "wait $PID\n"
            "echo 'Should not reach here'\n"
        )
        deploy_script.chmod(0o755)

        agent = StubAgent()
        deployer = DeploymentAgent(repo, agent)

        start_time = time.time()
        result = deployer.run_deploy_command("start", timeout=0.5)  # type: ignore[arg-type]
        elapsed = time.time() - start_time

        # Should have timed out quickly (not waited 100 seconds)
        assert elapsed < 2  # Should be ~0.5 seconds + cleanup time
        assert result["success"] is False

    def test_deployment_with_zero_timeout_fails(self, tmp_path):
        """Deployment with zero timeout should fail immediately."""
        repo = tmp_path / "repo"
        repo.mkdir()

        sds_dir = repo / ".sds"
        sds_dir.mkdir()

        deploy_script = sds_dir / "deploy.sh"
        deploy_script.write_text("#!/bin/bash\nexit 0")
        deploy_script.chmod(0o755)

        agent = StubAgent()
        deployer = DeploymentAgent(repo, agent)

        # Zero timeout should fail immediately
        result = deployer.run_deploy_command("start", timeout=0)

        assert result["success"] is False


class TestHealthCheckProcessErrors:
    """Tests for health check process errors."""

    def test_health_check_timeout(self, tmp_path):
        """Health check that times out should be handled properly."""
        from app_operator.healthcheck import run_health_check

        repo = tmp_path / "repo"
        repo.mkdir()

        sds_dir = repo / ".sds"
        sds_dir.mkdir()

        # Create a health check that runs too long
        health_script = sds_dir / "health_check.sh"
        health_script.write_text(
            "#!/bin/bash\n"
            "echo 'Checking health...'\n"
            "sleep 200\n"  # Very long
            "exit 0\n"
        )
        health_script.chmod(0o755)

        # Run with short timeout
        start_time = time.time()
        result = run_health_check(repo, health_script, timeout=0.5)  # type: ignore[arg-type]
        elapsed = time.time() - start_time

        # Should timeout quickly
        assert elapsed < 2
        assert result["success"] is False


class TestProcessExecutionEdgeCases:
    """Tests for edge cases in process execution."""

    def test_script_returns_nonzero_exit_code(self, tmp_path):
        """Script with non-zero exit code should be detected."""
        repo = tmp_path / "repo"
        repo.mkdir()

        sds_dir = repo / ".sds"
        sds_dir.mkdir()

        # Script that fails with specific exit code
        deploy_script = sds_dir / "deploy.sh"
        deploy_script.write_text("#!/bin/bash\necho 'Failing with exit code 42'\nexit 42\n")
        deploy_script.chmod(0o755)

        agent = StubAgent()
        deployer = DeploymentAgent(repo, agent)

        result = deployer.run_deploy_command("start", timeout=5)

        assert result["success"] is False
        assert result["exit_code"] == 42

    def test_script_with_stderr_output(self, tmp_path):
        """Script stderr output should be captured."""
        repo = tmp_path / "repo"
        repo.mkdir()

        sds_dir = repo / ".sds"
        sds_dir.mkdir()

        deploy_script = sds_dir / "deploy.sh"
        deploy_script.write_text("#!/bin/bash\necho 'stdout message'\necho 'stderr message' >&2\nexit 0\n")
        deploy_script.chmod(0o755)

        agent = StubAgent()
        deployer = DeploymentAgent(repo, agent)

        result = deployer.run_deploy_command("start", timeout=5)

        assert result["success"] is True
        assert "stdout message" in result["stdout"]
        assert "stderr message" in result["stderr"]

    def test_script_does_not_exist(self, tmp_path):
        """Attempting to run non-existent script should fail gracefully."""
        repo = tmp_path / "repo"
        repo.mkdir()

        sds_dir = repo / ".sds"
        sds_dir.mkdir()

        # Don't create deploy script

        agent = StubAgent()
        deployer = DeploymentAgent(repo, agent)

        # Should handle missing script gracefully
        result = deployer.run_deploy_command("start", timeout=5)

        assert result["success"] is False
        assert "No such file" in result["stderr"] or "not found" in result["stderr"].lower()

    def test_script_not_executable(self, tmp_path):
        """Script without execute permission should fail."""
        repo = tmp_path / "repo"
        repo.mkdir()

        sds_dir = repo / ".sds"
        sds_dir.mkdir()

        # Create script without execute permission
        deploy_script = sds_dir / "deploy.sh"
        deploy_script.write_text("#!/bin/bash\necho 'test'\nexit 0")
        deploy_script.chmod(0o644)  # No execute permission

        agent = StubAgent()
        deployer = DeploymentAgent(repo, agent)

        result = deployer.run_deploy_command("start", timeout=5)

        # Should fail with permission error
        assert result["success"] is False
        assert "Permission denied" in result["stderr"] or "permission" in result["stderr"].lower()
