import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from app_operator.filesystem import InMemoryFilesystem
from app_operator.langgraph.utils import run_script


class TestGraphRunScriptErrorHandling(unittest.TestCase):
    def setUp(self):
        self.repo_root = Path("/tmp/repo")
        self.fs = InMemoryFilesystem()
        self.fs.mkdir(self.repo_root)

    def tearDown(self):
        pass

    @patch("subprocess.run")
    def test_run_script_timeout(self, mock_run):
        # Simulate timeout
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="sleep 100", timeout=1)

        result = run_script(self.repo_root, self.fs, "sleep 100", timeout=1)

        self.assertFalse(result["success"])
        self.assertEqual(result["exit_code"], -1)
        self.assertIn("Command timed out", result["stderr"])

    @patch("subprocess.run")
    def test_run_script_exception(self, mock_run):
        # Simulate generic exception
        mock_run.side_effect = Exception("System failure")

        result = run_script(self.repo_root, self.fs, "ls")

        self.assertFalse(result["success"])
        self.assertEqual(result["exit_code"], -1)
        self.assertIn("Error: System failure", result["stderr"])


if __name__ == "__main__":
    unittest.main()
