from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

import pytest

from benchmarks.sregym.fastloop.cli import build_parser, format_incidents, format_summary, main
from benchmarks.sregym.fastloop.environment import FastloopEnvironment
from benchmarks.sregym.fastloop.records import IncidentRecord, OracleVerdict, append_record

if TYPE_CHECKING:
    from pathlib import Path

T0 = datetime(2026, 9, 27, 12, 0, 0, tzinfo=timezone.utc)


def _record(index: int, agent: str) -> IncidentRecord:
    return IncidentRecord(
        run_id=f"run-{agent}",
        index=index,
        agent=agent,
        problem_id="missing_configmap_hotel_reservation",
        model="gpt-6-luna",
        injection_started_at=T0,
        injection_finished_at=T0 + timedelta(seconds=6),
        detected_at=T0 + timedelta(seconds=9) if agent == "sdo" else None,
        mitigation_applied_at=T0 + timedelta(seconds=45),
        oracle=OracleVerdict(kind="sregym-mitigation-oracle", success=True),
        incident_wall_seconds=150.0,
        warm_path=True if agent == "sdo" else None,
    )


def test_summary_reads_every_incidents_file_under_a_run_dir(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    append_record(tmp_path / "results" / "a" / "incidents.jsonl", _record(0, "sdo"))
    append_record(tmp_path / "results" / "a" / "incidents.jsonl", _record(1, "sdo"))
    append_record(tmp_path / "results" / "b" / "incidents.jsonl", _record(0, "codex"))

    assert main(["summary", str(tmp_path)]) == 0

    output = capsys.readouterr().out
    assert "run-sdo" in output
    assert "run-codex" in output
    assert "inj->det_s" in output
    assert "9.0" in output


def test_summary_of_nothing_fails(tmp_path: Path) -> None:
    assert main(["summary", str(tmp_path)]) == 1


def test_tables_have_one_row_per_incident_and_per_agent() -> None:
    records = [_record(0, "sdo"), _record(1, "sdo"), _record(0, "codex")]

    assert len(format_incidents(records).splitlines()) == 4
    assert len(format_summary(records).splitlines()) == 3


def test_run_requires_an_environment_from_up(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="fastloop up"):
        FastloopEnvironment.load(tmp_path)


def test_parser_defaults_match_the_benchmark_configuration() -> None:
    args = build_parser().parse_args(["run", "--run-dir", "x", "--agent", "sdo"])

    assert (args.model, args.provider, args.reflection_session, args.incidents) == ("gpt-6-luna", "codex", "resume", 1)
    up = build_parser().parse_args(["up", "--run-dir", "x"])
    assert up.cpu_limit == "3"
    assert up.cluster_prefix == "fastloop-w"
    assert up.no_sandbox is False
