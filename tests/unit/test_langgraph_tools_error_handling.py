import subprocess
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from app_operator.filesystem import InMemoryFilesystem
from app_operator.langgraph.tools import (
    ToolContext,
    _build_bash,
    _build_glob,
    _build_grep,
    _build_ls,
    _build_read,
    _build_write_file,
)


class TestLangGraphToolsErrorHandling(unittest.TestCase):
    def setUp(self):
        self.repo_root = Path("/tmp/repo")
        self.fs = InMemoryFilesystem()
        self.fs.mkdir(self.repo_root)
        self.context = ToolContext(self.repo_root, self.fs)

    def tearDown(self):
        pass

    def test_ls_error(self):
        ls_tool = _build_ls(self.context)

        # Test non-existent path
        result = ls_tool.invoke({"path": "nonexistent"})
        self.assertTrue(result.startswith("Error: Path does not exist"))

        # Test internal error (simulate by mocking resolve_path to raise)
        with patch.object(self.context, "resolve_path", side_effect=OSError("Unexpected error")):
            result = ls_tool.invoke({"path": "."})
            self.assertEqual(result, "Error: Unexpected error")

    def test_glob_error(self):
        # 1. Test pattern escaping root (uses real Path logic, so use real context)
        glob_tool = _build_glob(self.context)
        result = glob_tool.invoke({"pattern": "/outside/repo/*.txt"})
        self.assertEqual(result, ["Error: Pattern escapes repository root: /outside/repo/*.txt"])

        # 2. Test generic exception during glob iteration
        # Create a context with a mock repo_root that raises on glob()
        mock_root = MagicMock()
        mock_root.glob.side_effect = OSError("Glob failed")
        # We need to ensure Path(pattern).relative_to(mock_root) doesn't crash before glob is called
        # if pattern is absolute. We use relative pattern here.

        context = ToolContext(mock_root, self.fs)
        glob_tool_fail = _build_glob(context)

        result = glob_tool_fail.invoke({"pattern": "*.txt"})
        self.assertEqual(result, ["Error: Glob failed"])

    def test_read_error(self):
        read_tool = _build_read(self.context)

        # Test file not found (via filesystem)
        result = read_tool.invoke({"path": "missing.txt"})
        self.assertTrue(result.startswith("Error: No such file"))

        # Test read permission error (simulated)
        self.fs.write_text(self.repo_root / "secret.txt", "content")
        self.fs.simulate_permission_error(self.repo_root / "secret.txt")
        result = read_tool.invoke({"path": "secret.txt"})
        self.assertTrue(result.startswith("Error: Permission denied"))

    def test_write_file_error(self):
        write_tool = _build_write_file(self.context)

        # Test writing to directory
        self.fs.mkdir(self.repo_root / "subdir")
        result = write_tool.invoke({"path": "subdir", "content": "data"})
        self.assertTrue(result.startswith("Error: Path is a directory"))

        # Test permission error on write
        self.fs.simulate_permission_error(self.repo_root / "protected.txt")
        result = write_tool.invoke({"path": "protected.txt", "content": "data"})
        self.assertTrue(result.startswith("Error: Permission denied"))

    def test_grep_error(self):
        grep_tool = _build_grep(self.context)

        # Test invalid regex
        result = grep_tool.invoke({"pattern": "[", "path": "."})
        self.assertTrue(result[0].startswith("Error: "))

    @patch("subprocess.run")
    def test_bash_error(self, mock_run):
        bash_tool = _build_bash(self.context)

        # Test timeout
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="sleep 100", timeout=1)
        result = bash_tool.invoke({"command": "sleep 100", "timeout": 1})
        self.assertFalse(result["success"])
        self.assertEqual(result["exit_code"], -1)
        self.assertIn("Command timed out", result["stderr"])

        # Test generic exception
        mock_run.side_effect = OSError("System failure")
        result = bash_tool.invoke({"command": "ls"})
        self.assertFalse(result["success"])
        self.assertEqual(result["exit_code"], -1)
        self.assertEqual(result["stderr"], "Error: System failure")

    def test_resolve_path_error(self):
        # Test path escaping root
        ls_tool = _build_ls(self.context)
        result = ls_tool.invoke({"path": "../outside"})
        self.assertTrue(result.startswith("Error: "))
        self.assertIn("Path escapes repository root", result)


if __name__ == "__main__":
    unittest.main()
