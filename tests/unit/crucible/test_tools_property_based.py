"""Property-based tests for sregym_agents.crucible.tools using hypothesis.

These tests encode invariants of the crucible tool functions and explore
edge cases (special characters, empty strings, boundary values) that
manual tests may miss.
"""

from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from hypothesis import assume, given, settings
from hypothesis import strategies as st

from sregym_agents.crucible.tools import (
    MAX_OUTPUT_CHARS,
    MUTATING_KUBECTL_VERBS,
    SREDeps,
    _check_mutating_kubectl,
    _run_bash_sync,
    grep,
    read_file,
    str_replace_file,
    write_file,
)

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------
# Arbitrary text including special shell characters (but no \r or surrogates for file safety)
_file_safe_chars = st.characters(blacklist_characters="\r", blacklist_categories=("Cs",))
arbitrary_text = st.text(alphabet=_file_safe_chars, max_size=500)

# File content — arbitrary unicode (same exclusions)
file_content = st.text(alphabet=_file_safe_chars, max_size=2000)

# Non-empty text for old_str in replacements (same exclusions)
non_empty_text = st.text(alphabet=_file_safe_chars, min_size=1, max_size=200)

# Line range tuples (start_line, end_line)
line_range = st.tuples(
    st.integers(min_value=-10, max_value=300),
    st.integers(min_value=-1, max_value=300),
)

# Read-only kubectl verbs (not in MUTATING_KUBECTL_VERBS)
readonly_verbs = st.sampled_from(["get", "describe", "logs", "top", "version", "api-resources"])

# Mutating kubectl verbs
mutating_verbs = st.sampled_from(sorted(MUTATING_KUBECTL_VERBS))

