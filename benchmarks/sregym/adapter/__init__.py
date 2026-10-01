"""SDO lifecycle adapter for Codex-backed SREGym incidents.

The adapter's modules also run as entry points (``python -m
benchmarks.sregym.adapter.persistent``, and ``...adapter.submission`` inside the
responder image), so the facade loads a module only when a caller asks for one
of its names.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from benchmarks.sregym.adapter.driver import (
        DeployedLifecycle,
        DeployedLifecycleContext,
        deployed_lifecycle,
        run_or_reuse_lifecycle,
    )
    from benchmarks.sregym.adapter.persistent import (
        REJECTED_RECEIPT_FILENAME,
        STRICT_RECEIPT_FILENAME,
        Clock,
        ClusterOps,
        KubectlClusterOps,
        PersistentState,
        StageInputs,
        control_namespace_for,
        drain_pending_incident,
        run_persistent_stage,
        teardown,
    )
    from benchmarks.sregym.adapter.runtime import RuntimeConfig

_EXPORTS: dict[str, str] = {
    "DeployedLifecycle": "driver",
    "DeployedLifecycleContext": "driver",
    "deployed_lifecycle": "driver",
    "run_or_reuse_lifecycle": "driver",
    "REJECTED_RECEIPT_FILENAME": "persistent",
    "STRICT_RECEIPT_FILENAME": "persistent",
    "Clock": "persistent",
    "ClusterOps": "persistent",
    "KubectlClusterOps": "persistent",
    "PersistentState": "persistent",
    "StageInputs": "persistent",
    "control_namespace_for": "persistent",
    "collect_followup_incident": "persistent",
    "drain_pending_incident": "persistent",
    "pause_controller": "persistent",
    "run_persistent_stage": "persistent",
    "teardown": "persistent",
    "RuntimeConfig": "runtime",
    "HealthyBaselineCaptureOps": "healthy_baseline",
    "kubectl_capture_ops": "healthy_baseline",
}

__all__ = [
    "REJECTED_RECEIPT_FILENAME",
    "STRICT_RECEIPT_FILENAME",
    "Clock",
    "ClusterOps",
    "DeployedLifecycle",
    "DeployedLifecycleContext",
    "KubectlClusterOps",
    "PersistentState",
    "RuntimeConfig",
    "StageInputs",
    "HealthyBaselineCaptureOps",
    "kubectl_capture_ops",
    "control_namespace_for",
    "deployed_lifecycle",
    "collect_followup_incident",
    "drain_pending_incident",
    "pause_controller",
    "run_or_reuse_lifecycle",
    "run_persistent_stage",
    "teardown",
]


def __getattr__(name: str) -> Any:
    """Import the owning adapter module on first use of one of its public names."""

    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(importlib.import_module(f"{__name__}.{module}"), name)
    globals()[name] = value
    return value
