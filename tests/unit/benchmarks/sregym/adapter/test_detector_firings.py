from __future__ import annotations

import json
import subprocess
import tarfile
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

import benchmarks.sregym.adapter.runtime as runtime
from sdo.controller_install import DETECTOR_FIRING_STREAM


def _entry(**extra: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "detector_id": "cause",
        "detector_class": "incident",
        "owner": "responder",
        "rule_id": "cause",
        "fingerprint": "cause",
        "parameter_bindings": {},
        "surfaced_playbooks": [],
        "first_activated_at": "2026-09-30T10:00:01Z",
        "last_seen_at": "2026-09-30T10:00:05Z",
        "relation": "before_dispatch",
        "activations": 1,
    }
    entry.update(extra)
    return entry


def test_receipt_reports_firing_timeline_and_booleans_from_the_closure() -> None:
    summary = runtime._detector_firing_summary(
        {
            "detector_timeline": [_entry()],
            "incident_detector_fired_before_dispatch": True,
            "incident_detector_fired_after_dispatch": False,
            "no_incident_detector_fired": False,
        }
    )

    assert summary["detector_firing_available"] is True
    assert summary["incident_detector_fired_before_dispatch"] is True
    assert summary["incident_detector_fired_after_dispatch"] is False
    assert summary["no_incident_detector_fired"] is False
    assert summary["detector_timeline"][0]["detector_id"] == "cause"


def test_receipt_marks_firing_telemetry_unavailable_for_older_closures() -> None:
    summary = runtime._detector_firing_summary({"final_detector_states": []})

    assert summary == {
        "detector_firing_available": False,
        "detector_timeline": [],
        "incident_detector_fired_before_dispatch": None,
        "incident_detector_fired_after_dispatch": None,
        "no_incident_detector_fired": None,
    }


def test_malformed_timeline_is_reported_and_never_fails_the_receipt() -> None:
    summary = runtime._detector_firing_summary({"detector_timeline": [{"detector_id": ""}]})

    assert summary["detector_firing_available"] is False
    assert "detector_timeline_error" in summary


def test_firing_stream_lives_under_the_exported_runtime_state() -> None:
    import posixpath

    from sdo.controller_install import RUNTIME_STATE_ROOT

    assert DETECTOR_FIRING_STREAM == "/workspace/.sdo-runtime/telemetry/detector-firings.jsonl"
    assert posixpath.relpath(posixpath.dirname(DETECTOR_FIRING_STREAM), RUNTIME_STATE_ROOT) in (
        runtime._EXPORTED_RUNTIME_PATHS
    )


def test_runtime_export_places_the_firing_stream_next_to_the_receipt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = tmp_path / "pod" / "telemetry"
    source.mkdir(parents=True)
    (source / "detector-firings.jsonl.1").write_text('{"event_id": "old"}\n', encoding="utf-8")
    (source / "detector-firings.jsonl").write_text('{"event_id": "new"}\n', encoding="utf-8")
    archive = tmp_path / "runtime.tar"
    with tarfile.open(archive, "w") as bundle:
        bundle.add(source, arcname="telemetry")
    real_run = subprocess.run

    def fake_run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        if args[0] == "kubectl":
            return subprocess.CompletedProcess(args, 0, archive.read_bytes(), b"")
        return real_run(args, **kwargs)

    monkeypatch.setattr(runtime.subprocess, "run", fake_run)
    results = tmp_path / "results"

    summary = runtime._export_runtime_artifacts("demo", results)

    assert summary["error"] is None
    stream = results / "detector_firings.jsonl"
    ids = [json.loads(line)["event_id"] for line in stream.read_text(encoding="utf-8").splitlines()]
    assert ids == ["old", "new"]


def test_receipt_reports_late_findings_consumption_only_when_the_closure_has_it() -> None:
    assert runtime._late_findings_summary({"final_detector_states": []}) == {}
    assert runtime._late_findings_summary({"late_findings_pull": None}) == {}

    summary = runtime._late_findings_summary(
        {
            "late_findings_pull": {
                "pulled": True,
                "pull_count": 2,
                "nonempty_pull_count": 1,
                "late_finding_detectors": ["learned"],
                "late_playbooks": [".sdo/playbooks/x/README.md"],
                "applied_late_playbooks": [".sdo/playbooks/x/README.md"],
            }
        }
    )

    assert summary["late_findings_pull"]["pull_count"] == 2
    assert summary["late_finding_consumed"] is True


def test_late_finding_consumed_requires_a_non_empty_pull() -> None:
    summary = runtime._late_findings_summary({"late_findings_pull": {"pulled": True, "pull_count": 1}})

    assert summary["late_finding_consumed"] is False
    malformed = runtime._late_findings_summary({"late_findings_pull": {"pull_count": "many"}})
    assert "late_findings_pull_error" in malformed
