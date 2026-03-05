"""Unit tests for the helper functions extracted from make_change_on_remote_copy."""

import subprocess
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from app_operator.langgraph.tools import (
    _create_gitlab_mr,
    _get_gitlab_info,
    _parse_remote_url,
    _run_git,
    _sanitize_branch_name,
    _stage_and_commit,
)


class TestRunGit(unittest.TestCase):
    @patch("subprocess.run")
    def test_success(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="true\n", stderr="")
        result = _run_git(["rev-parse", "--is-inside-work-tree"], "/tmp")
        self.assertTrue(result["success"])
        self.assertEqual(result["stdout"], "true\n")

    @patch("subprocess.run")
    def test_failure_returncode(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="not a git repo")
        result = _run_git(["rev-parse", "--is-inside-work-tree"], "/tmp")
        self.assertFalse(result["success"])
        self.assertEqual(result["exit_code"], 1)

    @patch("subprocess.run")
    def test_timeout(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="git status", timeout=120)
        result = _run_git(["status"], "/tmp")
        self.assertFalse(result["success"])
        self.assertEqual(result["exit_code"], -1)
        self.assertIn("timed out", result["stderr"])


class TestSanitizeBranchName(unittest.TestCase):
    def test_basic_name(self):
        result = _sanitize_branch_name("my-feature")
        self.assertTrue(result.startswith("my-feature-"))

    def test_invalid_chars_replaced(self):
        result = _sanitize_branch_name("fix: some bug!")
        # colons, exclamation marks get replaced with dashes
        self.assertNotIn(":", result)
        self.assertNotIn("!", result)

    def test_empty_name_defaults(self):
        result = _sanitize_branch_name("   ")
        self.assertTrue(result.startswith("sds-change-"))

    def test_timestamp_appended(self):
        result = _sanitize_branch_name("feat")
        parts = result.rsplit("-", 2)
        # The last two parts form a YYYYMMDD-HHMMSS timestamp
        self.assertEqual(len(parts), 3)

    def test_multiple_consecutive_dashes_collapsed(self):
        result = _sanitize_branch_name("a---b")
        # The result should not contain multiple consecutive dashes in the base portion
        base = result.rsplit("-", 2)[0]
        self.assertNotIn("--", base)


class TestParseRemoteUrl(unittest.TestCase):
    def test_ssh_url(self):
        host, path = _parse_remote_url("git@gitlab.com:group/project.git")
        self.assertEqual(host, "gitlab.com")
        self.assertEqual(path, "group/project")

    def test_https_url(self):
        host, path = _parse_remote_url("https://gitlab.com/group/project.git")
        self.assertEqual(host, "gitlab.com")
        self.assertEqual(path, "group/project")

    def test_http_url(self):
        host, path = _parse_remote_url("http://gitlab.example.com/grp/proj.git")
        self.assertEqual(host, "gitlab.example.com")
        self.assertEqual(path, "grp/proj")

    def test_unknown_scheme_returns_empty(self):
        host, path = _parse_remote_url("ftp://gitlab.com/group/proj")
        self.assertEqual(host, "")
        self.assertEqual(path, "")

    def test_git_suffix_stripped(self):
        host, path = _parse_remote_url("git@github.com:org/repo.git")
        self.assertFalse(path.endswith(".git"))


class TestGetGitlabInfo(unittest.TestCase):
    def test_inferred_from_ssh_url(self):
        gitlab_base, project_path = _get_gitlab_info("git@gitlab.com:group/project.git", None)
        self.assertEqual(gitlab_base, "https://gitlab.com")
        self.assertEqual(project_path, "group/project")

    def test_env_override(self):
        gitlab_base, project_path = _get_gitlab_info("git@gitlab.com:group/project.git", "https://my.gitlab.instance/")
        self.assertEqual(gitlab_base, "https://my.gitlab.instance")
        self.assertEqual(project_path, "group/project")

    def test_unparseable_url_empty_project(self):
        gitlab_base, project_path = _get_gitlab_info("ftp://unknown/path", None)
        self.assertEqual(project_path, "")
        # Falls back to gitlab.com when no host can be inferred
        self.assertEqual(gitlab_base, "https://gitlab.com")


