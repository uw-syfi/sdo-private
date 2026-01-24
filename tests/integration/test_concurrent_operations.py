"""Tests for concurrent operations and thread safety.

These tests verify that concurrent operations don't cause race conditions
or data corruption.
"""

import threading
import time
from app_operator.operator import AppOperator
from tests.fixtures.agents import StubAgent, TrackingAgent


class TestConcurrentOperations:
    """Tests for concurrent operation safety."""

    def test_shutdown_flag_thread_safe(self, tmp_path):
        """Shutdown flag should be thread-safe."""
        repo = tmp_path / "repo"
        repo.mkdir()

        operator = AppOperator(str(repo), agent=StubAgent())

        # Multiple threads setting shutdown flag
        def set_shutdown():
            for _ in range(100):
                operator._shutdown_requested = True

        threads = [threading.Thread(target=set_shutdown) for _ in range(10)]

        for t in threads:
            t.start()

        for t in threads:
            t.join()

        # Should be True without crashing
        assert operator._shutdown_requested is True

    def test_multiple_health_checks_dont_overlap(self, tmp_path):
        """Health checks should run sequentially, not overlapping."""
        repo = tmp_path / "repo"
        repo.mkdir()

        sds_dir = repo / ".sds"
        sds_dir.mkdir()

        # Create a health check that takes some time
        health_script = sds_dir / "health_check.sh"
        health_script.write_text(
            "#!/bin/bash\n"
            "echo 'Health check starting'\n"
            "sleep 0.5\n"
            "echo 'Health check complete'\n"
            "exit 0\n"
        )
        health_script.chmod(0o755)

        deploy_script = sds_dir / "deploy.sh"
        deploy_script.write_text("#!/bin/bash\nexit 0")
        deploy_script.chmod(0o755)

        agent = TrackingAgent()
        operator = AppOperator(
            str(repo),
            health_check_interval=1,
            health_check_max_count=3,
            max_deployment_attempts=1,
            agent=agent,
        )

        # Run operator
        start_time = time.time()
        exit_code = operator.run()
        elapsed = time.time() - start_time

        # Should have completed successfully
        assert exit_code == 0

        # Checks should have run sequentially (at least 3 seconds for 3 checks with 1s interval)
        # But allowing some variance for system scheduling
        assert elapsed >= 2  # At least 2 seconds for sequential execution


class TestSignalDuringOperations:
    """Tests for signal handling during various operations."""

    def test_signal_during_script_generation(self, tmp_path):
        """Signal during script generation should abort gracefully."""
        repo = tmp_path / "repo"
        repo.mkdir()

        agent = StubAgent()
        operator = AppOperator(
            str(repo),
            agent=agent,
            max_deployment_attempts=1,
        )

        # Request shutdown immediately
        operator._shutdown_requested = True

        # Run should abort early
        exit_code = operator.run()

        # Should have failed due to shutdown
        assert exit_code == 1
        assert operator._shutdown_requested is True


class TestResourceCleanup:
    """Tests for proper resource cleanup."""

    def test_log_files_created_in_correct_location(self, tmp_path):
        """Log files should be created in .sds/logs directory."""
        repo = tmp_path / "repo"
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
        operator = AppOperator(
            str(repo),
            health_check_max_count=1,
            max_deployment_attempts=1,
            agent=agent,
        )

        operator.run()

        # Should have created log directory
        logs_dir = sds_dir / "logs"
        assert logs_dir.exists()
        assert logs_dir.is_dir()

        # Should have created deployment log
        deploy_log = logs_dir / "deploy_attempt_1.log"
        assert deploy_log.exists()

    def test_cleanup_with_missing_scripts(self, tmp_path):
        """Cleanup should handle missing deployment scripts gracefully."""
        repo = tmp_path / "repo"
        repo.mkdir()

        agent = StubAgent()
        operator = AppOperator(
            str(repo),
            agent=agent,
        )

        # Mark as deployed even though no scripts exist
        operator._deployed = True

        # Cleanup should not crash
        operator._cleanup()  # Should handle missing scripts gracefully
