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


class TestBuildSpawnSubagentTokenTracking(unittest.TestCase):
    """Tests for nested subagent token usage tracking."""

    def _make_ai_message(self, input_tokens: int, output_tokens: int) -> MagicMock:
        msg = MagicMock()
        msg.__class__ = __import__("langchain_core.messages", fromlist=["AIMessage"]).AIMessage
        msg.usage_metadata = {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        }
        msg.content = "done"
        return msg

    def test_nested_subagent_token_structure(self):
        """Depth-0 spawning depth-1: parent record includes own + child tokens."""
        from unittest.mock import MagicMock, patch

        from langchain_core.messages import AIMessage

        # Build mocked stream chunks: one AIMessage per level
        depth1_msg = MagicMock(spec=AIMessage)
        depth1_msg.usage_metadata = {
            "input_tokens": 3,
            "output_tokens": 4,
            "total_tokens": 7,
        }
        depth1_msg.content = "child done"

        depth0_msg = MagicMock(spec=AIMessage)
        depth0_msg.usage_metadata = {
            "input_tokens": 10,
            "output_tokens": 5,
            "total_tokens": 15,
        }
        depth0_msg.content = "parent done"

        def make_stream(messages):
            for msg in messages:
                yield {"agent": {"messages": [msg]}}

        parent_sink: list = []

        # Patch create_react_agent so depth-1 subagent uses depth1 messages
        # and depth-0 subagent uses depth-0 messages (after calling its child tool)
        call_count = [0]

        def fake_create_react_agent(llm, tools, pre_model_hook=None):
            agent = MagicMock()
            idx = call_count[0]
            call_count[0] += 1
            if idx == 0:
                # depth-0 agent: stream depth0_msg, but also call its child tool first
                def stream_depth0(state, stream_mode, config):
                    # Invoke the spawn_subagent tool (depth-1) before yielding own msg
                    spawn = next(t for t in tools if getattr(t, "name", None) == "spawn_subagent")
                    spawn.invoke({"prompt": "do subtask"})
                    return make_stream([depth0_msg])

                agent.stream = stream_depth0
            else:
                # depth-1 agent: just stream depth1_msg
                agent.stream = lambda state, stream_mode, config: make_stream([depth1_msg])
            return agent

        from app_operator.langgraph import tools as tools_module

        with patch.object(tools_module, "create_react_agent", side_effect=fake_create_react_agent):
            spawn = tools_module._build_spawn_subagent(
                llm=MagicMock(),
                base_tools=[],
                compaction_hook=None,
                current_depth=0,
                max_depth=2,
                token_sink=parent_sink,
            )
            spawn.invoke({"prompt": "top task"})

        self.assertEqual(len(parent_sink), 1)
        record = parent_sink[0]

        # own tokens = depth-0 LLM only
        self.assertEqual(record["own_input"], 10)
        self.assertEqual(record["own_output"], 5)
        self.assertEqual(record["own_total"], 15)

        # total includes child
        self.assertEqual(record["input"], 10 + 3)
        self.assertEqual(record["output"], 5 + 4)
        self.assertEqual(record["total"], 15 + 7)

        # nested subagents list
        self.assertEqual(len(record["subagents"]), 1)
        child = record["subagents"][0]
        self.assertEqual(child["agent"], "subagent_d1")
        self.assertEqual(child["own_total"], 7)
        self.assertEqual(child["total"], 7)
        self.assertEqual(child["subagents"], [])

    def test_three_level_nesting(self):
        """Depth-0 → depth-1 → depth-2: tokens bubble up through all levels."""
        from langchain_core.messages import AIMessage

        # Token values per level
        # d0_own=100, d1_own=20, d2_own=5
        # Expected: d2.total=5, d1.total=25, d0.total=125
        def make_ai_msg(n: int) -> MagicMock:
            msg = MagicMock(spec=AIMessage)
            msg.usage_metadata = {
                "input_tokens": n,
                "output_tokens": 0,
                "total_tokens": n,
            }
            msg.content = f"done-{n}"
            return msg

        def make_stream(msg):
            yield {"agent": {"messages": [msg]}}

        parent_sink: list = []
        call_count = [0]

        def fake_create_react_agent(llm, tools, pre_model_hook=None):
            agent = MagicMock()
            idx = call_count[0]
            call_count[0] += 1
            if idx == 0:
                # depth-0: spawns depth-1, then reports 100 own tokens
                def stream_d0(state, stream_mode, config):
                    spawn = next(t for t in tools if getattr(t, "name", None) == "spawn_subagent")
                    spawn.invoke({"prompt": "d1 task"})
                    return make_stream(make_ai_msg(100))

                agent.stream = stream_d0
            elif idx == 1:
                # depth-1: spawns depth-2, then reports 20 own tokens
                def stream_d1(state, stream_mode, config):
                    spawn = next(t for t in tools if getattr(t, "name", None) == "spawn_subagent")
                    spawn.invoke({"prompt": "d2 task"})
                    return make_stream(make_ai_msg(20))

                agent.stream = stream_d1
            else:
                # depth-2: leaf, reports 5 own tokens
                agent.stream = lambda state, stream_mode, config: make_stream(make_ai_msg(5))
            return agent

        from app_operator.langgraph import tools as tools_module

        with patch.object(tools_module, "create_react_agent", side_effect=fake_create_react_agent):
            spawn = tools_module._build_spawn_subagent(
                llm=MagicMock(),
                base_tools=[],
                compaction_hook=None,
                current_depth=0,
                max_depth=3,
                token_sink=parent_sink,
            )
            spawn.invoke({"prompt": "top task"})

        # Only one top-level entry
        self.assertEqual(len(parent_sink), 1)
        d0 = parent_sink[0]
        self.assertEqual(d0["agent"], "subagent_d0")
        self.assertEqual(d0["own_total"], 100)
        self.assertEqual(d0["total"], 125)  # 100 + 20 + 5

        self.assertEqual(len(d0["subagents"]), 1)
        d1 = d0["subagents"][0]
        self.assertEqual(d1["agent"], "subagent_d1")
        self.assertEqual(d1["own_total"], 20)
        self.assertEqual(d1["total"], 25)  # 20 + 5

        self.assertEqual(len(d1["subagents"]), 1)
        d2 = d1["subagents"][0]
        self.assertEqual(d2["agent"], "subagent_d2")
        self.assertEqual(d2["own_total"], 5)
        self.assertEqual(d2["total"], 5)
        self.assertEqual(d2["subagents"], [])


if __name__ == "__main__":
    unittest.main()
