"""Unit tests for sregym_agents.crucible.tools."""

from __future__ import annotations

import asyncio
import json
import subprocess
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

if TYPE_CHECKING:
    from pathlib import Path

import pytest

from sregym_agents.crucible.tools import (
    MUTATING_KUBECTL_VERBS,
    JudgeDeps,
    SharedFile,
    SREDeps,
    _check_mutating_kubectl,
    _run_bash_sync,
    _submit_to_benchmark,
    exec_bash_readonly,
    mark_hypothesis_complete,
    mark_mitigation_complete,
    read_file,
    str_replace_file,
    submit_verdict,
    write_file,
)


def _make_sre_ctx(tmp_path: Path, stage: str = "diagnosis") -> MagicMock:
    ctx = MagicMock()
    ctx.deps = SREDeps(
        namespace="ns",
        shared_file=SharedFile(tmp_path / "shared.md"),
        iteration=1,
        stage=stage,
    )
    return ctx


def _make_judge_ctx(tmp_path: Path, stage: str = "diagnosis") -> MagicMock:
    ctx = MagicMock()
    ctx.deps = JudgeDeps(
        namespace="ns",
        shared_file=SharedFile(tmp_path / "shared.md"),
        iteration=1,
        stage=stage,
        submit_mcp_url="http://localhost:9000/sse",
    )
    return ctx


# ---------------------------------------------------------------------------
# SharedFile
# ---------------------------------------------------------------------------


class TestSharedFile:
    def test_init_creates_file_with_content(self, tmp_path: Path):
        sf = SharedFile(tmp_path / "state.md")
        sf.init("# header\n")
        assert (tmp_path / "state.md").read_text() == "# header\n"

    def test_init_skips_existing_file(self, tmp_path: Path):
        p = tmp_path / "state.md"
        p.write_text("original")
        sf = SharedFile(p)
        sf.init("new content")
        assert p.read_text() == "original"

    def test_append_writes_content(self, tmp_path: Path):
        p = tmp_path / "state.md"
        p.write_text("line1\n")
        sf = SharedFile(p)
        sf.append("line2\n")
        assert p.read_text() == "line1\nline2\n"

    def test_append_acquires_exclusive_lock(self, tmp_path: Path):
        p = tmp_path / "state.md"
        p.write_text("")
        sf = SharedFile(p)
        with patch("fcntl.flock") as mock_flock:
            sf.append("text")
        calls = [c.args[1] for c in mock_flock.call_args_list]
        import fcntl as _fcntl
        assert _fcntl.LOCK_EX in calls
        assert _fcntl.LOCK_UN in calls

    def test_read_returns_file_contents(self, tmp_path: Path):
        p = tmp_path / "state.md"
        p.write_text("hello world")
        sf = SharedFile(p)
        assert sf.read() == "hello world"

    def test_str_returns_path_string(self, tmp_path: Path):
        p = tmp_path / "state.md"
        assert str(SharedFile(p)) == str(p)


# ---------------------------------------------------------------------------
# _run_bash_sync
# ---------------------------------------------------------------------------


class TestRunBashSync:
    def test_successful_command_returns_stdout(self):
        result = MagicMock()
        result.returncode = 0
        result.stdout = "hello world"
        result.stderr = ""
        with patch("subprocess.run", return_value=result):
            output = _run_bash_sync("echo hello world")
        assert output == "hello world"

    def test_nonzero_exit_appends_stderr(self):
        result = MagicMock()
        result.returncode = 1
        result.stdout = "out"
        result.stderr = "err msg"
        with patch("subprocess.run", return_value=result):
            output = _run_bash_sync("false")
        assert "out" in output
        assert "STDERR:" in output
        assert "err msg" in output

    def test_timeout_returns_error_string(self):
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("cmd", 60)):
            output = _run_bash_sync("sleep 999")
        assert "timed out" in output.lower()

    def test_long_output_truncated_to_file(self, tmp_path: Path):
        result = MagicMock()
        result.returncode = 0
        result.stdout = "x" * 5000
        result.stderr = ""
        with patch("subprocess.run", return_value=result), patch("sregym_agents.crucible.tools.Path") as mock_path_cls:
            mock_path_cls.return_value.write_text = MagicMock()
            output = _run_bash_sync("bigcmd")
        assert "truncated" in output.lower() or "Output truncated" in output


