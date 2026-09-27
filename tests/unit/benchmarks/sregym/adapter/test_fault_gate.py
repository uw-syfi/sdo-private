from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from benchmarks.sregym.adapter.fault_gate import (
    FaultGateError,
    controller_baseline_clear,
    inject_fault_after_controller_baseline,
)


def _evaluation(*statuses: str) -> str:
    return json.dumps(
        {
            "controller_iteration": 0,
            "returncode": 0,
            "findings": [{"rule_id": f"r{index}", "status": status} for index, status in enumerate(statuses)],
        }
    )


def test_baseline_requires_a_controller_evaluation_without_active_findings() -> None:
    assert not controller_baseline_clear("")
    assert not controller_baseline_clear("building detectors\n")
    assert not controller_baseline_clear(_evaluation("active", "resolved"))
    assert controller_baseline_clear("noise\n" + _evaluation("resolved"))
    assert not controller_baseline_clear(_evaluation("resolved") + "\n" + _evaluation("active"))
    assert controller_baseline_clear(json.dumps({"controller_iteration": 3, "returncode": 0, "findings": None}))


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


NOT_BEFORE = datetime(2026, 9, 27, 12, 0, 0, tzinfo=timezone.utc)


def _logs(*outputs: tuple[int, str], created: str = "2026-09-27T12:00:05Z"):
    remaining = list(outputs)
    calls: list[list[str]] = []

    def kubectl(args: list[str], *, namespace: str, check: bool) -> subprocess.CompletedProcess[str]:
        assert namespace == "hotel"
        assert check is False
        calls.append(args)
        if args[0] == "get":
            job = {"metadata": {"creationTimestamp": created}}
            return subprocess.CompletedProcess(args, 0, stdout=json.dumps(job), stderr="")
        returncode, stdout = remaining.pop(0) if len(remaining) > 1 else remaining[0]
        return subprocess.CompletedProcess(args, returncode, stdout=stdout, stderr="not yet")

    return kubectl, calls


def test_gate_injects_once_after_controller_reports_all_clear() -> None:
    clock = _Clock()
    kubectl, calls = _logs((1, ""), (0, _evaluation("active")), (0, _evaluation("resolved")))
    injected: list[str] = []

    timings = inject_fault_after_controller_baseline(
        "hotel",
        inject=lambda: injected.append("fault"),
        kubectl_runner=kubectl,
        not_before=NOT_BEFORE,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
        timeout_seconds=60,
    )

    assert injected == ["fault"]
    assert ["logs", "job/sdo-controller-run", "--tail=200"] in calls
    assert timings["controller_baseline_wait"] == pytest.approx(2.0)


def test_gate_fails_without_injecting_when_controller_never_settles() -> None:
    clock = _Clock()
    kubectl, _ = _logs((0, _evaluation("active")))
    injected: list[str] = []

    with pytest.raises(FaultGateError, match=r"all-clear.*active findings \['r0'\]"):
        inject_fault_after_controller_baseline(
            "hotel",
            inject=lambda: injected.append("fault"),
            kubectl_runner=kubectl,
            not_before=NOT_BEFORE,
            monotonic=clock.monotonic,
            sleep=clock.sleep,
            timeout_seconds=10,
        )

    assert injected == []


def test_gate_ignores_a_previous_rounds_controller_job() -> None:
    clock = _Clock()
    kubectl, calls = _logs((0, _evaluation("resolved")), created="2026-09-27T11:59:00Z")
    injected: list[str] = []

    with pytest.raises(FaultGateError):
        inject_fault_after_controller_baseline(
            "hotel",
            inject=lambda: injected.append("fault"),
            kubectl_runner=kubectl,
            not_before=NOT_BEFORE,
            monotonic=clock.monotonic,
            sleep=clock.sleep,
            timeout_seconds=5,
        )

    assert injected == []
    assert all(call[0] == "get" for call in calls)


def test_sdo_registry_entry_defers_fault_injection() -> None:
    import yaml

    registry = Path(__file__).resolve().parents[5] / "benchmarks" / "sregym" / "registry.yaml"
    agents = {agent["name"]: agent for agent in yaml.safe_load(registry.read_text(encoding="utf-8"))["agents"]}

    assert agents["sdo_codex"]["defer_fault_injection"] is True
    assert "defer_fault_injection" not in agents["crucible"]
