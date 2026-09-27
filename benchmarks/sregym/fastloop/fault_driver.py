"""Fault injection, grading, and recovery through the SREGym fast-loop worker."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Protocol

from benchmarks.sregym.fastloop.loop import InjectionWindow
from benchmarks.sregym.fastloop.records import OracleVerdict

if TYPE_CHECKING:
    from collections.abc import Callable

HEALTH_POLL_SECONDS = 2.0
#: The oracle waits up to 60 s for rollouts itself; injection and recovery wait on rollouts too.
WORKER_OP_TIMEOUT_SECONDS = 900.0


class FastloopFaultError(RuntimeError):
    """Raised when a fault cannot be recovered to a healthy application."""


class WorkerRequester(Protocol):
    def request(self, op: str, *, timeout: float = ..., **args: Any) -> dict[str, Any]: ...


def _utc(epoch: object) -> datetime:
    if not isinstance(epoch, (int, float)):
        raise FastloopFaultError(f"worker returned a non-numeric timestamp {epoch!r}")
    return datetime.fromtimestamp(float(epoch), tz=timezone.utc)


class SregymFaultDriver:
    def __init__(
        self,
        worker: WorkerRequester,
        *,
        namespace: str,
        health_timeout_seconds: float = 300.0,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if health_timeout_seconds <= 0:
            raise ValueError("health_timeout_seconds must be positive")
        self._worker = worker
        self._namespace = namespace
        self._health_timeout = health_timeout_seconds
        self._monotonic = monotonic
        self._sleep = sleep

    def inject(self, problem_id: str) -> InjectionWindow:
        reply = self._worker.request("inject", problem_id=problem_id, timeout=WORKER_OP_TIMEOUT_SECONDS)
        return InjectionWindow(started_at=_utc(reply.get("started_at")), finished_at=_utc(reply.get("finished_at")))

    def oracle(self) -> OracleVerdict:
        return OracleVerdict.model_validate(self._worker.request("oracle", timeout=WORKER_OP_TIMEOUT_SECONDS))

    def recover(self) -> float:
        """Recover the fault the harness's way, then wait for the application to be healthy."""

        started = self._monotonic()
        self._worker.request("recover", timeout=WORKER_OP_TIMEOUT_SECONDS)
        deadline = started + self._health_timeout
        health: dict[str, Any] = {}
        while True:
            health = self._worker.request("health", namespace=self._namespace, timeout=60)
            if health.get("healthy"):
                return self._monotonic() - started
            if self._monotonic() >= deadline:
                raise FastloopFaultError(
                    f"{self._namespace} is not healthy {self._health_timeout:.0f}s after recovery; "
                    f"unready: {health.get('unready')}"
                )
            self._sleep(HEALTH_POLL_SECONDS)
