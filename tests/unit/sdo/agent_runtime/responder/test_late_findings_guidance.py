"""Responder guidance for pulling late findings, behind ``--late-findings``."""

from __future__ import annotations

from pathlib import Path

import pytest

from sdo.agent_runtime.responder.broker_cli import _argument_parser
from sdo.agent_runtime.responder.codex import _responder_prompt, late_findings_guidance
from sdo.contracts import IncidentRequest
from tests.unit.sdo.agent_runtime.responder.test_reflection_generalization import _leaks

_FIXTURE = Path(__file__).resolve().parents[4] / "fixtures" / "sdo" / "contracts" / "incident_request.json"


def _request(**update: object) -> IncidentRequest:
    request = IncidentRequest.model_validate_json(_FIXTURE.read_text(encoding="utf-8"))
    return request.model_copy(update=update) if update else request


def test_prompt_is_unchanged_when_the_flag_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SDO_LATE_FINDINGS", raising=False)
    baseline = _responder_prompt(_request())
    monkeypatch.setenv("SDO_LATE_FINDINGS", "off")

    assert _responder_prompt(_request()) == baseline
    assert "late-findings" not in baseline
    assert late_findings_guidance(_request()) == ""


@pytest.mark.parametrize("policy", ["commit", "recorded-actions"])
def test_pull_mode_adds_the_guidance_in_both_repair_modes(monkeypatch: pytest.MonkeyPatch, policy: str) -> None:
    monkeypatch.delenv("SDO_LATE_FINDINGS", raising=False)
    baseline = _responder_prompt(_request(repair_policy=policy))
    monkeypatch.setenv("SDO_LATE_FINDINGS", "pull")
    request = _request(repair_policy=policy)

    prompt = _responder_prompt(request)

    assert late_findings_guidance(request) in prompt
    assert f"--incident-id {request.incident_id}" in prompt
    assert "python3 -m sdo.operational_memory.late_findings" in prompt
    assert prompt.replace(late_findings_guidance(request), "") != ""
    assert len(prompt) > len(baseline)
    assert f"Repair evidence mode: {policy}" in prompt


def test_guidance_says_when_to_pull_and_to_treat_playbooks_as_hypotheses() -> None:
    text = late_findings_guidance(_request(), mode="pull")

    assert "once now" in text
    assert "before your first change" in text
    assert "hypothes" in text
    assert "live state" in text
    assert "never" in text
    assert "blindly" in text


def test_guidance_is_problem_agnostic() -> None:
    text = late_findings_guidance(_request(), mode="pull")

    assert _leaks(text.replace(_request().incident_id, "")) == []


def test_unknown_mode_is_rejected() -> None:
    with pytest.raises(ValueError, match="late"):
        late_findings_guidance(_request(), mode="push")


def test_broker_cli_accepts_late_findings_modes() -> None:
    parser = _argument_parser()
    base = ["--repository", "/r", "--worktree-root", "/w"]

    assert parser.parse_args(base).late_findings == "off"
    assert parser.parse_args([*base, "--late-findings", "pull"]).late_findings == "pull"
    with pytest.raises(SystemExit):
        parser.parse_args([*base, "--late-findings", "push"])
