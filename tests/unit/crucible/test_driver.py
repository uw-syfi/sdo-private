"""Unit tests for sregym_agents.crucible.driver tmp-prefix helpers.

The previously co-located cleanup + conductor-polling tests moved with
their subjects to ``tests/unit/sregym_lib/``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import patch

from sregym_agents.crucible.driver import (
    _cleanup_run_tmp_prefix,
    _setup_run_tmp_prefix,
)

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


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
