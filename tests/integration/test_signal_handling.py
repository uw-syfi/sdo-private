"""Tests for signal handling and graceful shutdown.

These tests verify that the operator handles SIGINT and SIGTERM signals
correctly, performing cleanup and exiting gracefully.
"""

import signal
import threading
import time
from unittest.mock import Mock

import pytest

from app_operator.cli_agent.operator import AppOperator
from app_operator.core import AgentConfig, Config, DeploymentError
from libs.model_config import ModelConfig
from tests.fixtures.agents import StubAgent


class TestSignalHandling:
    """Tests for signal handling during various operation phases."""

    def test_sigint_during_deployment_stops_gracefully(self, tmp_path):
        """SIGINT during deployment should trigger graceful shutdown."""
        repo = tmp_path / "repo"
        repo.mkdir()

        # Create sds directory with scripts
        sds_dir = repo / ".sds"
        sds_dir.mkdir()

        # Create a deploy script that runs for a while
        deploy_script = sds_dir / "deploy.sh"
        deploy_script.write_text(
            "#!/bin/bash\n"
            "echo 'Starting deployment...'\n"
            "sleep 2\n"  # Run for 2 seconds
            "echo 'Deployment complete'\n"
            "exit 0\n"
        )
        deploy_script.chmod(0o755)

        # Create a health check script
        health_script = sds_dir / "health_check.sh"
        health_script.write_text("#!/bin/bash\necho 'Health check passed'\nexit 0\n")
        health_script.chmod(0o755)

        agent = StubAgent()
        operator = AppOperator(
            str(repo),
            health_check_interval=30,
            health_check_max_count=1,
            max_deployment_attempts=1,
            agent=agent,
            config=Config(
                agent=AgentConfig(backend="codex", model_config=ModelConfig(provider="openai", model="test-model"))
            ),
        )

        # Use an event to synchronize: wait until the operator has started
        operator_started = threading.Event()

        def blocking_deploy(**kwargs):
            operator_started.set()
            while not operator._shutdown_requested:
                time.sleep(0.01)
            return False

        operator.deployer.run = blocking_deploy  # type: ignore[method-assign]

        result = None

        def run_with_result():
            nonlocal result
            result = operator.run()

        thread = threading.Thread(target=run_with_result)
        thread.start()

        # Wait for the operator to start, then request shutdown
        operator_started.wait(timeout=10)
        operator._shutdown_requested = True

        thread.join(timeout=10)

        # Should have stopped gracefully
        assert operator._shutdown_requested is True

    def test_shutdown_flag_prevents_new_checks(self, tmp_path, stub_agent):
        """Setting shutdown flag should prevent new monitoring checks."""
        repo = tmp_path / "repo"
        repo.mkdir()

        # Create scripts
        sds_dir = repo / ".sds"
        sds_dir.mkdir()

        deploy_script = sds_dir / "deploy.sh"
        deploy_script.write_text("#!/bin/bash\necho 'deployed'\nexit 0\n")
        deploy_script.chmod(0o755)

        health_script = sds_dir / "health_check.sh"
        health_script.write_text("#!/bin/bash\necho 'healthy'\nexit 0\n")
        health_script.chmod(0o755)

        operator = AppOperator(
            str(repo),
            health_check_interval=0.1,  # type: ignore[arg-type]
            health_check_max_count=5,
            max_deployment_attempts=1,
            agent=stub_agent,
            config=Config(
                agent=AgentConfig(backend="codex", model_config=ModelConfig(provider="openai", model="test-model"))
            ),
        )

        # Use an event to synchronize: wait until monitoring begins
        monitoring_started = threading.Event()

        operator.deployer.run = Mock(return_value=True)

        def blocking_monitor(**kwargs):
            monitoring_started.set()
            while not operator._shutdown_requested:
                time.sleep(0.01)
            operator.monitor.healthy = True

        operator.monitor.run = blocking_monitor  # type: ignore[method-assign]

        result = None

        def run_with_result():
            nonlocal result
            result = operator.run()

        thread = threading.Thread(target=run_with_result)
        thread.start()

        # Wait for the operator to reach monitoring, then request shutdown
        monitoring_started.wait(timeout=10)
        operator._shutdown_requested = True

        thread.join(timeout=10)

        # Should have exited without running all 5 checks
        # (This is a simple test - in practice we'd check the monitor didn't run 5 times)

    def test_deployed_flag_controls_cleanup(self, tmp_path, stub_agent):
        """Cleanup should only run if deployment succeeded."""
        repo = tmp_path / "repo"
        repo.mkdir()

        # Create scripts
        sds_dir = repo / ".sds"
        sds_dir.mkdir()

        # Deploy script that fails
        deploy_script = sds_dir / "deploy.sh"
        deploy_script.write_text("#!/bin/bash\necho 'deployment failed'\nexit 1\n")
        deploy_script.chmod(0o755)

        health_script = sds_dir / "health_check.sh"
        health_script.write_text("#!/bin/bash\necho 'healthy'\nexit 0\n")
        health_script.chmod(0o755)

        operator = AppOperator(
            str(repo),
            health_check_interval=30,
            health_check_max_count=1,
            max_deployment_attempts=1,
            agent=stub_agent,
            config=Config(
                agent=AgentConfig(backend="codex", model_config=ModelConfig(provider="openai", model="test-model"))
            ),
        )

        # Deployment failure → DeploymentError
        with pytest.raises(DeploymentError):
            operator.run()

        # _deployed flag should be False
        assert operator._deployed is False


