from __future__ import annotations

import pytest

from sregym_agents.crucible._prompts import configure


@pytest.fixture(autouse=True)
def _configure_prompts() -> None:
    """Ensure prompt version is configured before any crucible test that renders templates."""
    configure("v1")
