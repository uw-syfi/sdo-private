"""Tests for prompt version loading in sregym_agents.crucible._prompts."""

from __future__ import annotations

import pytest

import sregym_agents.crucible._prompts as prompts_mod
from sregym_agents.crucible._prompts import _render, configure


def test_configure_valid_version() -> None:
    configure("v1")
    # Should be able to render a known template after configuration.
    result = _render("diagnosis_agent_system")
    assert isinstance(result, str)
    assert len(result) > 0


def test_configure_invalid_version() -> None:
    with pytest.raises(ValueError, match="not found"):
        configure("nonexistent_version")


def test_render_before_configure_raises() -> None:
    # Temporarily clear the configured environment.
    original = prompts_mod._jinja_env
    try:
        prompts_mod._jinja_env = None
        with pytest.raises(RuntimeError, match="not configured"):
            _render("diagnosis_agent_system")
    finally:
        prompts_mod._jinja_env = original
