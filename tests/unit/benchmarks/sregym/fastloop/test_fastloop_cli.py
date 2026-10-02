from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from benchmarks.sregym.fastloop.cli import build_parser, format_incidents, format_summary, main
from benchmarks.sregym.fastloop.environment import FastloopEnvironment
from benchmarks.sregym.fastloop.records import IncidentRecord, OracleVerdict, append_record

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

    assert (args.model, args.provider, args.reflection_session, args.incidents) == ("gpt-6-luna", "codex", "fresh", 1)
    up = build_parser().parse_args(["up", "--run-dir", "x"])
    assert up.cpu_limit == "3"
    assert up.cluster_prefix == "fastloop-w"
    assert up.no_sandbox is False
    assert up.kind_worker_nodes == 1


def test_up_builds_the_same_one_worker_kind_lane_as_the_experiments(tmp_path: Path) -> None:
    from benchmarks.sregym.fastloop.cli import up_worker_environment

    args = build_parser().parse_args(
        ["up", "--run-dir", str(tmp_path), "--cluster-prefix", "assure-s", "--worker-id", "1"]
    )
    environment = up_worker_environment(args, workspace=tmp_path / "workspace")

    assert environment["SREGYM_KIND_CLUSTER_NAME"] == "assure-s1"
    assert environment["SREGYM_KIND_WORKER_NODES"] == "1"
    assert environment["SREGYM_APP_SOURCE_DIR"] == str(tmp_path / "workspace")
    assert environment["SREGYM_KIND_REQUIRE_NETWORK_POLICY"] == "1"


def test_up_rejects_a_negative_worker_count(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="kind-worker-nodes"):
        main(["up", "--run-dir", str(tmp_path), "--kind-worker-nodes", "-1", "--no-preflight"])


class _StopAtProxy(Exception):
    pass


def test_codex_run_hands_the_worker_an_agent_kubeconfig_path_it_can_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from benchmarks.sregym.fastloop import cli

    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    (codex_home / "auth.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    requested: list[Path] = []

    class Worker:
        def request(self, op: str, **args: object) -> dict[str, object]:
            assert op == "proxy"
            requested.append(Path(str(args["kubeconfig"])))
            raise _StopAtProxy

    environment = FastloopEnvironment(
        run_dir=tmp_path,
        cluster="fastloop-w0",
        kubeconfig=tmp_path / "kubeconfig",
        namespace="hotel-reservation",
        application="Hotel Reservation",
        workspace=tmp_path / "workspace",
        sregym_dir=tmp_path / "sregym",
        private_tmp=None,
    )
    args = build_parser().parse_args(["run", "--run-dir", str(tmp_path), "--agent", "codex"])
    results_dir = tmp_path / "results" / "fresh-run"

    with pytest.raises(_StopAtProxy):
        cli._run_codex(args, environment, results_dir, Worker(), None, None)  # type: ignore[arg-type]

    assert requested == [results_dir / "agent.kubeconfig"]
    assert results_dir.is_dir()


def test_sdo_run_validates_the_lifecycle_in_the_images_the_environment_was_built_with(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from benchmarks.sregym import adapter
    from benchmarks.sregym.fastloop import cli, sdo_agent
    from benchmarks.sregym.fastloop.environment import Images

    captured: dict[str, object] = {}
    lifecycle_calls: list[dict[str, object]] = []

    class RecordingAgent:
        def __init__(self, settings: object, **arguments: object) -> None:
            captured.update(arguments)

    def record_lifecycle(*_args: object, **kwargs: object) -> bool:
        lifecycle_calls.append(kwargs)
        return False

    monkeypatch.setattr(sdo_agent, "SdoPersistentAgent", RecordingAgent)
    monkeypatch.setattr(adapter, "run_or_reuse_lifecycle", record_lifecycle)
    environment = FastloopEnvironment(
        run_dir=tmp_path,
        cluster="fastloop-w0",
        kubeconfig=tmp_path / "kubeconfig",
        namespace="hotel-reservation",
        application="Hotel Reservation",
        workspace=tmp_path / "workspace",
        sregym_dir=tmp_path / "sregym",
        private_tmp=None,
        images=Images(controller="sdo-controller:t1", responder="sdo-sregym-responder:t1", validator="v:t1"),
    )
    args = build_parser().parse_args(["run", "--run-dir", str(tmp_path), "--agent", "sdo"])

    cli._sdo_agent(args, environment, tmp_path / "results")
    captured["run_lifecycle"](object())  # type: ignore[operator]

    assert lifecycle_calls[0]["validator_image"] == "v:t1"


def test_run_takes_an_optional_detection_timeout() -> None:
    base = ["run", "--run-dir", "/tmp/x", "--agent", "sdo"]

    assert build_parser().parse_args(base).detection_timeout is None
    assert build_parser().parse_args([*base, "--detection-timeout", "120"]).detection_timeout == 120.0
