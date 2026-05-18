
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from lego_agent.backend.tools import (
    build_tools,
    build_readonly_tools,
)
from libs.sds_core.filesystem import FileSystemInterface

class MockFileSystem(MagicMock):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.files = {}
        self.is_dir_map = {}

    def exists(self, path):
        return path in self.files or path in self.is_dir_map

    def is_dir(self, path):
        return self.is_dir_map.get(path, False)

    def read_text(self, path):
        if path in self.files:
            return self.files[path]
        raise FileNotFoundError(f"File not found: {path}")

    def glob(self, pattern):
        # Simplified glob for testing
        return [p for p in self.files if Path(p).match(pattern)]

    def rglob(self, pattern):
        # Simplified rglob for testing
        return [p for p in self.files if Path(p).match(pattern)]

    def iterdir(self, path):
        return [p for p in self.files if Path(p).parent == path]

class TestLegoAgentTools(unittest.TestCase):

    def setUp(self):
        self.repo_root = Path("/app")
        self.fs = MockFileSystem()
        self.tools = build_tools(self.repo_root, filesystem=self.fs)
        self.readonly_tools = build_readonly_tools(self.repo_root, filesystem=self.fs)

    def test_list_files_success(self):
        self.fs.is_dir_map = {Path("/app/src"): True}
        self.fs.files = {
            Path("/app/src/main.py"): "",
            Path("/app/src/utils.py"): "",
        }
        list_files = self.tools[0]
        with patch.object(Path, 'iterdir', self.fs.iterdir):
            result = list_files("src")
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["output"], "src/main.py\nsrc/utils.py")
    
    def test_find_files_success(self):
        self.fs.files = {
            Path("/app/src/main.py"): "",
            Path("/app/src/utils.py"): "",
        }
        find_files = self.tools[1]
        with patch.object(Path, 'glob', self.fs.glob):
            result = find_files("**/*.py")
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["output"], "src/main.py\nsrc/utils.py")

    def test_read_file_success(self):
        self.fs.files = {Path("/app/src/main.py"): "def main():\n    pass"}
        read_file = self.tools[2]
        result = read_file("src/main.py")
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["output"], "def main():\n    pass")

    def test_search_content_in_file_success(self):
        self.fs.files = {Path("/app/src/main.py"): "def main():\n    pass"}
        self.fs.is_dir_map = {Path("/app/src"): True}
        search_content = self.tools[3]
        result = search_content("main", "src/main.py")
        self.assertEqual(result["status"], "success")
        self.assertIn("src/main.py:1:def main()", result["output"])

if __name__ == "__main__":
    unittest.main()
