"""SDO's long-running controller as a fast-loop agent.

Each incident is one :func:`run_persistent_stage` against the application's
persistent controller: the first incident validates the lifecycle and installs
the controller; later ones reuse it. The controller reports an all-clear
baseline before the fault is injected, detects, dispatches a responder, and
independently verifies recovery. :meth:`SdoPersistentAgent.learn` then waits for
reflection and detector rollout and reads the strict receipt, so reflection is
measured separately from resolution.

There is no benchmark submission transport: the responder repairs and
verifies, and the loop grades the cluster with the problem's own oracle.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from benchmarks.sregym.adapter.persistent import (
    REJECTED_RECEIPT_FILENAME,
    STRICT_RECEIPT_FILENAME,
    Clock,
    ClusterOps,
    PersistentState,
    StageInputs,
    drain_pending_incident,
    run_persistent_stage,
)
from benchmarks.sregym.fastloop.loop import AgentOutcome, InjectionWindow
from benchmarks.sregym.fastloop.records import AgentName, TokenCounts
from sdo.controller_install import ControllerInstallError

if TYPE_CHECKING:
    from collections.abc import Callable

    from benchmarks.sregym.adapter.driver import DeployedLifecycle, DeployedLifecycleContext
    from benchmarks.sregym.adapter.runtime import RuntimeConfig
    from sdo.agent_runtime.lifecycle import LifecycleValidationCache


@dataclass(frozen=True)
class SdoAgentSettings:
    repository: Path
    namespace: str
    application: str
    runtime_config: RuntimeConfig
    state_path: Path
    results_dir: Path
    kubeconfig: str | None = None
    verification_timeout_seconds: float = 3900.0
    validation_cache: LifecycleValidationCache | None = None

    def __post_init__(self) -> None:
        if self.runtime_config.submission_api_base or self.runtime_config.submission_relay_target_base:
            raise ValueError("the fast loop has no benchmark submission transport")
        if self.verification_timeout_seconds <= 0:
            raise ValueError("verification_timeout_seconds must be positive")


def _timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _summaries(items: object, *, successful_only: bool = False) -> str:
    if not isinstance(items, list):
        return ""
    return "; ".join(
        str(item.get("summary", "")).strip()
        for item in items
        if isinstance(item, dict)
        and str(item.get("summary", "")).strip()
        and (not successful_only or item.get("success") is True)
    )


class SdoPersistentAgent:
    name: AgentName = "sdo"

    def __init__(
        self,
        settings: SdoAgentSettings,
        *,
        ops: ClusterOps,
        lifecycle_inputs: Callable[[], DeployedLifecycle],
        run_lifecycle: Callable[[DeployedLifecycleContext], bool],
        clock: Clock | None = None,
    ) -> None:
        self._settings = settings
        self._ops = ops
        self._lifecycle_inputs = lifecycle_inputs
        self._run_lifecycle = run_lifecycle
        self._clock = clock or Clock()

    @property
    def model(self) -> str:
        return self._settings.runtime_config.model

    def resolve(self, index: int, problem_id: str, inject: Callable[[], InjectionWindow]) -> AgentOutcome:
        settings = self._settings
        lifecycle = self._lifecycle_inputs()
        receipt_dir = settings.results_dir / f"{index:03d}_{problem_id}"
        windows: list[InjectionWindow] = []

        def gated_inject() -> None:
            windows.append(inject())

        resolution = run_persistent_stage(
            StageInputs(
                stage_label=f"fastloop-{index:03d}",
                application=settings.application,
                namespace=settings.namespace,
                lifecycle_fingerprint=lifecycle.fingerprint,
                runtime_config=replace(settings.runtime_config, artifacts_dir=receipt_dir),
                receipt_dir=receipt_dir,
                state_path=settings.state_path,
                kubeconfig=settings.kubeconfig,
                verification_timeout_seconds=settings.verification_timeout_seconds,
                validation_cache=settings.validation_cache,
            ),
            ops=self._ops,
            run_lifecycle=lambda: self._run_lifecycle(lifecycle.context),
            inject=gated_inject,
            clock=self._clock,
        )
        if not windows:
            raise RuntimeError("the controller verified an incident without the fault being injected")
        verified = _timestamp(resolution.get("verified_at"))
        seconds = resolution.get("incident_resolution_seconds")
        detected = (
            verified - timedelta(seconds=float(seconds))
            if verified is not None and isinstance(seconds, (int, float))
            else None
        )
        repairs = resolution.get("repair_actions")
        completions = [
            completed
            for item in (repairs if isinstance(repairs, list) else [])
            if isinstance(item, dict) and item.get("success") is True
            for completed in [_timestamp(item.get("completed_at"))]
            if completed is not None
        ]
        gate = resolution.get("fault_gate_timings_seconds") or {}
        costs = resolution.get("pre_injection_costs_seconds") or {}
        controller = resolution.get("persistent_controller") or {}
        return AgentOutcome(
            injection=windows[0],
            detected_at=detected,
            mitigation_applied_at=max(completions) if completions else None,
            resolved_at=verified,
            diagnosis=_summaries(resolution.get("confirmed_root_causes")),
            mitigation=_summaries(repairs, successful_only=True),
            baseline_gate_seconds=gate.get("controller_baseline_wait"),
            setup_seconds=float(costs.get("inventory_and_lifecycle", 0.0))
            + float(costs.get("controller_install_or_reuse", 0.0)),
            controller_installed=controller.get("installed_this_stage"),
            lifecycle_reused=resolution.get("lifecycle_reused"),
            lifecycle_validation_source=(resolution.get("lifecycle_validation") or {}).get("source"),
            previous_reflection_drain_seconds=costs.get("previous_incident_reflection_drain"),
            incident_id=str(resolution["incident_id"]),
            artifacts_dir=str(receipt_dir),
        )

    def learn(self, outcome: AgentOutcome) -> AgentOutcome:
        """Wait for reflection and detector rollout, then read the incident's strict receipt."""

        settings = self._settings
        state = PersistentState.load(settings.state_path)
        record = state.controllers.get(settings.namespace)
        if record is None or record.pending is None or record.pending.incident_id != outcome.incident_id:
            raise RuntimeError(f"incident {outcome.incident_id!r} is not pending on the persistent controller")
        started = self._clock.monotonic()
        error: str | None = None
        try:
            drain_pending_incident(
                record,
                ops=self._ops,
                repository=settings.repository,
                drained_by=f"fastloop-learn-{outcome.incident_id}",
                clock=self._clock,
            )
        except ControllerInstallError as exc:
            error = f"strict receipt rejected: {exc}"
        finally:
            # A failed drain must not block the next incident; its evidence stays on disk.
            record.pending = None
            state.save(settings.state_path)
        receipt = self._receipt(Path(str(outcome.artifacts_dir)))
        memory = receipt.get("memory_reuse")
        memory = memory if isinstance(memory, dict) else {}
        attempts = receipt.get("reflection_attempts")
        skipped = receipt.get("reflection_skipped_reason")
        return outcome.with_learning(
            reflection_seconds=self._clock.monotonic() - started,
            responder_tokens=TokenCounts.from_usage(receipt.get("usage")),
            reflection_tokens=TokenCounts.from_usage(receipt.get("reflection_usage")),
            warm_path=bool(memory["warm_path"]) if "warm_path" in memory else None,
            match_reasons=tuple(str(reason) for reason in memory.get("match_reasons", []) or []),
            reflection_attempts=attempts if isinstance(attempts, int) else None,
            reflection_skipped_reason=skipped if isinstance(skipped, str) else None,
            error=error,
        )

    @staticmethod
    def _receipt(receipt_dir: Path) -> dict[str, Any]:
        strict = receipt_dir / STRICT_RECEIPT_FILENAME
        if strict.is_file():
            document = json.loads(strict.read_text(encoding="utf-8"))
            return document if isinstance(document, dict) else {}
        rejected = receipt_dir / REJECTED_RECEIPT_FILENAME
        if rejected.is_file():
            document = json.loads(rejected.read_text(encoding="utf-8")).get("receipt")
            return document if isinstance(document, dict) else {}
        return {}

    def close(self) -> None:
        """The controller keeps running for the next loop; ``fastloop down`` stops it."""
