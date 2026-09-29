from __future__ import annotations

import json
from typing import TYPE_CHECKING

from benchmarks.sregym.assurance.phase1_timeline import (
    ExecUsage,
    count_exec_usage,
    first_finding_after,
    incident_open_epoch,
)

if TYPE_CHECKING:
    from pathlib import Path


def _call(ts: str, cmd: str, call_id: str = "c1") -> str:
    payload = {"type": "function_call", "call_id": call_id, "arguments": json.dumps({"cmd": cmd})}
    return json.dumps({"timestamp": ts, "payload": payload})


def _controller_line(clock: str, findings: str, iteration: int) -> str:
    return f'2026-09-29T{clock}Z {{"controller_iteration":{iteration},"findings":{findings}}}'


def test_count_exec_usage_finds_exec_attach_and_port_forward(tmp_path: Path) -> None:
    rollout = tmp_path / "rollout-x.jsonl"
    rollout.write_text(
        "\n".join(
            [
                _call("2026-09-29T00:00:01Z", "kubectl -n hotel exec deploy/frontend -- env", "a"),
                _call("2026-09-29T00:00:02Z", "kubectl port-forward svc/x 8080:80 -n hotel", "b"),
                _call("2026-09-29T00:00:03Z", "kubectl get pods && kubectl attach pod/x", "c"),
                _call("2026-09-29T00:00:04Z", "kubectl get pods", "d"),
                _call("2026-09-29T00:00:05Z", "echo kubectl exec is not run", "e"),
            ]
        ),
        encoding="utf-8",
    )
    assert count_exec_usage([rollout]) == ExecUsage(exec_calls=1, attach_calls=1, port_forward_calls=1, total_calls=5)


def test_incident_open_epoch_reads_the_nanosecond_suffix() -> None:
    assert incident_open_epoch("Hotel Reservation-1790641097912066323") == 1790641097.912066323


def test_first_finding_after_skips_healthy_lines_and_earlier_findings(tmp_path: Path) -> None:
    log = tmp_path / "controller.log"
    log.write_text(
        "\n".join(
            [
                _controller_line("00:00:00.000000000", '[{"detector_id":"a","rule_id":"r","status":"active"}]', 1),
                _controller_line("00:00:10.000000000", "[]", 2),
                _controller_line(
                    "00:00:20.500000000", '[{"detector_id":"traffic-health","rule_id":"s","status":"active"}]', 3
                ),
            ]
        ),
        encoding="utf-8",
    )
    epoch_start = 1790640000.0  # 2026-09-29T00:00:00Z
    found = first_finding_after([log], after=epoch_start + 5)
    assert found is not None
    assert found.detector_id == "traffic-health"
    assert abs(found.epoch - (epoch_start + 20.5)) < 1e-6