class TestCreateGitlabMr(unittest.TestCase):
    @patch("app_operator.langgraph.tools.urlopen")
    def test_success(self, mock_urlopen):
        import json
        from unittest.mock import MagicMock

        body = json.dumps({"web_url": "https://gitlab.com/group/project/-/merge_requests/1"})
        response = MagicMock()
        response.getcode.return_value = 201
        response.read.return_value = body.encode("utf-8")
        response.__enter__ = lambda s: s
        response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = response

        result = _create_gitlab_mr(
            "https://gitlab.com",
            "group/project",
            "feature-branch",
            "main",
            "token123",
            "My MR",
            None,
        )
        self.assertTrue(result["success"])
        self.assertEqual(
            result["mr_url"],
            "https://gitlab.com/group/project/-/merge_requests/1",
        )
        self.assertIsNone(result["error"])

    @patch("app_operator.langgraph.tools.urlopen")
    def test_http_error(self, mock_urlopen):
        from urllib.error import HTTPError

        err = HTTPError(url="http://x", code=401, msg="Unauthorized", hdrs={}, fp=None)
        err.read = lambda: b"Unauthorized"
        mock_urlopen.side_effect = err

        result = _create_gitlab_mr(
            "https://gitlab.com",
            "group/project",
            "branch",
            "main",
            "bad-token",
            "title",
            None,
        )
        self.assertFalse(result["success"])
        self.assertIn("401", result["error"])

    @patch("app_operator.langgraph.tools.urlopen")
    def test_missing_web_url(self, mock_urlopen):
        import json

        body = json.dumps({"id": 1})
        response = MagicMock()
        response.getcode.return_value = 201
        response.read.return_value = body.encode("utf-8")
        response.__enter__ = lambda s: s
        response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = response

        result = _create_gitlab_mr(
            "https://gitlab.com",
            "group/project",
            "branch",
            "main",
            "token",
            "title",
            None,
        )
        self.assertFalse(result["success"])
        self.assertIn("web_url", result["error"])


class TestStageAndCommit(unittest.TestCase):
    @patch("app_operator.langgraph.tools._run_git")
    def test_no_staged_changes(self, mock_run_git):
        # add -A succeeds, diff --cached returns empty
        def side_effect(args, cwd):
            if args[:2] == ["add", "-A"]:
                return {"success": True, "stdout": "", "stderr": ""}
            if args[:2] == ["diff", "--cached"]:
                return {"success": True, "stdout": "", "stderr": ""}
            return {"success": True, "stdout": "", "stderr": ""}

        mock_run_git.side_effect = side_effect
        git_outputs: list[str] = []
        result = _stage_and_commit("/tmp/repo", Path("/tmp/repo"), "msg", "branch", git_outputs)
        self.assertIsNotNone(result)
        self.assertFalse(result["success"])  # type: ignore[index]
        self.assertIn("No staged changes", result["error"])  # type: ignore[index]

    @patch("app_operator.langgraph.tools._run_git")
    def test_add_failure(self, mock_run_git):
        mock_run_git.return_value = {"success": False, "stdout": "", "stderr": "error"}
        git_outputs: list[str] = []
        result = _stage_and_commit("/tmp/repo", Path("/tmp/repo"), "msg", "branch", git_outputs)
        self.assertIsNotNone(result)
        self.assertFalse(result["success"])  # type: ignore[index]
        self.assertIn("Failed to stage", result["error"])  # type: ignore[index]

    @patch("app_operator.langgraph.tools._run_git")
    def test_successful_commit(self, mock_run_git):
        call_count = {"n": 0}

        def side_effect(args, cwd):
            call_count["n"] += 1
            if args[:2] == ["add", "-A"]:
                return {"success": True, "stdout": "", "stderr": ""}
            if args[:2] == ["diff", "--cached"]:
                return {"success": True, "stdout": "file.py\n", "stderr": ""}
            if args[0] == "commit":
                return {"success": True, "stdout": "1 file changed", "stderr": ""}
            return {"success": True, "stdout": "", "stderr": ""}

        mock_run_git.side_effect = side_effect
        git_outputs: list[str] = []
        result = _stage_and_commit("/tmp/repo", Path("/tmp/repo"), "commit msg", "branch", git_outputs)
        # None means success
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
