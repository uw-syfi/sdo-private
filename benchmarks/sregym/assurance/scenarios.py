"""Named scripted scenarios: incident sequences with their directives, chaos and expected outcomes."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal

from benchmarks.sregym.assurance.scripted_codex.directive import Directive

NP = "network_policy_block"
MCM_GEO = "missing_configmap_hotel_reservation"
MCM_RATE = "missing_configmap_mongodb_rate_hotel_reservation"

NP_DETECTOR = "deny-all-networkpolicy-isolation"
MCM_DETECTOR = "required-configmap-missing"
CLAIMED_DETECTOR = "wedged-workload-restart"

#: What the harness expects SDO to do with one incident.
Resolution = Literal["mitigated", "not-mitigated", "loud-failure"]


@dataclass(frozen=True)
class Expectation:
    resolution: Resolution = "mitigated"
    #: True: the warm path must fire; False: it must not; None: either.
    warm: bool | None = None
    #: Incident detectors that must be registered in memory after the incident.
    learned: tuple[str, ...] = ()
    #: Incident detectors that must NOT be registered after the incident.
    not_learned: tuple[str, ...] = ()
    #: The broker skipped the reflection model turn deterministically.
    reflection_skipped: bool | None = None
    #: Minimum reflection attempts recorded by the broker.
    min_reflection_attempts: int | None = None


@dataclass(frozen=True)
class IncidentSpec:
    problem_id: str
    directive: Directive
    expect: Expectation = Expectation()
    #: Chaos action to run during the incident (see ``benchmarks.sregym.assurance.chaos``).
    chaos: str | None = None

    def __post_init__(self) -> None:
        if not self.problem_id:
            raise ValueError("problem_id must not be empty")


_TARGETS = {NP: ("network_policy_block", "recommendation"), MCM_GEO: ("missing_configmap", "mongodb-geo")}
_TARGETS[MCM_RATE] = ("missing_configmap", "mongodb-rate")


def incident(
    problem_id: str,
    scenario: str,
    seed: int,
    *,
    expect: Expectation | None = None,
    chaos: str | None = None,
    **directive: object,
) -> IncidentSpec:
    fault, target = _TARGETS[problem_id]
    return IncidentSpec(
        problem_id=problem_id,
        directive=Directive(scenario=scenario, fault=fault, target=target, usage_seed=seed, **directive),  # type: ignore[arg-type]
        expect=expect or Expectation(),
        chaos=chaos,
    )


def _learned(detector: str) -> Expectation:
    return Expectation(warm=False, learned=(detector,), reflection_skipped=False)


def _warm(detector: str) -> Expectation:
    return Expectation(warm=True, learned=(detector,), reflection_skipped=True)


SCENARIOS: dict[str, tuple[IncidentSpec, ...]] = {
    # First encounter learns a detector and playbook; the exact repeat takes the warm path.
    "first-repeat": (
        incident(NP, "first-repeat/first", 1, expect=_learned(NP_DETECTOR)),
        incident(NP, "first-repeat/repeat", 2, expect=_warm(NP_DETECTOR)),
    ),
    "wrong-then-correct": (
        incident(MCM_GEO, "wrong-then-correct", 3, mitigation="wrong_then_correct", expect=_learned(MCM_DETECTOR)),
    ),
    # A wrong repair that the responder admits, then one it believes fixed the incident.
    "wrong-only": (
        incident(
            NP,
            "wrong-only/honest",
            4,
            mitigation="wrong_only_honest",
            expect=Expectation(resolution="not-mitigated", not_learned=(CLAIMED_DETECTOR, NP_DETECTOR)),
        ),
        incident(
            NP,
            "wrong-only/claimed",
            5,
            mitigation="wrong_only_claimed",
            expect=Expectation(resolution="not-mitigated", not_learned=(CLAIMED_DETECTOR, NP_DETECTOR)),
        ),
    ),
    "sequence": (
        incident(NP, "sequence/np", 11, expect=_learned(NP_DETECTOR)),
        incident(MCM_GEO, "sequence/geo", 12, expect=_learned(MCM_DETECTOR)),
        incident(NP, "sequence/np-repeat", 13, expect=_warm(NP_DETECTOR)),
        incident(MCM_RATE, "sequence/rate-variant", 14, expect=Expectation(learned=(MCM_DETECTOR,))),
        incident(MCM_GEO, "sequence/geo-repeat", 15, expect=_warm(MCM_DETECTOR)),
    ),
}


def with_expectation(spec: IncidentSpec, **changes: object) -> IncidentSpec:
    return replace(spec, expect=replace(spec.expect, **changes))  # type: ignore[arg-type]
