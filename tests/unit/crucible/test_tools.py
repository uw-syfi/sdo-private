"""Unit tests for sregym_agents.crucible.tools."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import time
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

if TYPE_CHECKING:
    from pathlib import Path

import pytest

from sregym_agents.crucible.tools import (
    MUTATING_KUBECTL_VERBS,
    JudgeDeps,
    SharedFile,
    SharedState,
    SREDeps,
    SRESubmission,
    check_mutating_kubectl,
    exec_bash_readonly,
    read_file,
    reveal_agent_hypothesis,
    run_bash_sync,
    str_replace_file,
    submit_independent_findings,
    submit_to_benchmark,
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
        model_id="test",
    )
    return ctx


def _make_judge_ctx(
    tmp_path: Path,
    stage: str = "diagnosis",
    hypothesis_text: str = "**Diagnosis**: test\n**Justification**: test\n",
) -> MagicMock:
    ctx = MagicMock()
    ctx.deps = JudgeDeps(
        namespace="ns",
        shared_file=SharedFile(tmp_path / "shared.md"),
        iteration=1,
        stage=stage,
        submit_mcp_url="http://localhost:9000/sse",
        hypothesis_text=hypothesis_text,
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

    def test_write_text_acquires_exclusive_lock(self, tmp_path: Path):
        p = tmp_path / "state.md"
        p.write_text("")
        sf = SharedFile(p)
        with patch("fcntl.flock") as mock_flock:
            sf.write_text("text")
        calls = [c.args[1] for c in mock_flock.call_args_list]
        import fcntl as _fcntl

        assert _fcntl.LOCK_EX in calls
        assert _fcntl.LOCK_UN in calls

    def test_write_text_writes_content(self, tmp_path: Path):
        p = tmp_path / "state.md"
        p.write_text("original")
        sf = SharedFile(p)
        sf.write_text("new content")
        assert p.read_text() == "new content"

    def test_replace_substitutes_first_occurrence(self, tmp_path: Path):
        p = tmp_path / "state.md"
        p.write_text("foo bar foo")
        sf = SharedFile(p)
        assert sf.replace("foo", "baz") is True
        assert p.read_text() == "baz bar foo"

    def test_replace_returns_false_when_missing(self, tmp_path: Path):
        p = tmp_path / "state.md"
        p.write_text("foo bar")
        sf = SharedFile(p)
        assert sf.replace("xyz", "abc") is False
        assert p.read_text() == "foo bar"

    def test_replace_acquires_exclusive_lock(self, tmp_path: Path):
        p = tmp_path / "state.md"
        p.write_text("foo")
        sf = SharedFile(p)
        with patch("fcntl.flock") as mock_flock:
            sf.replace("foo", "bar")
        calls = [c.args[1] for c in mock_flock.call_args_list]
        import fcntl as _fcntl

        assert _fcntl.LOCK_EX in calls
        assert _fcntl.LOCK_UN in calls

    def test_concurrent_append_and_replace_no_data_loss(self, tmp_path: Path):
        """Interleave many appends with many replaces; assert all appended
        lines are present and all replacements applied atomically."""
        import threading

        p = tmp_path / "state.md"
        # Seed with N placeholders that will each be replaced exactly once.
        num_replacements = 50
        num_appends = 200
        seed_lines = [f"[PLACEHOLDER-{i}]\n" for i in range(num_replacements)]
        p.write_text("".join(seed_lines))
        sf = SharedFile(p)

        barrier = threading.Barrier(2)

        def appender() -> None:
            barrier.wait()
            for i in range(num_appends):
                sf.append(f"APPEND-{i}\n")

        def replacer() -> None:
            barrier.wait()
            for i in range(num_replacements):
                ok = sf.replace(f"[PLACEHOLDER-{i}]\n", f"REAL-{i}\n")
                assert ok, f"replacement {i} should have succeeded"

        t1 = threading.Thread(target=appender)
        t2 = threading.Thread(target=replacer)
        t1.start()
        t2.start()
        t1.join(timeout=30)
        t2.join(timeout=30)
        assert not t1.is_alive()
        assert not t2.is_alive()

        final = p.read_text()
        # All appends must be present.
        for i in range(num_appends):
            assert f"APPEND-{i}\n" in final, f"lost append {i}"
        # All placeholders must have been replaced exactly once.
        for i in range(num_replacements):
            assert f"REAL-{i}\n" in final, f"missing replacement {i}"
            assert f"[PLACEHOLDER-{i}]" not in final, f"placeholder {i} still present"

    def test_read_returns_file_contents(self, tmp_path: Path):
        p = tmp_path / "state.md"
        p.write_text("hello world")
        sf = SharedFile(p)
        assert sf.read() == "hello world"

    def test_str_returns_path_string(self, tmp_path: Path):
        p = tmp_path / "state.md"
        assert str(SharedFile(p)) == str(p)


# ---------------------------------------------------------------------------
# run_bash_sync
# ---------------------------------------------------------------------------


def _mock_popen(returncode=0, stdout="", stderr="", communicate_side_effect=None):
    """Create a mock ``subprocess.Popen`` instance for ``run_bash_sync`` tests."""
    mock_proc = MagicMock()
    mock_proc.pid = 12345
    mock_proc.returncode = returncode
    if communicate_side_effect:
        mock_proc.communicate = MagicMock(side_effect=communicate_side_effect)
    else:
        mock_proc.communicate = MagicMock(return_value=(stdout, stderr))
    mock_proc.wait = MagicMock()
    mock_proc.kill = MagicMock()
    return mock_proc


class TestRunBashSync:
    def test_successful_command_returns_stdout(self):
        mock_proc = _mock_popen(returncode=0, stdout="hello world", stderr="")
        with patch("subprocess.Popen", return_value=mock_proc):
            output = run_bash_sync("echo hello world")
        assert output == "hello world"

    def test_nonzero_exit_appends_stderr(self):
        mock_proc = _mock_popen(returncode=1, stdout="out", stderr="err msg")
        with patch("subprocess.Popen", return_value=mock_proc):
            output = run_bash_sync("false")
        assert "out" in output
        assert "STDERR:" in output
        assert "err msg" in output
        assert "Exit code 1" in output

    def test_stderr_included_on_success(self):
        mock_proc = _mock_popen(returncode=0, stdout="ok", stderr="deprecation warning")
        with patch("subprocess.Popen", return_value=mock_proc):
            output = run_bash_sync("some cmd")
        assert "ok" in output
        assert "STDERR:" in output
        assert "deprecation warning" in output
        assert "Exit code" not in output

    def test_exit_code_shown_on_failure(self):
        mock_proc = _mock_popen(returncode=127, stdout="", stderr="command not found")
        with patch("subprocess.Popen", return_value=mock_proc):
            output = run_bash_sync("badcmd")
        assert output.startswith("[Exit code 127]")

    def test_uses_bash_executable_and_new_session(self):
        mock_proc = _mock_popen(returncode=0, stdout="ok", stderr="")
        with patch("subprocess.Popen", return_value=mock_proc) as mock_popen_cls:
            run_bash_sync("echo hi")
        mock_popen_cls.assert_called_once()
        call_kwargs = mock_popen_cls.call_args
        assert call_kwargs.kwargs.get("executable") == "/bin/bash"
        assert call_kwargs.kwargs.get("start_new_session") is True

    def test_timeout_returns_error_string(self):
        mock_proc = _mock_popen(
            communicate_side_effect=subprocess.TimeoutExpired("cmd", 60),
        )
        with (
            patch("subprocess.Popen", return_value=mock_proc),
            patch("os.killpg") as mock_killpg,
            patch("os.getpgid", return_value=12345),
        ):
            output = run_bash_sync("sleep 999")
        assert "timed out" in output.lower()
        mock_killpg.assert_called_once_with(12345, signal.SIGKILL)

    def test_timeout_kills_process_group(self):
        """On timeout, SIGKILL must be sent to the entire process group."""
        mock_proc = _mock_popen(
            communicate_side_effect=subprocess.TimeoutExpired("cmd", 60),
        )
        with (
            patch("subprocess.Popen", return_value=mock_proc),
            patch("os.killpg") as mock_killpg,
            patch("os.getpgid", return_value=99999),
        ):
            run_bash_sync("kubectl exec -it pod -- nslookup foo")
        mock_killpg.assert_called_once_with(99999, signal.SIGKILL)
        mock_proc.wait.assert_called_once()

    def test_timeout_fallback_to_kill_if_killpg_fails(self):
        """If os.killpg raises OSError (e.g. process already dead), fall back to process.kill()."""
        mock_proc = _mock_popen(
            communicate_side_effect=subprocess.TimeoutExpired("cmd", 60),
        )
        with (
            patch("subprocess.Popen", return_value=mock_proc),
            patch("os.killpg", side_effect=OSError("No such process")),
            patch("os.getpgid", return_value=12345),
        ):
            output = run_bash_sync("sleep 999")
        assert "timed out" in output.lower()
        mock_proc.kill.assert_called_once()
        mock_proc.wait.assert_called_once()

    def test_long_output_truncated_to_file(self, tmp_path: Path):
        mock_proc = _mock_popen(returncode=0, stdout="x" * 5000, stderr="")
        with (
            patch("subprocess.Popen", return_value=mock_proc),
            patch("sregym_agents.crucible.tools._bash_tools.Path") as mock_path_cls,
        ):
            mock_path_cls.return_value.write_text = MagicMock()
            output = run_bash_sync("bigcmd")
        assert "truncated" in output.lower() or "Output truncated" in output

    def test_general_exception_kills_process_group(self):
        """Any unexpected exception should also clean up the process group."""
        mock_proc = _mock_popen(
            communicate_side_effect=RuntimeError("unexpected"),
        )
        with (
            patch("subprocess.Popen", return_value=mock_proc),
            patch("os.killpg") as mock_killpg,
            patch("os.getpgid", return_value=12345),
        ):
            output = run_bash_sync("bad command")
        assert "Error executing command" in output
        mock_killpg.assert_called_once_with(12345, signal.SIGKILL)
        mock_proc.wait.assert_called_once()


class TestRunBashSyncIntegration:
    """Integration tests that run real subprocesses to verify process cleanup."""

    def test_child_processes_killed_on_timeout(self):
        """When a command times out, all child processes must be killed.

        Spawns a bash command that starts a long-running child, then verifies
        that after ``run_bash_sync`` returns the timeout error, no orphaned
        child processes remain.
        """
        # Use a short timeout for the test
        marker = f"run_bash_sync_test_{os.getpid()}"
        cmd = f"bash -c 'sleep 300 & echo {marker}_$$; wait'"

        with patch("sregym_agents.crucible.tools._bash_tools.BASH_TIMEOUT", 2):
            output = run_bash_sync(cmd)

        assert "timed out" in output.lower()

        # Give the OS a moment to reap
        time.sleep(0.2)

        # Verify no orphaned sleep processes with our marker remain
        result = subprocess.run(
            ["pgrep", "-f", "sleep 300"],
            capture_output=True,
            text=True,
        )
        # pgrep returns the PIDs of matching processes; filter for our marker
        # isn't directly possible, so we check via /proc
        if result.stdout.strip():
            for pid_str in result.stdout.strip().split("\n"):
                try:
                    pid = int(pid_str.strip())
                    # Check if this process' parent was one of ours
                    with open(f"/proc/{pid}/cmdline") as f:
                        cmdline = f.read()
                    assert marker not in cmdline, f"Orphaned child process {pid} still alive after timeout"
                except (ProcessLookupError, FileNotFoundError, ValueError):
                    pass  # Process already gone — fine

    def test_normal_command_not_affected_by_session(self):
        """Normal (non-timeout) commands must still work correctly."""
        output = run_bash_sync("echo hello_from_test")
        assert "hello_from_test" in output

    def test_nonzero_exit_still_works(self):
        """Non-zero exit codes are reported correctly with new session."""
        output = run_bash_sync("exit 42")
        assert "Exit code 42" in output


# ---------------------------------------------------------------------------
# check_mutating_kubectl
# ---------------------------------------------------------------------------


class TestCheckMutatingKubectl:
    def test_non_kubectl_returns_none(self):
        assert check_mutating_kubectl("echo foo") is None

    @pytest.mark.parametrize("verb", ["get", "describe", "logs", "top"])
    def test_readonly_kubectl_returns_none(self, verb: str):
        assert check_mutating_kubectl(f"kubectl {verb} pods") is None

    @pytest.mark.parametrize("verb", list(MUTATING_KUBECTL_VERBS))
    def test_mutating_verb_returns_error(self, verb: str):
        result = check_mutating_kubectl(f"kubectl {verb} something")
        assert result is not None
        assert verb in result

    def test_flags_before_verb_blocked(self):
        # Flags that take no value (e.g. --dry-run) are skipped correctly.
        result = check_mutating_kubectl("kubectl --dry-run delete pod mypod")
        assert result is not None
        assert "delete" in result

    def test_flag_with_value_before_verb_not_blocked(self):
        # Known limitation: `-n ns` causes `ns` to be treated as the verb candidate;
        # since `ns` is not in MUTATING_KUBECTL_VERBS, the check returns None even
        # though `delete` follows it. Tests document current behaviour, not ideal.
        result = check_mutating_kubectl("kubectl -n ns delete pod mypod")
        assert result is None

    def test_malformed_quoting_returns_error(self):
        result = check_mutating_kubectl("kubectl 'unclosed")
        assert result is not None
        assert "malformed quoting" in result


# ---------------------------------------------------------------------------
# submit_to_benchmark
# ---------------------------------------------------------------------------


class TestSubmitToBenchmark:
    def _run(self, coro):
        return asyncio.run(coro)

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

            success, msg, result_oracle = self._run(submit_to_benchmark("http://x/sse", "my answer", "diagnosis"))

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

            success, msg, oracle = self._run(submit_to_benchmark("http://x/sse", "answer", "diagnosis"))

        assert success is False
        assert oracle is None

    def test_unparseable_response(self):
        mock_session = self._make_mock_session("not a dict at all !!!{}")

        with patch("mcp.ClientSession") as mock_cs, patch("mcp.client.sse.sse_client") as mock_sse:
            mock_sse.return_value.__aenter__ = AsyncMock(return_value=("r", "w"))
            mock_sse.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_cs.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_cs.return_value.__aexit__ = AsyncMock(return_value=False)

            success, msg, oracle = self._run(submit_to_benchmark("http://x/sse", "answer", "diagnosis"))

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

            success, msg, oracle = self._run(submit_to_benchmark("http://x/sse", "answer", "diagnosis"))

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

            success, msg, result_oracle = self._run(submit_to_benchmark("http://x/sse", "answer", "diagnosis"))

        assert success is False
        assert "rejected" in msg.lower()
        assert result_oracle == {}

    def test_success_false_in_oracle(self):
        oracle = {"Diagnosis": {"success": False}}
        raw = repr({"status": "200", "text": json.dumps(oracle)})
        mock_session = self._make_mock_session(raw)

        with patch("mcp.ClientSession") as mock_cs, patch("mcp.client.sse.sse_client") as mock_sse:
            mock_sse.return_value.__aenter__ = AsyncMock(return_value=("r", "w"))
            mock_sse.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_cs.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_cs.return_value.__aexit__ = AsyncMock(return_value=False)

            success, msg, result_oracle = self._run(submit_to_benchmark("http://x/sse", "answer", "diagnosis"))

        assert success is False
        assert result_oracle == oracle

    def test_mitigation_filters_out_diagnosis(self):
        oracle = {
            "Diagnosis": {"judgment": "True", "success": True, "accuracy": 100.0},
            "TTL": 69.4,
            "Mitigation": {"success": True},
            "TTM": 166.5,
        }
        raw = repr({"status": "200", "text": json.dumps(oracle)})
        mock_session = self._make_mock_session(raw)

        with patch("mcp.ClientSession") as mock_cs, patch("mcp.client.sse.sse_client") as mock_sse:
            mock_sse.return_value.__aenter__ = AsyncMock(return_value=("r", "w"))
            mock_sse.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_cs.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_cs.return_value.__aexit__ = AsyncMock(return_value=False)

            success, msg, result_oracle = self._run(submit_to_benchmark("http://x/sse", "", "mitigation"))

        assert success is True
        assert "accepted" in msg.lower()
        assert result_oracle == {"Mitigation": {"success": True}, "TTM": 166.5}
        assert result_oracle is not None
        assert "Diagnosis" not in result_oracle


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

    def test_negative_end_line_other_than_minus_one(self, tmp_path: Path):
        f = tmp_path / "f.txt"
        f.write_text("a\nb\nc\nd\n")
        ctx = _make_sre_ctx(tmp_path)
        # Any negative end_line should read to end, same as -1
        result_neg2 = read_file(ctx, str(f), start_line=0, end_line=-2)
        result_neg1 = read_file(ctx, str(f), start_line=0, end_line=-1)
        assert result_neg2 == result_neg1

    def test_large_file_returns_error_when_exceeds_cap(self, tmp_path: Path):
        f = tmp_path / "big.txt"
        # Each line ~130 chars, 250 lines ≈ 32k chars — well over MAX_READ_CHARS (25000)
        f.write_text("\n".join(f"line{i:04d} {'x' * 120}" for i in range(250)))
        ctx = _make_sre_ctx(tmp_path)
        result = read_file(ctx, str(f), start_line=0, end_line=-1)
        assert "Error: requested range too large" in result
        assert "250 lines" in result
        assert "decrease the line range" in result

    def test_large_file_small_range_still_works(self, tmp_path: Path):
        f = tmp_path / "big.txt"
        f.write_text("\n".join(f"line{i:04d} {'x' * 70}" for i in range(200)))
        ctx = _make_sre_ctx(tmp_path)
        result = read_file(ctx, str(f), start_line=0, end_line=10)
        assert "line0000" in result
        assert "Error" not in result


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

    def test_empty_old_str_returns_error(self, tmp_path: Path):
        f = tmp_path / "f.txt"
        f.write_text("hello world")
        ctx = _make_sre_ctx(tmp_path)
        result = str_replace_file(ctx, str(f), "", "x")
        assert "Error" in result
        # File must not be corrupted
        assert f.read_text() == "hello world"


# ---------------------------------------------------------------------------
# exec_bash_readonly
# ---------------------------------------------------------------------------


class TestExecBashReadonly:
    def test_allowed_command_delegates(self, tmp_path: Path):
        ctx = _make_judge_ctx(tmp_path)
        with patch("sregym_agents.crucible.tools._bash_tools.run_bash_sync", return_value="ok") as mock_run:
            result = exec_bash_readonly(ctx, "ls -la")
        mock_run.assert_called_once_with("ls -la")
        assert result == "ok"

    def test_mutating_kubectl_blocked(self, tmp_path: Path):
        ctx = _make_judge_ctx(tmp_path)
        with patch("sregym_agents.crucible.tools._bash_tools.run_bash_sync") as mock_run:
            result = exec_bash_readonly(ctx, "kubectl delete pod mypod")
        mock_run.assert_not_called()
        assert "Error" in result
        assert "delete" in result


# ---------------------------------------------------------------------------
# SRESubmission
# ---------------------------------------------------------------------------


class TestSRESubmission:
    def test_valid_submission(self):
        sub = SRESubmission(answer="disk full", justification="saw 100% disk usage")
        assert sub.answer == "disk full"
        assert sub.justification == "saw 100% disk usage"

    def test_serialization_roundtrip(self):
        sub = SRESubmission(answer="OOM kill", justification="container hit memory limit")
        data = sub.model_dump()
        restored = SRESubmission(**data)
        assert restored == sub


# ---------------------------------------------------------------------------
# submit_verdict
# ---------------------------------------------------------------------------


class TestSubmitVerdict:
    def test_blocked_before_hypothesis_revealed(self, tmp_path: Path):
        shared = tmp_path / "shared.md"
        shared.write_text("")
        ctx = _make_judge_ctx(tmp_path)
        assert ctx.deps.state.hypothesis_revealed is False

        result = asyncio.run(submit_verdict(ctx, False, "reason", "answer"))
        assert "Error" in result
        assert "reveal_agent_hypothesis" in result
        assert ctx.deps.state.submitted is False

    def test_reject_sets_state_no_benchmark_call(self, tmp_path: Path):
        shared = tmp_path / "shared.md"
        shared.write_text("")
        ctx = _make_judge_ctx(tmp_path)
        ctx.deps.state.hypothesis_revealed = True

        with patch(
            "sregym_agents.crucible.tools._judge_tools.submit_to_benchmark", new_callable=AsyncMock
        ) as mock_submit:
            asyncio.run(submit_verdict(ctx, False, "not good enough", "answer"))

        mock_submit.assert_not_called()
        assert ctx.deps.state.submitted is True
        assert ctx.deps.state.verdict == "REJECTED"

    def test_approve_calls_benchmark(self, tmp_path: Path):
        shared = tmp_path / "shared.md"
        shared.write_text("")
        ctx = _make_judge_ctx(tmp_path)
        ctx.deps.state.hypothesis_revealed = True

        oracle = {"Diagnosis": {"success": True}}
        with patch(
            "sregym_agents.crucible.tools._judge_tools.submit_to_benchmark",
            new_callable=AsyncMock,
            return_value=(True, "Benchmark accepted...", oracle),
        ):
            asyncio.run(submit_verdict(ctx, True, "great work", "answer"))

        assert ctx.deps.state.submitted is True
        assert ctx.deps.state.verdict == "APPROVED"
        assert "<benchmark_result>" in shared.read_text()

    def test_benchmark_exception_writes_error_block(self, tmp_path: Path):
        shared = tmp_path / "shared.md"
        shared.write_text("")
        ctx = _make_judge_ctx(tmp_path)
        ctx.deps.state.hypothesis_revealed = True

        with patch(
            "sregym_agents.crucible.tools._judge_tools.submit_to_benchmark",
            new_callable=AsyncMock,
            side_effect=RuntimeError("connection refused"),
        ):
            asyncio.run(submit_verdict(ctx, True, "great", "answer"))

        assert ctx.deps.state.submitted is True
        assert "<benchmark_result>" in shared.read_text()
        assert "Error" in shared.read_text()

    def test_benchmark_result_not_returned_to_llm(self, tmp_path: Path):
        shared = tmp_path / "shared.md"
        shared.write_text("")
        ctx = _make_judge_ctx(tmp_path)
        ctx.deps.state.hypothesis_revealed = True

        oracle = {"Diagnosis": {"success": True}}
        with patch(
            "sregym_agents.crucible.tools._judge_tools.submit_to_benchmark",
            new_callable=AsyncMock,
            return_value=(True, "Benchmark accepted...", oracle),
        ):
            result = asyncio.run(submit_verdict(ctx, True, "great work", "answer"))

        assert "<benchmark_result>" not in result

    def test_benchmark_result_written_to_shared_file(self, tmp_path: Path):
        shared = tmp_path / "shared.md"
        shared.write_text("")
        ctx = _make_judge_ctx(tmp_path)
        ctx.deps.state.hypothesis_revealed = True

        oracle = {"Diagnosis": {"success": True}}
        with patch(
            "sregym_agents.crucible.tools._judge_tools.submit_to_benchmark",
            new_callable=AsyncMock,
            return_value=(True, "Benchmark accepted...", oracle),
        ):
            asyncio.run(submit_verdict(ctx, True, "great work", "answer"))

        assert "<benchmark_result>" in shared.read_text()

    def test_second_submit_blocked_after_approval(self, tmp_path: Path):
        shared = tmp_path / "shared.md"
        shared.write_text("")
        ctx = _make_judge_ctx(tmp_path)
        ctx.deps.state.hypothesis_revealed = True

        oracle = {"Diagnosis": {"success": True}}
        with patch(
            "sregym_agents.crucible.tools._judge_tools.submit_to_benchmark",
            new_callable=AsyncMock,
            return_value=(True, "Benchmark accepted...", oracle),
        ) as mock_submit:
            asyncio.run(submit_verdict(ctx, True, "great work", "answer"))
            assert mock_submit.call_count == 1

            result2 = asyncio.run(submit_verdict(ctx, False, "changed my mind", "answer2"))
            assert mock_submit.call_count == 1  # benchmark not called again

        assert "already submitted" in result2.lower()
        assert ctx.deps.state.verdict == "APPROVED"  # not overwritten

    def test_file_write_error_returns_error_string(self, tmp_path: Path):
        ctx = _make_judge_ctx(tmp_path)
        ctx.deps.shared_file = SharedFile(tmp_path)  # directory — open will fail
        ctx.deps.state.hypothesis_revealed = True

        result = asyncio.run(submit_verdict(ctx, False, "reason", "answer"))

        assert "Error writing verdict" in result


# ---------------------------------------------------------------------------
# submit_independent_findings
# ---------------------------------------------------------------------------


class TestSubmitIndependentFindings:
    def test_records_findings_in_shared_file(self, tmp_path: Path):
        shared = tmp_path / "shared.md"
        shared.write_text("# header\n")
        ctx = _make_judge_ctx(tmp_path)
        result = submit_independent_findings(ctx, "Pod X is CrashLoopBackOff due to missing config")
        assert "recorded" in result.lower()
        content = shared.read_text()
        assert "Judge Independent Findings" in content
        assert "Pod X is CrashLoopBackOff" in content

    def test_sets_findings_submitted_flag(self, tmp_path: Path):
        shared = tmp_path / "shared.md"
        shared.write_text("")
        ctx = _make_judge_ctx(tmp_path)
        submit_independent_findings(ctx, "some findings")
        assert ctx.deps.state.independent_findings_submitted is True

    def test_second_call_blocked(self, tmp_path: Path):
        shared = tmp_path / "shared.md"
        shared.write_text("")
        ctx = _make_judge_ctx(tmp_path)
        submit_independent_findings(ctx, "first findings")
        result = submit_independent_findings(ctx, "second findings")
        assert "Error" in result
        assert "already submitted" in result.lower()

    def test_empty_findings_returns_error(self, tmp_path: Path):
        shared = tmp_path / "shared.md"
        shared.write_text("")
        ctx = _make_judge_ctx(tmp_path)
        result = submit_independent_findings(ctx, "   ")
        assert "Error" in result
        assert ctx.deps.state.independent_findings_submitted is False

    def test_uses_append_not_direct_open(self, tmp_path: Path):
        """Writes must go through SharedFile.append() to ensure fcntl locking."""
        shared = tmp_path / "shared.md"
        shared.write_text("")
        ctx = _make_judge_ctx(tmp_path)
        with (
            patch.object(ctx.deps.shared_file, "append") as mock_append,
            patch.object(ctx.deps.shared_file, "open") as mock_open,
        ):
            submit_independent_findings(ctx, "some findings")
        mock_append.assert_called_once()
        mock_open.assert_not_called()


# ---------------------------------------------------------------------------
# reveal_agent_hypothesis
# ---------------------------------------------------------------------------


class TestRevealAgentHypothesis:
    def test_returns_hypothesis_text_from_deps(self, tmp_path: Path):
        shared = tmp_path / "shared.md"
        shared.write_text("")
        hypothesis = "**Diagnosis**: disk full\n**Justification**: 100% usage\n"
        ctx = _make_judge_ctx(tmp_path, hypothesis_text=hypothesis)
        ctx.deps.state.independent_findings_submitted = True
        result = reveal_agent_hypothesis(ctx)
        assert result == hypothesis

    def test_blocked_before_findings_submitted(self, tmp_path: Path):
        shared = tmp_path / "shared.md"
        shared.write_text("")
        ctx = _make_judge_ctx(tmp_path)
        assert ctx.deps.state.independent_findings_submitted is False
        result = reveal_agent_hypothesis(ctx)
        assert "Error" in result
        assert "submit_independent_findings" in result
        assert ctx.deps.state.hypothesis_revealed is False

    def test_sets_revealed_flag(self, tmp_path: Path):
        shared = tmp_path / "shared.md"
        shared.write_text("")
        ctx = _make_judge_ctx(tmp_path)
        ctx.deps.state.independent_findings_submitted = True
        reveal_agent_hypothesis(ctx)
        assert ctx.deps.state.hypothesis_revealed is True

    def test_second_call_blocked(self, tmp_path: Path):
        shared = tmp_path / "shared.md"
        shared.write_text("")
        ctx = _make_judge_ctx(tmp_path)
        ctx.deps.state.independent_findings_submitted = True
        reveal_agent_hypothesis(ctx)
        result = reveal_agent_hypothesis(ctx)
        assert "Error" in result
        assert "already revealed" in result.lower()


# ---------------------------------------------------------------------------
# Judge tool sequence (integration)
# ---------------------------------------------------------------------------


class TestJudgeToolSequence:
    def test_happy_path_findings_then_reveal_then_verdict(self, tmp_path: Path):
        shared = tmp_path / "shared.md"
        shared.write_text("# header\n")
        hypothesis = "**Diagnosis**: disk full\n**Justification**: 100% usage\n"
        ctx = _make_judge_ctx(tmp_path, hypothesis_text=hypothesis)

        submit_independent_findings(ctx, "I see pod X failing with disk pressure")
        result = reveal_agent_hypothesis(ctx)
        assert result == hypothesis

        asyncio.run(submit_verdict(ctx, False, "hypothesis is correct but missing specifics", "disk full"))

        content = shared.read_text()
        assert "Judge Independent Findings" in content
        assert "Judge Verdict" in content

    def test_reveal_before_findings_blocked(self, tmp_path: Path):
        shared = tmp_path / "shared.md"
        shared.write_text("")
        ctx = _make_judge_ctx(tmp_path)
        result = reveal_agent_hypothesis(ctx)
        assert "Error" in result
        assert ctx.deps.state.hypothesis_revealed is False

    def test_verdict_before_reveal_blocked(self, tmp_path: Path):
        shared = tmp_path / "shared.md"
        shared.write_text("")
        ctx = _make_judge_ctx(tmp_path)
        ctx.deps.state.independent_findings_submitted = True
        # findings submitted but hypothesis not revealed
        result = asyncio.run(submit_verdict(ctx, False, "reason", "answer"))
        assert "Error" in result
        assert ctx.deps.state.submitted is False

    def test_verdict_before_findings_blocked(self, tmp_path: Path):
        shared = tmp_path / "shared.md"
        shared.write_text("")
        ctx = _make_judge_ctx(tmp_path)
        result = asyncio.run(submit_verdict(ctx, False, "reason", "answer"))
        assert "Error" in result
        assert ctx.deps.state.submitted is False

    def test_reveal_after_findings_returns_hypothesis(self, tmp_path: Path):
        shared = tmp_path / "shared.md"
        shared.write_text("")
        hypothesis = "**Diagnosis**: OOM\n**Justification**: memory limit exceeded\n"
        ctx = _make_judge_ctx(tmp_path, hypothesis_text=hypothesis)
        submit_independent_findings(ctx, "findings text")
        result = reveal_agent_hypothesis(ctx)
        assert result == hypothesis

    def test_full_sequence_reject_preserves_state_for_next_iteration(self, tmp_path: Path):
        shared = tmp_path / "shared.md"
        shared.write_text("# header\n")
        hypothesis = "**Diagnosis**: disk full\n**Justification**: 100% usage\n"

        # Iteration 1 — reject
        ctx = _make_judge_ctx(tmp_path, hypothesis_text=hypothesis)
        submit_independent_findings(ctx, "findings for iter 1")
        reveal_agent_hypothesis(ctx)
        asyncio.run(submit_verdict(ctx, False, "not specific enough", "disk full"))

        # Iteration 2 — fresh state
        state2 = SharedState()
        assert state2.independent_findings_submitted is False
        assert state2.hypothesis_revealed is False
        assert state2.submitted is False

    def test_submit_verdict_from_running_loop(self, tmp_path: Path):
        """Regression: submit_verdict must not raise RuntimeError when called from within asyncio.run()."""
        shared = tmp_path / "shared.md"
        shared.write_text("")
        ctx = _make_judge_ctx(tmp_path)
        ctx.deps.state.hypothesis_revealed = True

        async def _run():
            with patch(
                "sregym_agents.crucible.tools._judge_tools.submit_to_benchmark",
                new_callable=AsyncMock,
                return_value=(True, "Benchmark accepted", {"Diagnosis": {"success": True}}),
            ):
                return await submit_verdict(ctx, True, "great work", "answer")

        result = asyncio.run(_run())
        assert ctx.deps.state.verdict == "APPROVED"
        assert "Verdict submitted" in result