# ---------------------------------------------------------------------------
# _check_mutating_kubectl
# ---------------------------------------------------------------------------


class TestCheckMutatingKubectl:
    def test_non_kubectl_returns_none(self):
        assert _check_mutating_kubectl("echo foo") is None

    @pytest.mark.parametrize("verb", ["get", "describe", "logs", "top"])
    def test_readonly_kubectl_returns_none(self, verb: str):
        assert _check_mutating_kubectl(f"kubectl {verb} pods") is None

    @pytest.mark.parametrize("verb", list(MUTATING_KUBECTL_VERBS))
    def test_mutating_verb_returns_error(self, verb: str):
        result = _check_mutating_kubectl(f"kubectl {verb} something")
        assert result is not None
        assert verb in result

    def test_flags_before_verb_blocked(self):
        # Flags that take no value (e.g. --dry-run) are skipped correctly.
        result = _check_mutating_kubectl("kubectl --dry-run delete pod mypod")
        assert result is not None
        assert "delete" in result

    def test_flag_with_value_before_verb_not_blocked(self):
        # Known limitation: `-n ns` causes `ns` to be treated as the verb candidate;
        # since `ns` is not in MUTATING_KUBECTL_VERBS, the check returns None even
        # though `delete` follows it. Tests document current behaviour, not ideal.
        result = _check_mutating_kubectl("kubectl -n ns delete pod mypod")
        assert result is None

    def test_malformed_quoting_returns_none(self):
        assert _check_mutating_kubectl("kubectl 'unclosed") is None


# ---------------------------------------------------------------------------
# _submit_to_benchmark
# ---------------------------------------------------------------------------


class TestSubmitToBenchmark:
    def _run(self, coro):
        return asyncio.get_event_loop().run_until_complete(coro)

    def _make_mock_session(self, raw_response: str):
        mock_result = MagicMock()
        mock_result.content = [MagicMock(text=raw_response)]
        mock_session = AsyncMock()
        mock_session.call_tool = AsyncMock(return_value=mock_result)
        mock_session.initialize = AsyncMock()
        return mock_session

    def test_success(self):
        oracle = {"Diagnosis": {"success": True, "score": 1.0}}
        raw = repr({"status": "200", "text": json.dumps(oracle)})
        mock_session = self._make_mock_session(raw)

        with patch("mcp.ClientSession") as mock_cs, patch("mcp.client.sse.sse_client") as mock_sse:
            mock_sse.return_value.__aenter__ = AsyncMock(return_value=("r", "w"))
            mock_sse.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_cs.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_cs.return_value.__aexit__ = AsyncMock(return_value=False)

            success, msg, result_oracle = self._run(_submit_to_benchmark("http://x/sse", "my answer", "diagnosis"))

        assert success is True
        assert "accepted" in msg.lower()
        assert result_oracle == oracle

    def test_non_200_status(self):
        raw = repr({"status": "500", "text": "{}"})
        mock_session = self._make_mock_session(raw)

        with patch("mcp.ClientSession") as mock_cs, patch("mcp.client.sse.sse_client") as mock_sse:
            mock_sse.return_value.__aenter__ = AsyncMock(return_value=("r", "w"))
            mock_sse.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_cs.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_cs.return_value.__aexit__ = AsyncMock(return_value=False)

            success, msg, oracle = self._run(_submit_to_benchmark("http://x/sse", "answer", "diagnosis"))

        assert success is False
        assert oracle is None

    def test_unparseable_response(self):
        mock_session = self._make_mock_session("not a dict at all !!!{}")

        with patch("mcp.ClientSession") as mock_cs, patch("mcp.client.sse.sse_client") as mock_sse:
            mock_sse.return_value.__aenter__ = AsyncMock(return_value=("r", "w"))
            mock_sse.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_cs.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_cs.return_value.__aexit__ = AsyncMock(return_value=False)

            success, msg, oracle = self._run(_submit_to_benchmark("http://x/sse", "answer", "diagnosis"))

        assert success is False
        assert "Failed to parse" in msg
        assert oracle is None

    def test_invalid_json_in_text_field(self):
        raw = repr({"status": "200", "text": "not valid json"})
        mock_session = self._make_mock_session(raw)

        with patch("mcp.ClientSession") as mock_cs, patch("mcp.client.sse.sse_client") as mock_sse:
            mock_sse.return_value.__aenter__ = AsyncMock(return_value=("r", "w"))
            mock_sse.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_cs.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_cs.return_value.__aexit__ = AsyncMock(return_value=False)

            success, msg, oracle = self._run(_submit_to_benchmark("http://x/sse", "answer", "diagnosis"))

        assert success is False
        assert "not valid JSON" in msg

    def test_stage_not_in_oracle(self):
        oracle = {"Mitigation": {"success": True}}
        raw = repr({"status": "200", "text": json.dumps(oracle)})
        mock_session = self._make_mock_session(raw)

        with patch("mcp.ClientSession") as mock_cs, patch("mcp.client.sse.sse_client") as mock_sse:
            mock_sse.return_value.__aenter__ = AsyncMock(return_value=("r", "w"))
            mock_sse.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_cs.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_cs.return_value.__aexit__ = AsyncMock(return_value=False)

            success, msg, result_oracle = self._run(_submit_to_benchmark("http://x/sse", "answer", "diagnosis"))

        assert success is False
        assert "rejected" in msg.lower()
        assert result_oracle == oracle

    def test_success_false_in_oracle(self):
        oracle = {"Diagnosis": {"success": False}}
        raw = repr({"status": "200", "text": json.dumps(oracle)})
        mock_session = self._make_mock_session(raw)

        with patch("mcp.ClientSession") as mock_cs, patch("mcp.client.sse.sse_client") as mock_sse:
            mock_sse.return_value.__aenter__ = AsyncMock(return_value=("r", "w"))
            mock_sse.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_cs.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_cs.return_value.__aexit__ = AsyncMock(return_value=False)

            success, msg, result_oracle = self._run(_submit_to_benchmark("http://x/sse", "answer", "diagnosis"))

        assert success is False
        assert result_oracle == oracle


