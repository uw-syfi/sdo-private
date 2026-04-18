"""Unit tests for sregym_agents.crucible.driver._signal_cleanup.

``_wait_for_stage`` was removed in favor of the shared async helper
``sregym_agents.crucible._conductor.poll_stage``; see
``tests/unit/crucible/test_conductor.py`` for its coverage.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import requests

from sregym_agents.crucible.driver import _signal_cleanup


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
