"""Guardrails for the v3 refine_triage_priors prompt contract."""

from __future__ import annotations

from pathlib import Path

_REFINE_TRIAGE_PRIORS = (
    Path(__file__).resolve().parents[3]
    / "sregym_agents"
    / "crucible"
    / "configs"
    / "prompts"
    / "v3"
    / "kb"
    / "refine_triage_priors.j2"
)


def test_refine_triage_priors_requires_markdown_sections_and_non_overlap() -> None:
    text = _REFINE_TRIAGE_PRIORS.read_text()
    assert "mutual exclusivity" in text.lower() or "no overlap" in text.lower()
    assert "##" in text
    assert "preamble" in text.lower() or "commentary" in text.lower()