# ---------------------------------------------------------------------------
# read_file
# ---------------------------------------------------------------------------


class TestReadFile:
    def test_returns_numbered_lines(self, tmp_path: Path):
        f = tmp_path / "f.txt"
        f.write_text("line1\nline2\nline3\n")
        ctx = _make_sre_ctx(tmp_path)
        result = read_file(ctx, str(f))
        assert "line1" in result
        assert "line2" in result
        # Should have line numbers in cat -n style
        assert "1\t" in result

    def test_missing_file_returns_error(self, tmp_path: Path):
        ctx = _make_sre_ctx(tmp_path)
        result = read_file(ctx, str(tmp_path / "missing.txt"))
        assert "Error: File not found" in result

    def test_start_end_slicing(self, tmp_path: Path):
        f = tmp_path / "f.txt"
        f.write_text("\n".join(f"line{i}" for i in range(10)))
        ctx = _make_sre_ctx(tmp_path)
        result = read_file(ctx, str(f), start_line=2, end_line=5)
        assert "line2" in result
        assert "line4" in result
        assert "line0" not in result
        assert "line5" not in result

    def test_end_line_minus_one_reads_to_end(self, tmp_path: Path):
        f = tmp_path / "f.txt"
        f.write_text("a\nb\nc\n")
        ctx = _make_sre_ctx(tmp_path)
        result = read_file(ctx, str(f), start_line=1, end_line=-1)
        assert "b" in result
        assert "c" in result
        assert "a" not in result

    def test_empty_range_returns_marker(self, tmp_path: Path):
        f = tmp_path / "f.txt"
        f.write_text("a\nb\nc\n")
        ctx = _make_sre_ctx(tmp_path)
        result = read_file(ctx, str(f), start_line=2, end_line=2)
        assert result == "(empty range)"


# ---------------------------------------------------------------------------
# write_file
# ---------------------------------------------------------------------------


