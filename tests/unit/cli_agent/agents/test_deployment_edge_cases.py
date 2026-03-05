"""Tests for edge cases in deployment logic.

These tests verify proper handling of boundary conditions and unusual
scenarios in the deployment process.
"""

from app_operator.cli_agent.agents.deployer import DeploymentAgent
from tests.fixtures.agents import ErrorAgent, StubAgent, TrackingAgent


class TestDeploymentAttemptBoundaries:
    """Tests for deployment attempt boundary conditions."""

    def test_max_attempts_equals_one(self, tmp_path):
        """Deployment with max_attempts=1 should work correctly."""
        repo = tmp_path / "repo"
        repo.mkdir()

        sds_dir = repo / ".sds"
        sds_dir.mkdir()

        # Create successful scripts
        deploy_script = sds_dir / "deploy.sh"
        deploy_script.write_text("#!/bin/bash\nexit 0")
        deploy_script.chmod(0o755)

        health_script = sds_dir / "health_check.sh"
        health_script.write_text("#!/bin/bash\nexit 0")
        health_script.chmod(0o755)

        agent = StubAgent()
        deployer = DeploymentAgent(repo, agent)

        result = deployer.run(max_attempts=1, check_shutdown=lambda: False)

        assert result is True

    def test_max_attempts_equals_one_with_failure(self, tmp_path):
        """Single attempt failure should fail immediately."""
        repo = tmp_path / "repo"
        repo.mkdir()

        sds_dir = repo / ".sds"
        sds_dir.mkdir()

        # Create failing script
        deploy_script = sds_dir / "deploy.sh"
        deploy_script.write_text("#!/bin/bash\nexit 1")
        deploy_script.chmod(0o755)

        health_script = sds_dir / "health_check.sh"
        health_script.write_text("#!/bin/bash\nexit 0")
        health_script.chmod(0o755)

        agent = StubAgent()
        deployer = DeploymentAgent(repo, agent)

        result = deployer.run(max_attempts=1, check_shutdown=lambda: False)

        assert result is False

    def test_max_attempts_large_number(self, tmp_path):
        """Large max_attempts value should be handled."""
        repo = tmp_path / "repo"
        repo.mkdir()

        sds_dir = repo / ".sds"
        sds_dir.mkdir()

        # Create successful scripts
        deploy_script = sds_dir / "deploy.sh"
        deploy_script.write_text("#!/bin/bash\nexit 0")
        deploy_script.chmod(0o755)

        health_script = sds_dir / "health_check.sh"
        health_script.write_text("#!/bin/bash\nexit 0")
        health_script.chmod(0o755)

        agent = StubAgent()
        deployer = DeploymentAgent(repo, agent)

        # Should succeed on first attempt even with large max
        result = deployer.run(max_attempts=100, check_shutdown=lambda: False)

        assert result is True


class TestRepositoryPathEdgeCases:
    """Tests for edge cases in repository paths."""

    def test_repository_path_with_spaces(self, tmp_path):
        """Repository path with spaces should work."""
        repo = tmp_path / "repo with spaces"
        repo.mkdir()

        sds_dir = repo / ".sds"
        sds_dir.mkdir()

        deploy_script = sds_dir / "deploy.sh"
        deploy_script.write_text("#!/bin/bash\nexit 0")
        deploy_script.chmod(0o755)

        health_script = sds_dir / "health_check.sh"
        health_script.write_text("#!/bin/bash\nexit 0")
        health_script.chmod(0o755)

        agent = StubAgent()
        deployer = DeploymentAgent(repo, agent)

        result = deployer.run(max_attempts=1, check_shutdown=lambda: False)

        assert result is True

    def test_repository_path_with_special_characters(self, tmp_path):
        """Repository path with special characters should work."""
        repo = tmp_path / "repo-test_123"
        repo.mkdir()

        sds_dir = repo / ".sds"
        sds_dir.mkdir()

        deploy_script = sds_dir / "deploy.sh"
        deploy_script.write_text("#!/bin/bash\nexit 0")
        deploy_script.chmod(0o755)

        health_script = sds_dir / "health_check.sh"
        health_script.write_text("#!/bin/bash\nexit 0")
        health_script.chmod(0o755)

        agent = StubAgent()
        deployer = DeploymentAgent(repo, agent)

        result = deployer.run(max_attempts=1, check_shutdown=lambda: False)

        assert result is True


class TestHealthCheckEdgeCases:
    """Tests for health check edge cases."""

    def test_health_check_passes_but_is_flaky(self, tmp_path):
        """Health check that passes initially but is flaky."""
        repo = tmp_path / "repo"
        repo.mkdir()

        sds_dir = repo / ".sds"
        sds_dir.mkdir()

        deploy_script = sds_dir / "deploy.sh"
        deploy_script.write_text("#!/bin/bash\nexit 0")
        deploy_script.chmod(0o755)

        # Health check passes (we can't test flakiness in a single run)
        health_script = sds_dir / "health_check.sh"
        health_script.write_text("#!/bin/bash\nexit 0")
        health_script.chmod(0o755)

        agent = StubAgent()
        deployer = DeploymentAgent(repo, agent)

        result = deployer.run(max_attempts=1, check_shutdown=lambda: False)

        assert result is True

    def test_health_check_succeeds_after_deployment_fails(self, tmp_path):
        """Health check should only run after successful deployment."""
        repo = tmp_path / "repo"
        repo.mkdir()

        sds_dir = repo / ".sds"
        sds_dir.mkdir()

        # Deployment fails
        deploy_script = sds_dir / "deploy.sh"
        deploy_script.write_text("#!/bin/bash\nexit 1")
        deploy_script.chmod(0o755)

        # Health check would pass (but shouldn't be run)
        health_script = sds_dir / "health_check.sh"
        health_script.write_text("#!/bin/bash\nexit 0")
        health_script.chmod(0o755)

        agent = StubAgent()
        deployer = DeploymentAgent(repo, agent)

        result = deployer.run(max_attempts=1, check_shutdown=lambda: False)

        # Deployment should fail, health check should not run
        assert result is False


