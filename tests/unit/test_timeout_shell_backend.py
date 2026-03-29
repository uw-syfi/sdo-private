"""Tests for TimeoutShellBackend process-group timeout handling."""

from __future__ import annotations

import pytest

from sregym_agents.crucible_simple.shell_backend import TimeoutShellBackend


class TestTimeoutShellBackend:
    """Verify that TimeoutShellBackend kills the entire process group."""

    def test_normal_command_succeeds(self):
        backend = TimeoutShellBackend(virtual_mode=True, inherit_env=True)
        result = backend.execute("echo hello")
        assert result.exit_code == 0
        assert "hello" in result.output

    def test_failing_command_returns_nonzero(self):
        backend = TimeoutShellBackend(virtual_mode=True, inherit_env=True)
        result = backend.execute("exit 42")
        assert result.exit_code == 42

    def test_timeout_kills_hanging_process(self):
        """A sleep command must be killed and return exit code 124."""
        backend = TimeoutShellBackend(
            virtual_mode=True, inherit_env=True, timeout=1,
        )
        result = backend.execute("sleep 60")
        assert result.exit_code == 124
        assert "timed out" in result.output.lower()

    def test_per_command_timeout_override(self):
        """Per-command timeout overrides the default."""
        backend = TimeoutShellBackend(
            virtual_mode=True, inherit_env=True, timeout=300,
        )
        result = backend.execute("sleep 60", timeout=1)
        assert result.exit_code == 124

    def test_timeout_kills_child_processes(self):
        """Children spawned by the shell must also be killed."""
        backend = TimeoutShellBackend(
            virtual_mode=True, inherit_env=True, timeout=2,
        )
        # Spawn a subshell that spawns a sleep — the whole group should die
        result = backend.execute("bash -c 'sleep 60 & wait'")
        assert result.exit_code == 124

    def test_empty_command_returns_error(self):
        backend = TimeoutShellBackend(virtual_mode=True, inherit_env=True)
        result = backend.execute("")
        assert result.exit_code == 1

    def test_invalid_timeout_raises(self):
        backend = TimeoutShellBackend(virtual_mode=True, inherit_env=True)
        with pytest.raises(ValueError, match="positive"):
            backend.execute("echo hi", timeout=-1)

    def test_output_truncation(self):
        backend = TimeoutShellBackend(
            virtual_mode=True, inherit_env=True, max_output_bytes=50,
        )
        result = backend.execute("python3 -c \"print('x' * 200)\"")
        assert result.truncated
        assert "truncated" in result.output.lower()
