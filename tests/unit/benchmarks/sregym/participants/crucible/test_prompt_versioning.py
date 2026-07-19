"""Tests for prompt version loading in benchmarks.sregym.participants.crucible._prompts."""

from __future__ import annotations

import pytest

from benchmarks.sregym.participants.crucible._prompts import PromptRenderer


def test_configure_valid_version() -> None:
    renderer = PromptRenderer("v1")
    result = renderer.render("diagnosis_agent_system")
    assert isinstance(result, str)
    assert len(result) > 0


def test_configure_invalid_version() -> None:
    with pytest.raises(ValueError, match="not found"):
        PromptRenderer("nonexistent_version")


def test_render_raises_on_missing_variable() -> None:
    renderer = PromptRenderer("v1")
    import jinja2

    with pytest.raises(jinja2.UndefinedError):
        # diagnosis_agent_user requires many variables — omit most to trigger the error.
        renderer.render("diagnosis_agent_user", app_name="test", namespace="test")