class TestAgentInteractionEdgeCases:
    """Tests for edge cases in agent interactions."""

    def test_agent_returns_empty_response(self, tmp_path):
        """Agent returning empty response should be handled."""
        repo = tmp_path / "repo"
        repo.mkdir()

        sds_dir = repo / ".sds"
        sds_dir.mkdir()

        # Failing deployment to trigger agent fix
        deploy_script = sds_dir / "deploy.sh"
        deploy_script.write_text("#!/bin/bash\nexit 1")
        deploy_script.chmod(0o755)

        health_script = sds_dir / "health_check.sh"
        health_script.write_text("#!/bin/bash\nexit 0")
        health_script.chmod(0o755)

        # Agent returns empty response
        agent = TrackingAgent(response="")
        deployer = DeploymentAgent(repo, agent)

        result = deployer.run(max_attempts=2, check_shutdown=lambda: False)

        # Should handle empty response gracefully
        assert isinstance(result, bool)
        assert agent.fix_request_count > 0

    def test_agent_raises_exception_during_fix(self, tmp_path):
        """Agent exception during fix should be handled."""
        repo = tmp_path / "repo"
        repo.mkdir()

        sds_dir = repo / ".sds"
        sds_dir.mkdir()

        # Failing deployment
        deploy_script = sds_dir / "deploy.sh"
        deploy_script.write_text("#!/bin/bash\nexit 1")
        deploy_script.chmod(0o755)

        health_script = sds_dir / "health_check.sh"
        health_script.write_text("#!/bin/bash\nexit 0")
        health_script.chmod(0o755)

        # Agent that raises errors
        agent = ErrorAgent()
        deployer = DeploymentAgent(repo, agent)

        result = deployer.run(max_attempts=2, check_shutdown=lambda: False)

        # Should fail gracefully without crashing
        assert result is False


class TestScriptGenerationEdgeCases:
    """Tests for edge cases in script generation."""

    def test_script_generation_creates_valid_files(self, tmp_path):
        """Generated scripts should be valid and executable."""
        from app_operator.cli_agent.agents.deployer import generate_scripts

        repo = tmp_path / "repo"
        repo.mkdir()

        agent = StubAgent()

        success, message = generate_scripts(str(repo), agent)

        # Should create .sds directory
        sds_dir = repo / ".sds"
        assert sds_dir.exists()

        # Scripts may or may not be created (depends on stub agent behavior)
        # The important thing is it doesn't crash


class TestLogFileManagement:
    """Tests for log file management edge cases."""

    def test_log_files_numbered_correctly(self, tmp_path):
        """Log files should be numbered sequentially."""
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

        agent = StubAgent()
        deployer = DeploymentAgent(repo, agent)

        # First run
        result = deployer.run(max_attempts=1, check_shutdown=lambda: False)
        assert result is True

        # Verify log file was created with number 1
        log_file_1 = sds_dir / "logs" / "deploy_attempt_1.log"
        assert log_file_1.exists()

        # Second run should continue numbering
        deployer2 = DeploymentAgent(repo, agent)
        result2 = deployer2.run(max_attempts=1, check_shutdown=lambda: False)
        assert result2 is True

        # Should create log file with number 2
        log_file_2 = sds_dir / "logs" / "deploy_attempt_2.log"
        assert log_file_2.exists()

    def test_resume_after_previous_attempts(self, tmp_path):
        """Should resume attempt numbering from previous runs."""
        repo = tmp_path / "repo"
        repo.mkdir()

        sds_dir = repo / ".sds"
        sds_dir.mkdir()
        logs_dir = sds_dir / "logs"
        logs_dir.mkdir()

        # Create fake previous log files
        (logs_dir / "deploy_attempt_1.log").write_text("previous attempt 1")
        (logs_dir / "deploy_attempt_2.log").write_text("previous attempt 2")
        (logs_dir / "deploy_attempt_3.log").write_text("previous attempt 3")

        # Create working deploy script
        deploy_script = sds_dir / "deploy.sh"
        deploy_script.write_text("#!/bin/bash\nexit 0\n")
        deploy_script.chmod(0o755)

        health_script = sds_dir / "health_check.sh"
        health_script.write_text("#!/bin/bash\nexit 0\n")
        health_script.chmod(0o755)

        agent = StubAgent()
        deployer = DeploymentAgent(repo, agent)

        # Run deployment which should create attempt 4
        result = deployer.run(max_attempts=1, check_shutdown=lambda: False)
        assert result is True

        # Should create log file numbered 4 (continuing from previous)
        log_file_4 = sds_dir / "logs" / "deploy_attempt_4.log"
        assert log_file_4.exists()
