import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from lego_agent.backend.tools import (
    build_readonly_tools,
    build_tools,
)


class MockFileSystem(MagicMock):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.files: dict[Path, str] = {}
        self.is_dir_map: dict[Path, bool] = {}

    def exists(self, path: Path) -> bool:
        return path in self.files or path in self.is_dir_map

    def is_dir(self, path: Path) -> bool:
        return self.is_dir_map.get(path, False)

    def read_text(self, path: Path) -> str:
        if path in self.files:
            return self.files[path]
        raise FileNotFoundError(f"File not found: {path}")


def _iterdir_for(files: dict[Path, str]):
    def iterdir(path: Path) -> list[Path]:
        return [p for p in files if p.parent == path]

    return iterdir


def _glob_for(files: dict[Path, str]):
    def glob(_root: Path, pattern: str) -> list[Path]:
        return [p for p in files if p.match(pattern)]

    return glob


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
        with patch.object(Path, "iterdir", _iterdir_for(self.fs.files)):
            result = list_files("src")
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["output"], "src/main.py\nsrc/utils.py")

    def test_find_files_success(self):
        self.fs.files = {
            Path("/app/src/main.py"): "",
            Path("/app/src/utils.py"): "",
        }
        find_files = self.tools[1]
        with (
            patch.object(Path, "glob", _glob_for(self.fs.files)),
            patch.object(Path, "is_file", return_value=True),
            patch.object(Path, "is_dir", return_value=False),
        ):
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
        search_content = self.tools[3]
        result = search_content("main", "src/main.py")
        self.assertEqual(result["status"], "success")
        self.assertIn("src/main.py:1:def main()", result["output"])


if __name__ == "__main__":
    unittest.main()
