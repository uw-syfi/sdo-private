"""Unit tests for sregym_agents.crucible.driver._signal_cleanup.

``_wait_for_stage`` was removed in favor of the shared async helper
``sregym_agents.crucible._conductor.poll_stage``; see
``tests/unit/crucible/test_conductor.py`` for its coverage.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import requests

from sregym_agents.crucible.driver import (
    _cleanup_run_tmp_prefix,
    _setup_run_tmp_prefix,
    _signal_cleanup,
)

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def _make_response(stage: str) -> MagicMock:
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {"stage": stage}
    return resp


class TestSignalCleanup:
    """Crucible always POSTs /cleanup after orchestrator.run() returns, to release
    the conductor's deferred-teardown gate. The call must tolerate network errors
    so a flaky conductor connection does not mask orchestrator failures."""

    def test_posts_cleanup_to_api(self):
        resp = MagicMock(status_code=200, text='{"status":"ok"}')
        with patch("sregym_agents.crucible.driver.requests.post", return_value=resp) as mock_post:
            _signal_cleanup("http://localhost:8000")
        mock_post.assert_called_once()
        url = mock_post.call_args.args[0]
        assert url == "http://localhost:8000/cleanup"

    def test_swallows_exceptions(self):
        with patch(
            "sregym_agents.crucible.driver.requests.post",
            side_effect=requests.ConnectionError("refused"),
        ):
            # Must not raise — cleanup-signal failure should not crash the driver.
            _signal_cleanup("http://localhost:8000")

    def test_uses_timeout(self):
        resp = MagicMock(status_code=200, text="")
        with patch("sregym_agents.crucible.driver.requests.post", return_value=resp) as mock_post:
            _signal_cleanup("http://localhost:8000")
        assert mock_post.call_args.kwargs.get("timeout") is not None


class TestRunTmpPrefix:
    """Per-run /tmp prefix setup + cleanup for bash/grep truncation artifacts (issue #93)."""

    def test_setup_creates_dir_and_publishes_env_var(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        from sregym_agents.crucible.tools._bash_tools import RUN_PREFIX_ENV

        monkeypatch.delenv(RUN_PREFIX_ENV, raising=False)
        # Redirect /tmp-relative path into pytest's tmp_path by patching Path("/tmp/...")
        # construction indirectly via monkeypatching the helper's target directly.
        with patch("sregym_agents.crucible.driver.Path") as mock_path_cls:
            fake_prefix = tmp_path / "crucible_run_abc123"
            mock_path_cls.return_value = fake_prefix
            prefix = _setup_run_tmp_prefix("abc123")

        assert prefix == fake_prefix
        assert fake_prefix.exists()
        assert fake_prefix.is_dir()
        import os as _os

        assert _os.environ.get(RUN_PREFIX_ENV) == str(fake_prefix)

        # Cleanup side-effect for the global env var to avoid leaking between tests.
        _cleanup_run_tmp_prefix(fake_prefix)

    def test_cleanup_removes_dir_and_clears_env_var(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        from sregym_agents.crucible.tools._bash_tools import RUN_PREFIX_ENV

        prefix = tmp_path / "crucible_run_xyz"
        prefix.mkdir()
        (prefix / "bash_out_deadbeef.txt").write_text("leaked output")
        monkeypatch.setenv(RUN_PREFIX_ENV, str(prefix))

        _cleanup_run_tmp_prefix(prefix)

        assert not prefix.exists()
        import os as _os

        assert RUN_PREFIX_ENV not in _os.environ

    def test_cleanup_is_safe_when_dir_missing(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """Cleanup must never raise even if the prefix dir was already deleted."""
        from sregym_agents.crucible.tools._bash_tools import RUN_PREFIX_ENV

        monkeypatch.setenv(RUN_PREFIX_ENV, str(tmp_path / "does_not_exist"))
        # Must not raise.
        _cleanup_run_tmp_prefix(tmp_path / "does_not_exist")
