import subprocess
from unittest.mock import MagicMock, patch

import pytest
from app_operator.cli_agent.healthcheck import run_health_check


@pytest.fixture
def repo_path(tmp_path):
    return tmp_path / "repo"


@pytest.fixture
def health_check_script(repo_path):
    script = repo_path / "health_check.sh"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.touch()
    return script


def test_run_health_check_timeout(repo_path, health_check_script):
    """Test health check timeout handling."""
    # Create a TimeoutExpired exception with partial output
    timeout_exception = subprocess.TimeoutExpired(
        cmd=[str(health_check_script)],
        timeout=10,
        output="Partial stdout",
        stderr="Partial stderr",
    )
    # Ensure stdout/stderr are strings as we use text=True
    timeout_exception.stdout = "Partial stdout"
    timeout_exception.stderr = "Partial stderr"

    with patch("subprocess.run", side_effect=timeout_exception):
        result = run_health_check(
            repo_path=repo_path, health_check_script=health_check_script, timeout=10
        )

        assert result["success"] is False
        assert result["exit_code"] == -1
        assert result["stdout"] == "Partial stdout"
        assert result["stderr"] == "Partial stderr"


def test_run_health_check_timeout_logging(repo_path, health_check_script):
    """Test that timeout info is logged to file."""
    log_file_path = repo_path / "health_check.log"

    timeout_exception = subprocess.TimeoutExpired(
        cmd=[str(health_check_script)], timeout=10
    )
    timeout_exception.stdout = "Partial stdout"
    timeout_exception.stderr = "Partial stderr"

    with patch("subprocess.run", side_effect=timeout_exception):
        # We need to use real file or mock open properly.
        # Since logic uses open() directly, let's use a real file for simplicity in checking content
        # but mock subprocess.

        run_health_check(
            repo_path=repo_path,
            health_check_script=health_check_script,
            timeout=10,
            log_file_path=log_file_path,
        )

        assert log_file_path.exists()
        content = log_file_path.read_text()
        assert "Health check timed out" in content
        assert "Partial stdout" in content
        assert "Partial stderr" in content


def test_run_health_check_log_open_failure(repo_path, health_check_script):
    """Test error handling when opening log file fails."""
    log_file_path = repo_path / "health_check.log"

    # Mock open to raise exception
    with patch("builtins.open", side_effect=OSError("Permission denied")):
        # Should not crash
        run_health_check(
            repo_path=repo_path,
            health_check_script=health_check_script,
            log_file_path=log_file_path,
        )


def test_run_health_check_log_write_failure(repo_path, health_check_script):
    """Test error handling when writing to log file fails."""
    log_file_path = repo_path / "health_check.log"

    # Mock file object whose write method fails
    mock_file = MagicMock()
    mock_file.write.side_effect = OSError("Disk full")

    with patch("builtins.open", return_value=mock_file):
        with patch(
            "subprocess.run",
            return_value=MagicMock(returncode=0, stdout="out", stderr="err"),
        ):
            # Should not crash
            run_health_check(
                repo_path=repo_path,
                health_check_script=health_check_script,
                log_file_path=log_file_path,
            )


def test_run_health_check_timeout_log_write_failure(repo_path, health_check_script):
    """Test error handling when writing to log file fails during timeout handling."""
    log_file_path = repo_path / "health_check.log"

    mock_file = MagicMock()
    mock_file.write.side_effect = OSError("Disk full")

    timeout_exception = subprocess.TimeoutExpired(cmd=[], timeout=10)

    with patch("builtins.open", return_value=mock_file):
        with patch("subprocess.run", side_effect=timeout_exception):
            # Should not crash
            run_health_check(
                repo_path=repo_path,
                health_check_script=health_check_script,
                log_file_path=log_file_path,
            )