class TestWriteFile:
    def test_creates_dirs_and_writes(self, tmp_path: Path):
        ctx = _make_sre_ctx(tmp_path)
        dest = tmp_path / "sub" / "dir" / "file.txt"
        result = write_file(ctx, str(dest), "hello")
        assert dest.read_text() == "hello"
        assert "bytes" in result

    def test_error_on_unwritable_path(self, tmp_path: Path):
        ctx = _make_sre_ctx(tmp_path)
        # Writing to a directory path as if it's a file should fail
        result = write_file(ctx, "/proc/nonexistent_path/file.txt", "data")
        assert "Error" in result


# ---------------------------------------------------------------------------
# str_replace_file
# ---------------------------------------------------------------------------


class TestStrReplaceFile:
    def test_replaces_first_occurrence(self, tmp_path: Path):
        f = tmp_path / "f.txt"
        f.write_text("aaa bbb aaa")
        ctx = _make_sre_ctx(tmp_path)
        result = str_replace_file(ctx, str(f), "aaa", "XXX")
        assert result.startswith("Replaced")
        assert f.read_text() == "XXX bbb aaa"

    def test_old_str_not_found_returns_error(self, tmp_path: Path):
        f = tmp_path / "f.txt"
        f.write_text("hello world")
        ctx = _make_sre_ctx(tmp_path)
        result = str_replace_file(ctx, str(f), "nothere", "x")
        assert "Error" in result
        assert "not found" in result

    def test_file_not_found_returns_error(self, tmp_path: Path):
        ctx = _make_sre_ctx(tmp_path)
        result = str_replace_file(ctx, str(tmp_path / "missing.txt"), "x", "y")
        assert "Error: File not found" in result


# ---------------------------------------------------------------------------
# exec_bash_readonly
# ---------------------------------------------------------------------------


class TestExecBashReadonly:
    def test_allowed_command_delegates(self, tmp_path: Path):
        ctx = _make_judge_ctx(tmp_path)
        with patch("sregym_agents.crucible.tools._run_bash_sync", return_value="ok") as mock_run:
            result = exec_bash_readonly(ctx, "ls -la")
        mock_run.assert_called_once_with("ls -la")
        assert result == "ok"

    def test_mutating_kubectl_blocked(self, tmp_path: Path):
        ctx = _make_judge_ctx(tmp_path)
        with patch("sregym_agents.crucible.tools._run_bash_sync") as mock_run:
            result = exec_bash_readonly(ctx, "kubectl delete pod mypod")
        mock_run.assert_not_called()
        assert "Error" in result
        assert "delete" in result


# ---------------------------------------------------------------------------
# mark_hypothesis_complete
# ---------------------------------------------------------------------------


class TestMarkHypothesisComplete:
    def test_empty_diagnosis_returns_error(self, tmp_path: Path):
        ctx = _make_sre_ctx(tmp_path)
        result = mark_hypothesis_complete(ctx, "   ", "some justification")
        assert "Error" in result
        assert ctx.deps.state.submitted is False

    def test_empty_justification_returns_error(self, tmp_path: Path):
        ctx = _make_sre_ctx(tmp_path)
        result = mark_hypothesis_complete(ctx, "diagnosis text", "   ")
        assert "Error" in result
        assert ctx.deps.state.submitted is False

    def test_valid_args_appends_to_file(self, tmp_path: Path):
        shared = tmp_path / "shared.md"
        shared.write_text("# header\n")
        ctx = _make_sre_ctx(tmp_path)
        result = mark_hypothesis_complete(ctx, "disk full", "saw 100% disk usage")
        assert ctx.deps.state.submitted is True
        content = shared.read_text()
        assert "disk full" in content
        assert "saw 100% disk usage" in content
        assert "Hypothesis" in result

    def test_file_write_error_returns_error_string(self, tmp_path: Path):
        ctx = _make_sre_ctx(tmp_path)
        # shared_file points to a directory — open("a") will fail
        ctx.deps.shared_file = SharedFile(tmp_path)
        result = mark_hypothesis_complete(ctx, "diagnosis", "justification")
        assert "Error" in result
        assert ctx.deps.state.submitted is False


# ---------------------------------------------------------------------------
# mark_mitigation_complete
# ---------------------------------------------------------------------------


