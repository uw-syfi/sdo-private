"""Pull-before-act: findings that activated after an incident's responder was dispatched.

The incident request is an immutable payload, so a finding that activates after the responder launched cannot
join it. The controller's durable firing telemetry already records each such activation with the open incident
id and the ``after_dispatch`` relation. This module lets the responder *pull* that evidence for its own incident
through the repository volume it already mounts; the controller pushes nothing and adds no dispatch latency.

The reader is read-only with respect to the cluster, the repository, and the stream. Its only write is a small
pull receipt beside the stream, which the broker folds into the closure evidence so an experiment can measure
whether a late finding was consumed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from collections.abc import Mapping

LateFindingsMode = Literal["off", "pull"]
LATE_FINDINGS_MODES: tuple[str, ...] = ("off", "pull")
TELEMETRY_FILENAME = "detector-firings.jsonl"
PULL_LOG_FILENAME = "late-findings-pulls.jsonl"
TELEMETRY_ENV = "SDO_FIRING_TELEMETRY_PATH"
REPORT_SCHEMA = "sdo.late-findings/v1"
LATE_FINDINGS_COMMAND = "python3 -m sdo.operational_memory.late_findings"
_AFTER_DISPATCH = "after_dispatch"
_RELATIVE_STREAM = Path(".sdo-runtime") / "telemetry" / TELEMETRY_FILENAME


class LateFinding(BaseModel):
    """One finding that activated after the responder for the incident was dispatched."""

    model_config = ConfigDict(extra="forbid")

    detector_id: str
    detector_class: str = ""
    rule_id: str = ""
    fingerprint: str = ""
    severity: str = ""
    parameter_bindings: dict[str, Any] = Field(default_factory=dict)
    surfaced_playbooks: list[str] = Field(default_factory=list)
    activated_at: str
    still_active: bool = True


class LateFindingsReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["sdo.late-findings/v1"] = REPORT_SCHEMA
    incident_id: str
    telemetry_available: bool
    late_findings: list[LateFinding] = Field(default_factory=list)


class LateFindingsPullSummary(BaseModel):
    """Closure evidence of how the responder used the pull command."""

    model_config = ConfigDict(extra="forbid")

    pulled: bool = False
    pull_count: int = 0
    nonempty_pull_count: int = 0
    late_finding_detectors: list[str] = Field(default_factory=list)
    late_playbooks: list[str] = Field(default_factory=list)
    # Late playbooks the responder reported as applied; the strongest "consumed" signal.
    applied_late_playbooks: list[str] = Field(default_factory=list)


def locate_telemetry(start: Path, *, environ: Mapping[str, str] | None = None) -> Path | None:
    """Find the firing stream: the environment override, else the nearest ancestor holding one."""

    env = os.environ if environ is None else environ
    override = env.get(TELEMETRY_ENV, "").strip()
    if override:
        return Path(override)
    current = start.resolve()
    for candidate in (current, *current.parents):
        stream = candidate / _RELATIVE_STREAM
        if stream.parent.is_dir():
            return stream
    return None


def _records(stream: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for part in (stream.with_name(stream.name + ".1"), stream):
        try:
            text = part.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for line in text.splitlines():
            try:
                record = json.loads(line)
            except ValueError:
                continue  # a torn final line is repaired by the writer; skip it here
            if isinstance(record, dict):
                records.append(record)
    return records


def _existing_playbooks(paths: object, worktree: Path) -> list[str]:
    root = worktree.resolve()
    kept: list[str] = []
    for raw in paths if isinstance(paths, list) else []:
        if not isinstance(raw, str) or not raw or Path(raw).is_absolute():
            continue
        resolved = (root / raw).resolve()
        if root in resolved.parents and resolved.is_file() and raw not in kept:
            kept.append(raw)
    return kept


def read_late_findings(stream: Path, incident_id: str, worktree: Path) -> LateFindingsReport:
    """Findings of ``incident_id`` that activated after its responder was dispatched."""

    available = stream.is_file() or stream.with_name(stream.name + ".1").is_file()
    entries: dict[tuple[str, str], LateFinding] = {}
    for record in _records(stream):
        if record.get("incident_id") != incident_id:
            continue
        key = (str(record.get("detector_id", "")), str(record.get("fingerprint", "")))
        event = record.get("event")
        if event == "activated" and record.get("dispatch_relation") == _AFTER_DISPATCH:
            existing = entries.get(key)
            if existing is not None:
                existing.still_active = True
                continue
            entries[key] = LateFinding(
                detector_id=key[0],
                detector_class=str(record.get("detector_class", "")),
                rule_id=str(record.get("rule_id", "")),
                fingerprint=key[1],
                severity=str(record.get("severity", "")),
                parameter_bindings=dict(record.get("parameter_bindings") or {}),
                surfaced_playbooks=_existing_playbooks(record.get("surfaced_playbooks"), worktree),
                activated_at=str(record.get("recorded_at", "")),
            )
        elif event == "cleared" and key in entries:
            entries[key].still_active = False
    return LateFindingsReport(
        incident_id=incident_id,
        telemetry_available=available,
        late_findings=sorted(entries.values(), key=lambda finding: finding.activated_at),
    )


def record_pull(stream: Path, report: LateFindingsReport, *, at: str | None = None) -> bool:
    """Append a receipt for one pull beside the stream; ``False`` when it cannot be written."""

    receipt = {
        "incident_id": report.incident_id,
        "pulled_at": at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "late_finding_count": len(report.late_findings),
        "detectors": [finding.detector_id for finding in report.late_findings],
        "playbooks": sorted({path for finding in report.late_findings for path in finding.surfaced_playbooks}),
    }
    try:
        stream.parent.mkdir(parents=True, exist_ok=True)
        with (stream.parent / PULL_LOG_FILENAME).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(receipt, sort_keys=True) + "\n")
    except OSError:
        return False
    return True


def summarize_pulls(log: Path, incident_id: str, *, applied_playbooks: list[str]) -> LateFindingsPullSummary:
    """Fold the pull receipts of one incident into closure evidence."""

    pulls = 0
    nonempty = 0
    detectors: set[str] = set()
    playbooks: set[str] = set()
    try:
        lines = log.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        lines = []
    for line in lines:
        try:
            receipt = json.loads(line)
        except ValueError:
            continue
        if not isinstance(receipt, dict) or receipt.get("incident_id") != incident_id:
            continue
        pulls += 1
        if receipt.get("late_finding_count"):
            nonempty += 1
        detectors.update(str(item) for item in receipt.get("detectors") or [])
        playbooks.update(str(item) for item in receipt.get("playbooks") or [])
    applied = set(applied_playbooks)
    return LateFindingsPullSummary(
        pulled=pulls > 0,
        pull_count=pulls,
        nonempty_pull_count=nonempty,
        late_finding_detectors=sorted(detectors),
        late_playbooks=sorted(playbooks),
        applied_late_playbooks=sorted(playbooks & applied),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="sdo incident late-findings",
        description="Print, as compact JSON, the findings that activated after this incident's responder was "
        "dispatched, with their surfaced playbook paths. Read-only.",
    )
    parser.add_argument("--incident-id", required=True, help="the incident this responder is handling")
    parser.add_argument("--telemetry-path", type=Path, help=f"firing stream (default: ${TELEMETRY_ENV} or discovered)")
    args = parser.parse_args(argv)
    worktree = Path.cwd()
    stream = args.telemetry_path or locate_telemetry(worktree)
    if stream is None:
        report = LateFindingsReport(incident_id=args.incident_id, telemetry_available=False)
    else:
        report = read_late_findings(stream, args.incident_id, worktree)
        if not record_pull(stream, report):
            print("late-findings: could not record the pull receipt", file=sys.stderr)
    print(report.model_dump_json())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