# Simple kubectl args (no special quoting issues)
kubectl_args = st.text(
    alphabet=st.characters(categories=("L", "N"), whitelist_characters="-_./"),
    min_size=1,
    max_size=30,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_sre_ctx(d: Path) -> MagicMock:
    ctx = MagicMock()
    ctx.deps = SREDeps(
        namespace="ns",
        shared_file=d / "shared.md",  # type: ignore[arg-type]
        iteration=1,
        stage="diagnosis",
    )
    return ctx


def _tmpdir() -> Path:
    """Create a fresh temporary directory for each hypothesis example."""
    return Path(tempfile.mkdtemp())


def _is_valid_regex(pattern: str) -> bool:
    try:
        re.compile(pattern)
        return True
    except re.error:
        return False


# ---------------------------------------------------------------------------
# _run_bash_sync
# ---------------------------------------------------------------------------


class TestRunBashSyncProperties:
    """_run_bash_sync must always return a string, never raise."""

    @staticmethod
    def _make_popen_mock(stdout: str = "ok", stderr: str = "", returncode: int = 0):
        mock = MagicMock()
        mock.communicate.return_value = (stdout, stderr)
        mock.returncode = returncode
        mock.pid = 12345
        mock.wait.return_value = None
        return mock

    @given(cmd=arbitrary_text)
    @settings(max_examples=100, deadline=None)
    def test_never_raises_with_mocked_subprocess(self, cmd: str):
        popen_mock = self._make_popen_mock()
        with patch("subprocess.Popen", return_value=popen_mock):
            output = _run_bash_sync(cmd)
        assert isinstance(output, str)

    @given(cmd=arbitrary_text)
    @settings(max_examples=50, deadline=None)
    def test_returns_error_string_on_timeout(self, cmd: str):
        popen_mock = self._make_popen_mock()
        popen_mock.communicate.side_effect = subprocess.TimeoutExpired("cmd", 60)
        with (
            patch("subprocess.Popen", return_value=popen_mock),
            patch("os.killpg"),
            patch("os.getpgid", return_value=12345),
        ):
            output = _run_bash_sync(cmd)
        assert isinstance(output, str)
        assert "timed out" in output.lower()

    @given(cmd=arbitrary_text)
    @settings(max_examples=50, deadline=None)
    def test_returns_error_string_on_exception(self, cmd: str):
        with patch("subprocess.Popen", side_effect=OSError("mock error")):
            output = _run_bash_sync(cmd)
        assert isinstance(output, str)
        assert "Error" in output

    @given(
        cmd=arbitrary_text, stdout_len=st.integers(min_value=MAX_OUTPUT_CHARS + 1, max_value=MAX_OUTPUT_CHARS + 5000)
    )
    @settings(max_examples=30, deadline=None)
    def test_truncates_long_output(self, cmd: str, stdout_len: int):
        popen_mock = self._make_popen_mock(stdout="x" * stdout_len)
        with (
            patch("subprocess.Popen", return_value=popen_mock),
            patch("sregym_agents.crucible.tools.Path") as mock_path,
        ):
            mock_path.return_value.write_text = MagicMock()
            output = _run_bash_sync(cmd)
        assert "truncated" in output.lower() or "Output truncated" in output


# ---------------------------------------------------------------------------
# _check_mutating_kubectl
# ---------------------------------------------------------------------------


class TestCheckMutatingKubectlProperties:
    """_check_mutating_kubectl must never raise for any input."""

    @given(cmd=arbitrary_text)
    @settings(max_examples=200)
    def test_never_raises(self, cmd: str):
        result = _check_mutating_kubectl(cmd)
        assert result is None or isinstance(result, str)

    @given(cmd=st.text(min_size=1, max_size=200).filter(lambda s: "kubectl" not in s.lower()))
    @settings(max_examples=100)
    def test_non_kubectl_returns_none_or_parse_error(self, cmd: str):
        result = _check_mutating_kubectl(cmd)
        # If shlex can't parse it, we get a parse error; otherwise None
        if result is not None:
            assert "malformed quoting" in result

    @given(verb=mutating_verbs, args=kubectl_args)
    @settings(max_examples=50)
    def test_mutating_verb_immediately_after_kubectl_caught(self, verb: str, args: str):
        cmd = f"kubectl {verb} {args}"
        result = _check_mutating_kubectl(cmd)
        assert result is not None
        assert verb in result

    @given(verb=readonly_verbs, args=kubectl_args)
    @settings(max_examples=50)
    def test_readonly_verb_immediately_after_kubectl_allowed(self, verb: str, args: str):
        cmd = f"kubectl {verb} {args}"
        result = _check_mutating_kubectl(cmd)
        assert result is None


# ---------------------------------------------------------------------------
# read_file
# ---------------------------------------------------------------------------


class TestReadFileProperties:
    """read_file must always return a string, never raise."""

    @given(content=file_content, lr=line_range)
    @settings(max_examples=100)
    def test_never_raises(self, content: str, lr: tuple[int, int]):
        d = _tmpdir()
        f = d / "f.txt"
        f.write_text(content)
        ctx = _make_sre_ctx(d)
        result = read_file(ctx, str(f), start_line=lr[0], end_line=lr[1])
        assert isinstance(result, str)

    @given(content=file_content)
    @settings(max_examples=50)
    def test_negative_start_clamped_to_zero(self, content: str):
        d = _tmpdir()
        f = d / "f.txt"
        f.write_text(content)
        ctx = _make_sre_ctx(d)
        result_neg = read_file(ctx, str(f), start_line=-5, end_line=-1)
        result_zero = read_file(ctx, str(f), start_line=0, end_line=-1)
        assert result_neg == result_zero

    @given(
        content=file_content, start=st.integers(min_value=0, max_value=100), end=st.integers(min_value=0, max_value=100)
    )
    @settings(max_examples=50)
    def test_start_ge_end_returns_empty_range(self, content: str, start: int, end: int):
        assume(start >= end and end > 0)
        d = _tmpdir()
        f = d / "f.txt"
        f.write_text(content)
        ctx = _make_sre_ctx(d)
        result = read_file(ctx, str(f), start_line=start, end_line=end)
        assert result == "(empty range)"

    @given(content=file_content)
    @settings(max_examples=50)
    def test_all_negative_end_lines_equivalent(self, content: str):
        d = _tmpdir()
        f = d / "f.txt"
        f.write_text(content)
        ctx = _make_sre_ctx(d)
        result_neg1 = read_file(ctx, str(f), start_line=0, end_line=-1)
        result_neg5 = read_file(ctx, str(f), start_line=0, end_line=-5)
        assert result_neg1 == result_neg5


# ---------------------------------------------------------------------------
# str_replace_file
# ---------------------------------------------------------------------------


class TestStrReplaceFileProperties:
    """str_replace_file must never raise and must handle edge cases safely."""

    @given(content=file_content, old_str=non_empty_text, new_str=arbitrary_text)
    @settings(max_examples=100)
    def test_never_raises(self, content: str, old_str: str, new_str: str):
        d = _tmpdir()
        f = d / "f.txt"
        f.write_text(content)
        ctx = _make_sre_ctx(d)
        result = str_replace_file(ctx, str(f), old_str, new_str)
        assert isinstance(result, str)

    @given(prefix=file_content, old_str=non_empty_text, suffix=file_content, new_str=arbitrary_text)
    @settings(max_examples=100)
    def test_found_replaces_exactly_once(self, prefix: str, old_str: str, suffix: str, new_str: str):
        content = prefix + old_str + suffix
        d = _tmpdir()
        f = d / "f.txt"
        f.write_text(content)
        ctx = _make_sre_ctx(d)
        result = str_replace_file(ctx, str(f), old_str, new_str)
        assert result.startswith("Replaced")
        assert f.read_text() == content.replace(old_str, new_str, 1)

    @given(content=file_content, old_str=non_empty_text)
    @settings(max_examples=100)
    def test_not_found_leaves_file_unchanged(self, content: str, old_str: str):
        assume(old_str not in content)
        d = _tmpdir()
        f = d / "f.txt"
        f.write_text(content)
        ctx = _make_sre_ctx(d)
        result = str_replace_file(ctx, str(f), old_str, "replacement")
        assert "Error" in result
        assert f.read_text() == content

    @given(content=file_content, new_str=arbitrary_text)
    @settings(max_examples=50)
    def test_empty_old_str_returns_error(self, content: str, new_str: str):
        d = _tmpdir()
        f = d / "f.txt"
        f.write_text(content)
        ctx = _make_sre_ctx(d)
        result = str_replace_file(ctx, str(f), "", new_str)
        assert "Error" in result
        # File must not be modified
        assert f.read_text() == content


# ---------------------------------------------------------------------------
# grep
# ---------------------------------------------------------------------------


class TestGrepProperties:
    """grep must always return a string, never raise."""

    @given(pattern=arbitrary_text)
    @settings(max_examples=100)
    def test_never_raises_on_arbitrary_pattern(self, pattern: str):
        d = _tmpdir()
        f = d / "f.txt"
        f.write_text("some content\nanother line\n")
        ctx = _make_sre_ctx(d)
        with patch("sregym_agents.crucible.tools._agent_cwd", return_value=d):
            result = grep(ctx, pattern, path=str(f))
        assert isinstance(result, str)

    @given(content=file_content)
    @settings(max_examples=50)
    def test_valid_literal_pattern_returns_string(self, content: str):
        d = _tmpdir()
        f = d / "f.txt"
        f.write_text(content)
        ctx = _make_sre_ctx(d)
        with patch("sregym_agents.crucible.tools._agent_cwd", return_value=d):
            result = grep(ctx, "test", path=str(f))
        assert isinstance(result, str)

    def test_too_long_pattern_returns_error(self, tmp_path):
        f = tmp_path / "f.txt"
        f.write_text("content")
        ctx = _make_sre_ctx(tmp_path)
        long_pattern = "a" * 1001
        with patch("sregym_agents.crucible.tools._agent_cwd", return_value=tmp_path):
            result = grep(ctx, long_pattern, path=str(f))
        assert "Error" in result
        assert "too long" in result

    @given(pattern=st.from_regex(r"[^\x00]{1,50}", fullmatch=True))
    @settings(max_examples=50)
    def test_invalid_regex_returns_error_not_crash(self, pattern: str):
        d = _tmpdir()
        f = d / "f.txt"
        f.write_text("test content\n")
        ctx = _make_sre_ctx(d)
        with patch("sregym_agents.crucible.tools._agent_cwd", return_value=d):
            result = grep(ctx, pattern, path=str(f))
        assert isinstance(result, str)
        # If it's invalid regex, should contain "Error"; if valid, any string is fine
        if not _is_valid_regex(pattern):
            assert "Error" in result


# ---------------------------------------------------------------------------
# write_file
# ---------------------------------------------------------------------------


class TestWriteFileProperties:
    """write_file must correctly persist any content."""

    @given(content=file_content)
    @settings(max_examples=100)
    def test_roundtrip_content_preserved(self, content: str):
        d = _tmpdir()
        f = d / "f.txt"
        ctx = _make_sre_ctx(d)
        result = write_file(ctx, str(f), content)
        assert "bytes" in result
        assert f.read_text() == content