class TestMarkMitigationComplete:
    def test_empty_mitigation_returns_error(self, tmp_path: Path):
        ctx = _make_sre_ctx(tmp_path, stage="mitigation")
        result = mark_mitigation_complete(ctx, "   ", "justification")
        assert "Error" in result
        assert ctx.deps.state.submitted is False

    def test_empty_justification_returns_error(self, tmp_path: Path):
        ctx = _make_sre_ctx(tmp_path, stage="mitigation")
        result = mark_mitigation_complete(ctx, "restarted pod", "   ")
        assert "Error" in result
        assert ctx.deps.state.submitted is False

    def test_valid_args_appends_to_file(self, tmp_path: Path):
        shared = tmp_path / "shared.md"
        shared.write_text("# header\n")
        ctx = _make_sre_ctx(tmp_path, stage="mitigation")
        result = mark_mitigation_complete(ctx, "restarted pod", "pod was crashlooping")
        assert ctx.deps.state.submitted is True
        content = shared.read_text()
        assert "restarted pod" in content
        assert "pod was crashlooping" in content
        assert "Mitigation" in result

    def test_file_write_error_returns_error_string(self, tmp_path: Path):
        ctx = _make_sre_ctx(tmp_path, stage="mitigation")
        ctx.deps.shared_file = SharedFile(tmp_path)
        result = mark_mitigation_complete(ctx, "mitigation", "justification")
        assert "Error" in result
        assert ctx.deps.state.submitted is False


# ---------------------------------------------------------------------------
# submit_verdict
# ---------------------------------------------------------------------------


class TestSubmitVerdict:
    def test_reject_sets_state_no_benchmark_call(self, tmp_path: Path):
        shared = tmp_path / "shared.md"
        shared.write_text("")
        ctx = _make_judge_ctx(tmp_path)

        with patch("sregym_agents.crucible.tools._run_async") as mock_run:
            submit_verdict(ctx, False, "not good enough", "answer")

        mock_run.assert_not_called()
        assert ctx.deps.state.submitted is True
        assert ctx.deps.state.verdict == "REJECTED"

    def test_approve_calls_benchmark(self, tmp_path: Path):
        shared = tmp_path / "shared.md"
        shared.write_text("")
        ctx = _make_judge_ctx(tmp_path)

        oracle = {"Diagnosis": {"success": True}}
        with patch("sregym_agents.crucible.tools._run_async", return_value=(True, "Benchmark accepted...", oracle)):
            result = submit_verdict(ctx, True, "great work", "answer")

        assert ctx.deps.state.submitted is True
        assert ctx.deps.state.verdict == "APPROVED"
        assert "<benchmark_result>" in result

    def test_benchmark_exception_writes_error_block(self, tmp_path: Path):
        shared = tmp_path / "shared.md"
        shared.write_text("")
        ctx = _make_judge_ctx(tmp_path)

        with patch("sregym_agents.crucible.tools._run_async", side_effect=RuntimeError("connection refused")):
            result = submit_verdict(ctx, True, "great", "answer")

        assert ctx.deps.state.submitted is True
        assert "<benchmark_result>" in result
        assert "Error" in result

    def test_file_write_error_returns_error_string(self, tmp_path: Path):
        ctx = _make_judge_ctx(tmp_path)
        ctx.deps.shared_file = SharedFile(tmp_path)  # directory — open will fail

        with patch("sregym_agents.crucible.tools._run_async", return_value=(False, "rejected", None)):
            result = submit_verdict(ctx, False, "reason", "answer")

        assert "Error writing verdict" in result

    def test_submit_verdict_from_running_loop(self, tmp_path: Path):
        """Regression: submit_verdict must not raise RuntimeError when called from within asyncio.run()."""
        shared = tmp_path / "shared.md"
        shared.write_text("")
        ctx = _make_judge_ctx(tmp_path)

        async def _run():
            with patch(
                "sregym_agents.crucible.tools._submit_to_benchmark",
                new_callable=AsyncMock,
                return_value=(True, "Benchmark accepted", {"Diagnosis": {"success": True}}),
            ):
                return submit_verdict(ctx, True, "great work", "answer")

        result = asyncio.run(_run())
        assert ctx.deps.state.verdict == "APPROVED"
        assert "<benchmark_result>" in result
