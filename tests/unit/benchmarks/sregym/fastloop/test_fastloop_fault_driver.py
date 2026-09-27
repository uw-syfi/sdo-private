from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import pytest

from benchmarks.sregym.fastloop.fault_driver import FastloopFaultError, SregymFaultDriver


@dataclass
class FakeWorker:
    health_sequence: list[bool] = field(default_factory=lambda: [True])
    requests: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    def request(self, op: str, *, timeout: float = 0, **args: Any) -> dict[str, Any]:
        self.requests.append((op, args))
        if op == "inject":
            return {"started_at": 1790000000.0, "finished_at": 1790000006.5}
        if op == "oracle":
            return {"kind": "sregym-mitigation-oracle", "success": True, "details": {"success": True}}
        if op == "recover":
            return {"seconds": 4.0}
        if op == "health":
            healthy = self.health_sequence.pop(0) if len(self.health_sequence) > 1 else self.health_sequence[0]
            return {"healthy": healthy, "unready": [] if healthy else ["mongodb-geo"]}
        raise AssertionError(op)


def _driver(worker: FakeWorker, timeout: float = 30.0) -> SregymFaultDriver:
    now = [0.0]

    def sleep(seconds: float) -> None:
        now[0] += seconds

    return SregymFaultDriver(
        worker, namespace="hotel-reservation", health_timeout_seconds=timeout, monotonic=lambda: now[0], sleep=sleep
    )


def test_injection_window_comes_from_the_worker_clock() -> None:
    window = _driver(FakeWorker()).inject("missing_configmap_hotel_reservation")

    assert window.started_at == datetime.fromtimestamp(1790000000.0, tz=timezone.utc)
    assert (window.finished_at - window.started_at).total_seconds() == pytest.approx(6.5)


def test_recovery_waits_until_the_app_is_healthy_again() -> None:
    worker = FakeWorker(health_sequence=[False, False, True])

    seconds = _driver(worker).recover()

    assert [op for op, _ in worker.requests] == ["recover", "health", "health", "health"]
    assert seconds == pytest.approx(2 * 2.0)


def test_recovery_that_never_becomes_healthy_fails_the_loop() -> None:
    with pytest.raises(FastloopFaultError, match="mongodb-geo"):
        _driver(FakeWorker(health_sequence=[False]), timeout=5.0).recover()


def test_oracle_verdict_is_typed() -> None:
    verdict = _driver(FakeWorker()).oracle()

    assert verdict.kind == "sregym-mitigation-oracle"
    assert verdict.success
