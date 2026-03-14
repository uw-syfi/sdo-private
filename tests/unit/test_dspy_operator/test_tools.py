"""Tests for app_operator_dspy.tools."""

import subprocess
from unittest.mock import patch

import pytest

from app_operator_dspy.tools.agent_tools import write_file_tool
from app_operator_dspy.tools.filesystem import list_files, read_file, write_file
from app_operator_dspy.tools.health_check import run_health_check
from app_operator_dspy.tools.shell import ShellResult, run_shell


class TestRunShell:
    def test_captures_stdout(self):
        result = run_shell("echo hello")
        assert isinstance(result, ShellResult)
        assert result.return_code == 0
        assert result.succeeded
        assert "Exit code: 0" in result.output
        assert "hello" in result.output

    def test_captures_nonzero_exit(self):
        result = run_shell("exit 1")
        assert result.return_code == 1
        assert not result.succeeded
        assert "Exit code: 1" in result.output

    def test_timeout_returns_exit_code_format(self):
        with patch("app_operator_dspy.tools.shell.subprocess.run", side_effect=subprocess.TimeoutExpired("cmd", 1)):
            result = run_shell("sleep 100", timeout=1)
        assert result.return_code == 124
        assert result.output.startswith("Exit code: 124")
        assert "timed out" in result.output

    def test_oserror_returns_exit_code_format(self):
        with patch("app_operator_dspy.tools.shell.subprocess.run", side_effect=OSError("no such file")):
            result = run_shell("nonexistent")
        assert result.return_code == 1
        assert result.output.startswith("Exit code: 1")
        assert "no such file" in result.output


class TestFilesystem:
    def test_read_file(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("content")
        assert read_file(str(f)) == "content"

    def test_read_file_missing(self):
        result = read_file("/nonexistent/path/file.txt")
        assert "Error" in result

    def test_write_file(self, tmp_path):
        f = tmp_path / "sub" / "out.txt"
        result = write_file(str(f), "data")
        assert "Wrote 4 bytes" in result
        assert f.read_text() == "data"

    def test_write_file_raises_on_failure(self):
        with patch("pathlib.Path.write_text", side_effect=OSError(13, "Permission denied")):
            with pytest.raises(OSError, match="Permission denied"):
                write_file("/nonexistent/file.txt", "data")

    def test_write_file_tool_returns_error_string(self):
        with patch("pathlib.Path.write_text", side_effect=OSError(13, "Permission denied")):
            result = write_file_tool("/nonexistent/file.txt", "data")
        assert result.startswith("Error writing")
        assert "Permission denied" in result

    def test_list_files(self, tmp_path):
        (tmp_path / "a.py").touch()
        (tmp_path / "b.txt").touch()
        result = list_files(str(tmp_path), "*.py")
        assert "a.py" in result
        assert "b.txt" not in result

    def test_list_files_no_matches(self, tmp_path):
        result = list_files(str(tmp_path), "*.xyz")
        assert "No files" in result


class TestHealthCheck:
    @patch(
        "app_operator_dspy.tools.health_check.run_shell",
        return_value=ShellResult(0, "Exit code: 0\nStdout:\nAll services healthy"),
    )
    def test_runs_health_check_script(self, mock_shell):
        result = run_health_check("/myapp")
        mock_shell.assert_called_once_with("/myapp/.sds/health_check.sh", cwd="/myapp", timeout=120)
        assert "healthy" in result

    @patch(
        "app_operator_dspy.tools.health_check.run_shell",
        return_value=ShellResult(0, "Exit code: 0\nStdout:\nhealthy"),
    )
    def test_custom_timeout(self, mock_shell):
        run_health_check("/myapp", timeout=600)
        mock_shell.assert_called_once_with("/myapp/.sds/health_check.sh", cwd="/myapp", timeout=600)
