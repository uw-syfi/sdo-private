from __future__ import annotations

import json
from typing import TYPE_CHECKING

from sdo.__main__ import main

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_incident_late_findings_subcommand_prints_compact_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    stream = tmp_path / ".sdo-runtime" / "telemetry" / "detector-firings.jsonl"
    stream.parent.mkdir(parents=True)
    stream.write_text("", encoding="utf-8")
    worktree = tmp_path / "worktrees" / "inc-1"
    worktree.mkdir(parents=True)
    monkeypatch.chdir(worktree)
    monkeypatch.delenv("SDO_FIRING_TELEMETRY_PATH", raising=False)

    assert main(["incident", "late-findings", "--incident-id", "inc-1"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["incident_id"] == "inc-1"
    assert payload["late_findings"] == []
    assert payload["telemetry_available"] is True
