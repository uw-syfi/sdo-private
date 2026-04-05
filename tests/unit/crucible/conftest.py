from __future__ import annotations

import pytest

from sregym_agents.crucible._prompts import PromptRenderer


@pytest.fixture
def renderer() -> PromptRenderer:
    """A PromptRenderer configured with the default v1 prompt templates."""
    return PromptRenderer("v1")
