"""The hotel-reservation fault catalog the no-LLM assurance suite injects.

Each case names a SREGym problem (injected and recovered by the fast-loop
fault driver, exactly as the benchmark does), the application objects its
fault changes (which the healthy-state diff must name), and a scripted wrong
fix: a plausible repair that leaves the fault in place, so the incident
status and the submission gate must keep refusing it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

#: SREGym's red herrings: they exist, mounted, before any fault is injected.
DECOYS: tuple[str, ...] = ("failure-admin-geo", "failure-admin-rate")

WrongFixKind = Literal["restart", "decoy-regrant", "revert-drift"]


@dataclass(frozen=True)
class WrongFix:
    """A scripted repair that does not touch the fault.

    ``restart`` rolls out ``target`` (a Deployment) again; ``decoy-regrant``
    runs SREGym's own Mongo privilege-restore scripts from the decoy
    ConfigMaps, the story the decoys tell; ``revert-drift`` removes the
    environment variable ``env`` from Deployment ``target``, undoing a benign
    decoy drift (the obvious recent change) and nothing else.
    """

    kind: WrongFixKind
    target: str = ""
    env: str = ""

    def __post_init__(self) -> None:
        if self.kind not in ("restart", "decoy-regrant", "revert-drift"):
            raise ValueError(f"unsupported wrong fix {self.kind!r}")
        if self.kind in ("restart", "revert-drift") and not self.target:
            raise ValueError(f"a {self.kind} wrong fix names its Deployment")
        if (self.kind == "revert-drift") != bool(self.env):
            raise ValueError("only a revert-drift wrong fix names an environment variable, and it must")

    @property
    def label(self) -> str:
        if self.kind == "restart":
            return f"restart deployment/{self.target}"
        if self.kind == "revert-drift":
            return f"revert the recent {self.env} change on deployment/{self.target}"
        return "re-grant Mongo roles (decoy scripts)"


@dataclass(frozen=True)
class FaultCase:
    """One SREGym problem, what it changes, and how to get it wrong."""

    name: str
    problem_id: str
    #: Application objects the fault changes, as ``Kind/name``; the incident diff must name each.
    faulted_objects: tuple[str, ...]
    wrong_fixes: tuple[WrongFix, ...]
    #: The service the fault breaks, for the report.
    service: str
    #: A detection-latency gap already known and tracked elsewhere; reported, not failed.
    known_gap: str = ""

    def __post_init__(self) -> None:
        if not self.name or not self.problem_id or not self.service:
            raise ValueError("a fault case needs a name, a problem id, and a service")
        if not self.faulted_objects or any("/" not in item for item in self.faulted_objects):
            raise ValueError(f"{self.name}: faulted objects are Kind/name, got {self.faulted_objects}")
        if not self.wrong_fixes:
            raise ValueError(f"{self.name}: at least one wrong fix is required")


@dataclass(frozen=True)
class CompositeCase:
    """Several fault cases live at once; the diff must name every faulted object.

    A suite composite injects its faults through the fault driver's composition. A registry
    composite (``registry_id``, SREGym's ``CompositeFaultProblem``) is one problem whose fault
    components are ``faults`` in order, and it may add benign decoy changes (``decoy_objects``),
    which the diff may name but which are no cause.
    """

    name: str
    faults: tuple[FaultCase, ...]
    source: str = "suite"
    wrong_fixes: tuple[WrongFix, ...] = field(default=())
    #: A SREGym registry composite (``composite_specs.json``), injected as one problem; its fault
    #: components are ``faults``, in order. Empty: the worker composes ``faults`` itself.
    registry_id: str = ""
    decoy_objects: tuple[str, ...] = field(default=())

    def __post_init__(self) -> None:
        if len(self.faults) + len(self.decoy_objects) < 2 or not self.faults:
            raise ValueError(f"{self.name}: a composite fault has at least two faults, or a fault and a decoy")
        if self.decoy_objects and not self.registry_id:
            raise ValueError(f"{self.name}: only a registry composite injects decoys")
        if self.registry_id and not self.registry_id.startswith("composite_"):
            raise ValueError(f"{self.name}: a registry composite id starts with composite_, got {self.registry_id}")
        problems = [fault.problem_id for fault in self.faults]
        if len(set(problems)) != len(problems):
            raise ValueError(f"{self.name}: a problem appears twice: {problems}")
        objects = [item for fault in self.faults for item in fault.faulted_objects]
        if len(set(objects)) != len(objects):
            # SREGym backs a fault's object up to one scratch file per service, so two faults on
            # one object would overwrite each other's backup and recovery could not restore it.
            raise ValueError(f"{self.name}: two faults change the same object: {objects}")

    @property
    def faulted_objects(self) -> tuple[str, ...]:
        return tuple(item for fault in self.faults for item in fault.faulted_objects)


WRONG_SELECTOR = FaultCase(
    name="selector-mismatch",
    problem_id="wrong_service_selector_hotel_reservation",
    faulted_objects=("Service/frontend",),
    wrong_fixes=(WrongFix("decoy-regrant"), WrongFix("restart", "frontend")),
    service="frontend",
)
MISSING_CONFIGMAP = FaultCase(
    name="missing-configmap",
    problem_id="missing_configmap_hotel_reservation",
    faulted_objects=("ConfigMap/mongo-geo-script",),
    wrong_fixes=(WrongFix("decoy-regrant"), WrongFix("restart", "geo")),
    service="mongodb-geo",
)
MISSING_CONFIGMAP_RATE = FaultCase(
    name="missing-configmap-rate",
    problem_id="missing_configmap_mongodb_rate_hotel_reservation",
    faulted_objects=("ConfigMap/mongo-rate-script",),
    wrong_fixes=(WrongFix("decoy-regrant"), WrongFix("restart", "rate")),
    service="mongodb-rate",
)
NETWORK_POLICY_BLOCK = FaultCase(
    name="network-policy-block",
    problem_id="network_policy_block",
    faulted_objects=("NetworkPolicy/deny-all-recommendation",),
    wrong_fixes=(WrongFix("decoy-regrant"), WrongFix("restart", "recommendation")),
    service="recommendation",
    known_gap="the prober detected network_policy_block late (+20.4 s) in 1 of 4 live runs (feedback-loop e2e)",
)
READINESS_PROBE = FaultCase(
    name="readiness-probe",
    problem_id="readiness_probe_misconfiguration_hotel_reservation",
    faulted_objects=("Deployment/frontend",),
    wrong_fixes=(WrongFix("decoy-regrant"), WrongFix("restart", "frontend")),
    service="frontend",
)
MISSING_SERVICE = FaultCase(
    name="missing-service",
    problem_id="missing_service_hotel_reservation",
    faulted_objects=("Service/mongodb-rate",),
    wrong_fixes=(WrongFix("decoy-regrant"), WrongFix("restart", "mongodb-rate")),
    service="mongodb-rate",
)

SINGLE_FAULTS: tuple[FaultCase, ...] = (
    WRONG_SELECTOR,
    MISSING_CONFIGMAP,
    NETWORK_POLICY_BLOCK,
    READINESS_PROBE,
    MISSING_SERVICE,
    MISSING_CONFIGMAP_RATE,
)

#: Obvious pairs first: two services, and a NetworkPolicy block plus a selector fault. Then the
#: assurance plan's K1-K3 (``benchmarks/sregym/experiments/assurance/PLAN.md`` (c)), as SREGym
#: registry composites.
COMPOSITES: tuple[CompositeCase, ...] = (
    CompositeCase(name="policy-block+selector", faults=(NETWORK_POLICY_BLOCK, WRONG_SELECTOR)),
    CompositeCase(name="configmap-geo+selector", faults=(MISSING_CONFIGMAP, WRONG_SELECTOR)),
    CompositeCase(name="configmap-geo+configmap-rate", faults=(MISSING_CONFIGMAP, MISSING_CONFIGMAP_RATE)),
    CompositeCase(
        name="K1",
        faults=(NETWORK_POLICY_BLOCK, MISSING_CONFIGMAP_RATE),
        source="PLAN.md K1",
        registry_id="composite_policy_and_rate_configmap_hotel_reservation",
        wrong_fixes=(WrongFix("decoy-regrant"),),
    ),
    CompositeCase(
        name="K2",
        faults=(WRONG_SELECTOR, READINESS_PROBE),
        source="PLAN.md K2",
        registry_id="composite_frontend_selector_and_readiness_hotel_reservation",
        wrong_fixes=(WrongFix("restart", "frontend"),),
    ),
    CompositeCase(
        name="K3",
        faults=(MISSING_CONFIGMAP,),
        source="PLAN.md K3",
        registry_id="composite_geo_configmap_with_log_drift_hotel_reservation",
        decoy_objects=("Deployment/geo",),
        wrong_fixes=(WrongFix("revert-drift", "geo", env="LOG_LEVEL"), WrongFix("decoy-regrant")),
    ),
)


def single_fault(name: str) -> FaultCase:
    for case in SINGLE_FAULTS:
        if name in (case.name, case.problem_id):
            return case
    raise KeyError(f"unknown fault {name!r}; known: {', '.join(case.name for case in SINGLE_FAULTS)}")


def composite(name: str) -> CompositeCase:
    for case in COMPOSITES:
        if case.name == name:
            return case
    raise KeyError(f"unknown composite {name!r}; known: {', '.join(case.name for case in COMPOSITES)}")
