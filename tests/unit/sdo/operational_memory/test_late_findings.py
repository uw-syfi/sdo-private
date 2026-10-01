"""Pull-before-act: the responder reads findings that activated after its dispatch."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from sdo.operational_memory.late_findings import (
    LATE_FINDINGS_MODES,
    PULL_LOG_FILENAME,
    TELEMETRY_FILENAME,
    LateFindingsPullSummary,
    locate_telemetry,
    main,
    read_late_findings,
    record_pull,
    summarize_pulls,
)

_PLAYBOOK = ".sdo/playbooks/learned/README.md"


def _record(**extra: Any) -> dict[str, Any]:
    record: dict[str, Any] = {
        "schema_version": "sdo.detector-firing/v1",
        "event_id": "e",
        "event": "activated",
        "recorded_at": "2026-09-30T10:00:12Z",
        "detector_id": "learned",
        "detector_class": "incident",
        "owner": "responder",
        "rule_id": "learned-rule",
        "fingerprint": "learned-fp",
        "severity": "warn",
        "parameter_bindings": {"workload": {"kind": "Deployment", "name": "web"}},
        "surfaced_playbooks": [_PLAYBOOK],
        "incident_id": "demo-1",
        "dispatch_relation": "after_dispatch",
    }
    record.update(extra)
    return record


def _write(path: Path, records: list[dict[str, Any]], *, torn: bool = False) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(json.dumps(r) + "\n" for r in records)
    path.write_text(text + ('{"event": "acti' if torn else ""), encoding="utf-8")
    return path


@pytest.fixture
def worktree(tmp_path: Path) -> Path:
    root = tmp_path / "worktrees" / "demo-1"
    (root / ".sdo" / "playbooks" / "learned").mkdir(parents=True)
    (root / _PLAYBOOK).write_text("# playbook\n", encoding="utf-8")
    return root


@pytest.fixture
def stream(tmp_path: Path) -> Path:
    return tmp_path / ".sdo-runtime" / "telemetry" / TELEMETRY_FILENAME


def test_modes_are_off_and_pull() -> None:
    assert LATE_FINDINGS_MODES == ("off", "pull")


def test_returns_only_this_incidents_after_dispatch_activations(stream: Path, worktree: Path) -> None:
    _write(
        stream,
        [
            _record(),
            _record(event_id="b", detector_id="early", fingerprint="early-fp", dispatch_relation="no_incident"),
            _record(event_id="c", event="batched", detector_id="early", dispatch_relation="before_dispatch"),
            _record(event_id="d", detector_id="other", incident_id="demo-0"),
            _record(event_id="f", detector_id="quiet", incident_id="", dispatch_relation="no_incident"),
        ],
    )

    report = read_late_findings(stream, "demo-1", worktree)

    assert report.telemetry_available is True
    assert [f.detector_id for f in report.late_findings] == ["learned"]
    (finding,) = report.late_findings
    assert finding.rule_id == "learned-rule"
    assert finding.severity == "warn"
    assert finding.activated_at == "2026-09-30T10:00:12Z"
    assert finding.parameter_bindings == {"workload": {"kind": "Deployment", "name": "web"}}
    assert finding.surfaced_playbooks == [_PLAYBOOK]
    assert finding.still_active is True


def test_empty_list_when_nothing_activated_late(stream: Path, worktree: Path) -> None:
    _write(stream, [_record(dispatch_relation="before_dispatch")])

    report = read_late_findings(stream, "demo-1", worktree)

    assert report.telemetry_available is True
    assert report.late_findings == []


def test_missing_stream_is_an_empty_unavailable_report(tmp_path: Path, worktree: Path) -> None:
    report = read_late_findings(tmp_path / "nope" / TELEMETRY_FILENAME, "demo-1", worktree)

    assert report.telemetry_available is False
    assert report.late_findings == []


def test_cleared_finding_is_reported_inactive(stream: Path, worktree: Path) -> None:
    _write(stream, [_record(), _record(event_id="x", event="cleared", recorded_at="2026-09-30T10:00:20Z")])

    (finding,) = read_late_findings(stream, "demo-1", worktree).late_findings

    assert finding.still_active is False


def test_reactivation_keeps_one_entry_with_first_activation_time(stream: Path, worktree: Path) -> None:
    _write(
        stream,
        [
            _record(),
            _record(event_id="x", event="cleared", recorded_at="2026-09-30T10:00:20Z"),
            _record(event_id="y", recorded_at="2026-09-30T10:00:30Z"),
        ],
    )

    (finding,) = read_late_findings(stream, "demo-1", worktree).late_findings

    assert finding.activated_at == "2026-09-30T10:00:12Z"
    assert finding.still_active is True


def test_rotated_file_and_torn_last_line_are_handled(stream: Path, worktree: Path) -> None:
    rotated = stream.with_name(stream.name + ".1")
    _write(rotated, [_record(detector_id="old", fingerprint="old-fp")])
    _write(stream, [_record()], torn=True)

    report = read_late_findings(stream, "demo-1", worktree)

    assert sorted(f.detector_id for f in report.late_findings) == ["learned", "old"]


def test_only_existing_contained_playbooks_are_surfaced(stream: Path, worktree: Path) -> None:
    _write(
        stream,
        [
            _record(
                surfaced_playbooks=[
                    _PLAYBOOK,
                    ".sdo/playbooks/missing/README.md",
                    "../../../etc/passwd",
                    "/etc/passwd",
                ]
            )
        ],
    )

    (finding,) = read_late_findings(stream, "demo-1", worktree).late_findings

    assert finding.surfaced_playbooks == [_PLAYBOOK]


def test_locate_telemetry_walks_up_from_the_worktree(tmp_path: Path, stream: Path, worktree: Path) -> None:
    _write(stream, [])

    assert locate_telemetry(worktree, environ={}) == stream
    assert locate_telemetry(worktree, environ={"SDO_FIRING_TELEMETRY_PATH": "/x/y.jsonl"}) == Path("/x/y.jsonl")
    assert locate_telemetry(tmp_path / "worktrees", environ={}) == stream
    lone = tmp_path.parent / "lone-dir"
    lone.mkdir(exist_ok=True)
    assert locate_telemetry(lone, environ={}) is None


def test_pull_receipts_count_pulls_and_nonempty_pulls(stream: Path, worktree: Path) -> None:
    _write(stream, [])
    log = stream.parent / PULL_LOG_FILENAME
    empty = read_late_findings(stream, "demo-1", worktree)
    _write(stream, [_record()])
    full = read_late_findings(stream, "demo-1", worktree)

    assert record_pull(stream, empty, at="2026-09-30T10:00:02Z") is True
    assert record_pull(stream, full, at="2026-09-30T10:00:40Z") is True
    other = full.model_copy(update={"incident_id": "demo-0"})
    record_pull(stream, other, at="2026-09-30T10:00:41Z")

    summary = summarize_pulls(log, "demo-1", applied_playbooks=[_PLAYBOOK, ".sdo/playbooks/else/README.md"])

    assert summary == LateFindingsPullSummary(
        pulled=True,
        pull_count=2,
        nonempty_pull_count=1,
        late_finding_detectors=["learned"],
        late_playbooks=[_PLAYBOOK],
        applied_late_playbooks=[_PLAYBOOK],
    )


def test_no_pull_log_means_not_pulled(tmp_path: Path) -> None:
    summary = summarize_pulls(tmp_path / PULL_LOG_FILENAME, "demo-1", applied_playbooks=[])

    assert summary.pulled is False
    assert summary.pull_count == 0
    assert summary.nonempty_pull_count == 0


def test_record_pull_failure_is_reported_not_raised(tmp_path: Path, worktree: Path) -> None:
    blocked = tmp_path / "file"
    blocked.write_text("x", encoding="utf-8")
    report = read_late_findings(blocked / "sub" / TELEMETRY_FILENAME, "demo-1", worktree)

    assert record_pull(blocked / "sub" / TELEMETRY_FILENAME, report, at="t") is False


def test_cli_prints_compact_json_and_leaves_the_worktree_untouched(
    stream: Path, worktree: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(stream, [_record()])
    before = sorted(p.relative_to(worktree) for p in worktree.rglob("*"))
    monkeypatch.chdir(worktree)
    monkeypatch.delenv("SDO_FIRING_TELEMETRY_PATH", raising=False)

    assert main(["--incident-id", "demo-1"]) == 0

    out = capsys.readouterr().out
    assert out.count("\n") == 1  # one compact JSON line
    payload = json.loads(out)
    assert payload["incident_id"] == "demo-1"
    assert payload["late_findings"][0]["detector_id"] == "learned"
    assert sorted(p.relative_to(worktree) for p in worktree.rglob("*")) == before
    assert (stream.parent / PULL_LOG_FILENAME).is_file()


def test_cli_without_a_stream_succeeds_with_an_empty_list(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    lone = tmp_path / "lone"
    lone.mkdir()
    monkeypatch.chdir(lone)
    monkeypatch.delenv("SDO_FIRING_TELEMETRY_PATH", raising=False)

    assert main(["--incident-id", "demo-1"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["late_findings"] == []
    assert payload["telemetry_available"] is False
