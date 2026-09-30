"""The broker records whether the responder consumed a late finding, as closure evidence."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sdo.operational_memory import BrokerClosure, LateFindingsPullSummary
from sdo.operational_memory.late_findings import PULL_LOG_FILENAME
from tests.unit.sdo.operational_memory.test_broker_service import (
    AcceptRepairValidator,
    NoChangeSessionBackend,
    SessionReflector,
    _closure,
    _init_repository,
    _service,
    _write_memory,
)

_INCIDENT = "inc-20260709-0001"
_APPLIED = ".sdo/playbooks/missing-configmap/README.md"


def _target(tmp_path: Path) -> Path:
    target = tmp_path / "target"
    target.mkdir()
    _write_memory(target)
    _init_repository(target)
    # Production keeps runtime state out of the repository's status, as the usage logs already are.
    with (target / ".git" / "info" / "exclude").open("a", encoding="utf-8") as handle:
        handle.write(".sdo-runtime/\n")
    return target


def _pull(target: Path, *, count: int, playbooks: list[str]) -> None:
    log = target / ".sdo-runtime" / "telemetry" / PULL_LOG_FILENAME
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "incident_id": _INCIDENT,
                    "pulled_at": "t",
                    "late_finding_count": count,
                    "detectors": ["learned"] * count,
                    "playbooks": playbooks,
                }
            )
            + "\n"
        )


def test_closure_without_late_findings_evidence_still_loads() -> None:
    payload = _closure(Path("/w"), "b").model_dump(mode="json")
    payload.pop("late_findings_pull", None)

    assert BrokerClosure.model_validate(payload).late_findings_pull is None


def test_pull_mode_records_the_consumed_late_playbook(tmp_path: Path) -> None:
    target = _target(tmp_path)
    service = _service(
        target,
        tmp_path / "worktrees",
        AcceptRepairValidator(),
        reflector=SessionReflector(NoChangeSessionBackend()),
        late_findings="pull",
    )
    workspace = service.prepare_incident(_INCIDENT)
    _pull(target, count=0, playbooks=[])
    _pull(target, count=1, playbooks=[_APPLIED])

    service.process_closure(_closure(workspace.path, workspace.base_commit))

    ledger = service._required_ledger(_INCIDENT)
    assert ledger.closure is not None
    assert ledger.closure.late_findings_pull == LateFindingsPullSummary(
        pulled=True,
        pull_count=2,
        nonempty_pull_count=1,
        late_finding_detectors=["learned"],
        late_playbooks=[_APPLIED],
        applied_late_playbooks=[_APPLIED],
    )


def test_pull_mode_without_a_pull_records_not_pulled(tmp_path: Path) -> None:
    target = _target(tmp_path)
    service = _service(
        target,
        tmp_path / "worktrees",
        AcceptRepairValidator(),
        reflector=SessionReflector(NoChangeSessionBackend()),
        late_findings="pull",
    )
    workspace = service.prepare_incident(_INCIDENT)

    service.process_closure(_closure(workspace.path, workspace.base_commit))

    summary = service._required_ledger(_INCIDENT).closure.late_findings_pull
    assert summary is not None
    assert summary.pulled is False
    assert summary.pull_count == 0


def test_off_mode_adds_no_evidence(tmp_path: Path) -> None:
    target = _target(tmp_path)
    service = _service(
        target, tmp_path / "worktrees", AcceptRepairValidator(), reflector=SessionReflector(NoChangeSessionBackend())
    )
    workspace = service.prepare_incident(_INCIDENT)
    _pull(target, count=1, playbooks=[_APPLIED])

    service.process_closure(_closure(workspace.path, workspace.base_commit))

    assert service._required_ledger(_INCIDENT).closure.late_findings_pull is None


def test_late_findings_mode_is_validated(tmp_path: Path) -> None:
    target = _target(tmp_path)

    with pytest.raises(ValueError, match="late-findings"):
        _service(target, tmp_path / "worktrees", AcceptRepairValidator(), late_findings="push")
