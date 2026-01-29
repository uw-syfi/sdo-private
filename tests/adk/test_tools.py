import unittest
from unittest.mock import MagicMock, patch
from pathlib import Path
from app_operator.adk.tools import build_tools
from app_operator.filesystem import InMemoryFilesystem


class TestAdkTools(unittest.TestCase):
    def setUp(self):
        self.repo_root = Path("/repo")
        self.fs = InMemoryFilesystem()
        # Initialize some directories
        self.fs.mkdir(self.repo_root)
        self.tools = {t.__name__: t for t in build_tools(self.repo_root, self.fs)}

    def test_list_files(self):
        # Setup
        self.fs.write_text(self.repo_root / "file1.txt", "content")
        self.fs.write_text(self.repo_root / "file2.txt", "content")
        subdir = self.repo_root / "subdir"
        self.fs.mkdir(subdir)

        # Test listing directory
        # Note: list_files uses target.iterdir() which hits the real filesystem
        # so we need to mock Path.iterdir

        with patch("pathlib.Path.iterdir") as mock_iterdir:
            mock_iterdir.return_value = [
                Path("file1.txt"),
                Path("file2.txt"),
                Path("subdir"),
            ]

            result = self.tools["list_files"](".")

            self.assertEqual(result["status"], "success")
            self.assertIn("file1.txt", result["output"])
            self.assertIn("file2.txt", result["output"])
            self.assertIn("subdir", result["output"])

        # Test non-existent path
        result = self.tools["list_files"]("nonexistent")
        self.assertEqual(result["status"], "error")
        self.assertIn("Path does not exist", result["error"])

    def test_find_files(self):
        # find_files uses repo_root.glob() which hits real filesystem
        with patch("pathlib.Path.glob") as mock_glob:
            # Create mock path objects that behave like files
            p1 = MagicMock(spec=Path)
            p1.is_file.return_value = True
            p1.relative_to.return_value = Path("src/main.py")
            p1.__str__.return_value = str(self.repo_root / "src/main.py")

            p2 = MagicMock(spec=Path)
            p2.is_file.return_value = True
            p2.relative_to.return_value = Path("src/utils.py")
            p2.__str__.return_value = str(self.repo_root / "src/utils.py")

            mock_glob.return_value = [p1, p2]

            result = self.tools["find_files"]("**/*.py")

            self.assertEqual(result["status"], "success")
            self.assertIn("src/main.py", result["output"])
            self.assertIn("src/utils.py", result["output"])

    def test_read_file(self):
        file_path = self.repo_root / "test.txt"
        content = "Hello World"
        self.fs.write_text(file_path, content)

        result = self.tools["read_file"]("test.txt")

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["output"], content)

        # Read non-existent
        result = self.tools["read_file"]("missing.txt")
        self.assertEqual(result["status"], "error")

    def test_write_file(self):
        path = "new_file.txt"
        content = "New Content"

        result = self.tools["write_file"](path, content)

        self.assertEqual(result["status"], "success")
        self.assertEqual(self.fs.read_text(self.repo_root / path), content)

        # Write to directory error
        dir_path = "subdir"
        self.fs.mkdir(self.repo_root / dir_path)
        result = self.tools["write_file"](dir_path, "content")
        self.assertEqual(result["status"], "error")
        self.assertIn("is a directory", result["error"].lower())

    def test_search_content(self):
        file_path = self.repo_root / "test.log"
        content = "Error: something went wrong\nInfo: all good\nError: another one"
        self.fs.write_text(file_path, content)

        # Test single file search
        result = self.tools["search_content"]("Error", "test.log")
        self.assertEqual(result["status"], "success")
        self.assertIn("test.log:1:Error: something went wrong", result["output"])
        self.assertIn("test.log:3:Error: another one", result["output"])

        # Test recursive search (requires mocking rglob)
        with patch("pathlib.Path.rglob") as mock_rglob:
            # Create mock path that says it's a file
            p = MagicMock(spec=Path)
            p.is_file.return_value = True
            p.relative_to.return_value = Path("test.log")
            p.__str__.return_value = str(
                file_path
            )  # Needs to match InMemoryFilesystem key

            # Since tools.py uses the path object directly for read_text(path),
            # and read_text converts path to str, this should work if p.__str__ is correct.

            mock_rglob.return_value = [p]

            result = self.tools["search_content"]("Info", ".")
            self.assertEqual(result["status"], "success")
            self.assertIn("test.log:2:Info: all good", result["output"])

    @patch("subprocess.run")
    def test_run_command(self, mock_run):
        # Mock success
        mock_run.return_value = MagicMock(
            returncode=0, stdout="command output", stderr=""
        )

        result = self.tools["run_command"]("ls -la", 10)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["output"], "command output")

        # Mock failure
        mock_run.return_value = MagicMock(
            returncode=1, stdout="", stderr="command failed"
        )

        result = self.tools["run_command"]("invalid", 10)
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error"], "command failed")
