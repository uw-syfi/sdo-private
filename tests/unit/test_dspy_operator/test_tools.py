"""Tests for app_operator_dspy.tools."""

import subprocess
from unittest.mock import patch

from app_operator_dspy.tools.docker import docker_compose_up, docker_logs, docker_ps
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

    def test_list_files(self, tmp_path):
        (tmp_path / "a.py").touch()
        (tmp_path / "b.txt").touch()
        result = list_files(str(tmp_path), "*.py")
        assert "a.py" in result
        assert "b.txt" not in result

    def test_list_files_no_matches(self, tmp_path):
        result = list_files(str(tmp_path), "*.xyz")
        assert "No files" in result


class TestDocker:
    @patch("app_operator_dspy.tools.docker.run_shell", return_value=ShellResult(0, "Exit code: 0"))
    def test_compose_up_with_build(self, mock_shell):
        result = docker_compose_up("/app", build=True)
        mock_shell.assert_called_once_with("docker compose up --build -d", cwd="/app", timeout=300)
        assert result == "Exit code: 0"

    @patch("app_operator_dspy.tools.docker.run_shell", return_value=ShellResult(0, "Exit code: 0"))
    def test_compose_up_no_build(self, mock_shell):
        docker_compose_up("/app", build=False)
        mock_shell.assert_called_once_with("docker compose up -d", cwd="/app", timeout=300)

    @patch("app_operator_dspy.tools.docker.run_shell", return_value=ShellResult(0, "Exit code: 0"))
    def test_docker_ps(self, mock_shell):
        docker_ps("/app")
        mock_shell.assert_called_once_with("docker compose ps", cwd="/app")

    @patch("app_operator_dspy.tools.docker.run_shell", return_value=ShellResult(0, "Exit code: 0"))
    def test_docker_logs(self, mock_shell):
        docker_logs("web", tail=10, cwd="/app")
        mock_shell.assert_called_once_with("docker compose logs --tail=10 web", cwd="/app")


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