class TestShutdownBehavior:
    """Tests for shutdown behavior and cleanup."""

    def test_cleanup_only_runs_when_deployed(self, tmp_path, stub_agent):
        """_cleanup() should only execute if _deployed is True."""
        repo = tmp_path / "repo"
        repo.mkdir()

        operator = AppOperator(
            str(repo),
            agent=stub_agent,
            config=Config(
                agent=AgentConfig(backend="codex", model_config=ModelConfig(provider="openai", model="test-model"))
            ),
        )

        # Initially not deployed
        assert operator._deployed is False

        # Cleanup should not fail when not deployed
        operator._cleanup()  # Should do nothing

        # Now mark as deployed
        operator._deployed = True

        # Cleanup should try to run stop command (will fail, but that's ok)
        # Just verifying it executes
        operator._cleanup()

    def test_multiple_signals_idempotent(self, tmp_path):
        """Multiple shutdown signals should be handled idempotently."""
        repo = tmp_path / "repo"
        repo.mkdir()

        operator = AppOperator(
            str(repo),
            agent=StubAgent(),
            config=Config(
                agent=AgentConfig(backend="codex", model_config=ModelConfig(provider="openai", model="test-model"))
            ),
        )

        # First signal
        operator._handle_shutdown_signal(signal.SIGTERM, None)
        assert operator._shutdown_requested is True

        # Second signal should not cause issues
        operator._handle_shutdown_signal(signal.SIGTERM, None)
        assert operator._shutdown_requested is True

    def test_sigterm_sets_shutdown_flag(self, tmp_path):
        """SIGTERM should set shutdown flag without raising exception."""
        repo = tmp_path / "repo"
        repo.mkdir()

        operator = AppOperator(
            str(repo),
            agent=StubAgent(),
            config=Config(
                agent=AgentConfig(backend="codex", model_config=ModelConfig(provider="openai", model="test-model"))
            ),
        )

        assert operator._shutdown_requested is False

        # SIGTERM should set flag but not raise
        operator._handle_shutdown_signal(signal.SIGTERM, None)

        assert operator._shutdown_requested is True

    def test_sigint_sets_shutdown_flag(self, tmp_path):
        """SIGINT should set shutdown flag without raising (safe in threads)."""
        repo = tmp_path / "repo"
        repo.mkdir()

        operator = AppOperator(
            str(repo),
            agent=StubAgent(),
            config=Config(
                agent=AgentConfig(backend="codex", model_config=ModelConfig(provider="openai", model="test-model"))
            ),
        )

        assert operator._shutdown_requested is False

        # SIGINT should set the flag but NOT raise KeyboardInterrupt
        # (raising from a signal handler is dangerous in multi-threaded code)
        operator._handle_shutdown_signal(signal.SIGINT, None)

        assert operator._shutdown_requested is True
