import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from lego_agent.backend.tools import (
    ToolContext,
    _build_find_files,
    _build_list_files,
    _build_read_file,
    _build_run_command,
    _build_search_content,
    _build_write_file,
    build_readonly_tools,
    build_tools,
)
from libs.sds_core.filesystem import FileSystemInterface


class TestToolContext(unittest.TestCase):
    def setUp(self):
        self.repo_root = Path("/app")
        self.filesystem = MagicMock(spec=FileSystemInterface)
        self.context = ToolContext(repo_root=self.repo_root, filesystem=self.filesystem)

    def test_resolve_path_absolute(self):
        path = "/app/src/main.py"
        resolved_path = self.context.resolve_path(path)
        self.assertEqual(resolved_path, Path("/app/src/main.py"))

    def test_resolve_path_relative(self):
        path = "src/main.py"
        resolved_path = self.context.resolve_path(path)
        self.assertEqual(resolved_path, Path("/app/src/main.py"))


@patch("pathlib.Path.is_file", return_value=True)
@patch("pathlib.Path.is_dir", return_value=False)
@patch("pathlib.Path.rglob")
@patch("pathlib.Path.glob")
@patch("pathlib.Path.iterdir")
class TestToolsWithPatch(unittest.TestCase):
    def setUp(self):
        self.repo_root = Path("/app")
        self.filesystem = MagicMock(spec=FileSystemInterface)
        self.context = ToolContext(repo_root=self.repo_root, filesystem=self.filesystem)

    def test_list_files_success(self, mock_iterdir, mock_glob, mock_rglob, mock_is_dir, mock_is_file):
        path = "src"
        self.filesystem.exists.return_value = True
        self.filesystem.is_dir.return_value = True
        mock_iterdir.return_value = [
            Path("/app/src/main.py"),
            Path("/app/src/utils.py"),
        ]

        list_files = _build_list_files(self.context)
        result = list_files(path)

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["output"], "src/main.py\nsrc/utils.py")

    def test_find_files_success(self, mock_iterdir, mock_glob, mock_rglob, mock_is_dir, mock_is_file):
        pattern = "**/*.py"
        mock_glob.return_value = [
            Path("/app/src/main.py"),
            Path("/app/src/utils.py"),
        ]
        find_files = _build_find_files(self.context)
        result = find_files(pattern)

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["output"], "src/main.py\nsrc/utils.py")

    def test_read_file_success(self, mock_iterdir, mock_glob, mock_rglob, mock_is_dir, mock_is_file):
        path = "src/main.py"
        content = "def main():\n    pass"
        self.filesystem.read_text.return_value = content
        read_file = _build_read_file(self.context)
        result = read_file(path)

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["output"], content)

    def test_write_file_success(self, mock_iterdir, mock_glob, mock_rglob, mock_is_dir, mock_is_file):
        path = "src/new_file.py"
        content = "print('hello')"
        self.filesystem.is_dir.return_value = False
        write_file = _build_write_file(self.context)
        result = write_file(path, content)

        self.assertEqual(result["status"], "success")
        self.assertIn("Wrote", result["output"])
        self.filesystem.write_text.assert_called_once()

    def test_search_content_in_file_success(self, mock_iterdir, mock_glob, mock_rglob, mock_is_dir, mock_is_file):
        path = "src/main.py"
        pattern = "main"
        content = "def main():\n    pass"
        self.filesystem.exists.return_value = True
        self.filesystem.is_dir.return_value = False
        self.filesystem.read_text.return_value = content
        search_content = _build_search_content(self.context)
        result = search_content(pattern, path)

        self.assertEqual(result["status"], "success")
        self.assertIn("src/main.py:1:def main()", result["output"])

    def test_search_content_in_directory_success(self, mock_iterdir, mock_glob, mock_rglob, mock_is_dir, mock_is_file):
        path = "src"
        pattern = "main"
        self.filesystem.exists.return_value = True
        self.filesystem.is_dir.return_value = True
        mock_rglob.return_value = [Path("/app/src/main.py")]
        self.filesystem.read_text.return_value = "def main():\n    pass"
        search_content = _build_search_content(self.context)
        result = search_content(pattern, path)

        self.assertEqual(result["status"], "success")
        self.assertIn("src/main.py:1:def main()", result["output"])

    @patch("subprocess.run")
    def test_run_command_success(
        self,
        mock_subprocess_run,
        mock_iterdir,
        mock_glob,
        mock_rglob,
        mock_is_dir,
        mock_is_file,
    ):
        command = "ls -l"
        mock_subprocess_run.return_value.returncode = 0
        mock_subprocess_run.return_value.stdout = "total 0"
        mock_subprocess_run.return_value.stderr = ""
        run_command = _build_run_command(self.context)
        result = run_command(command, 10)

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["output"], "total 0")


class TestBuildTools(unittest.TestCase):
    def test_build_tools(self):
        repo_path = Path("/app")
        tools = build_tools(repo_path)
        self.assertEqual(len(tools), 6)

    def test_build_readonly_tools(self):
        repo_path = Path("/app")
        tools = build_readonly_tools(repo_path)
        self.assertEqual(len(tools), 4)


if __name__ == "__main__":
    unittest.main()
