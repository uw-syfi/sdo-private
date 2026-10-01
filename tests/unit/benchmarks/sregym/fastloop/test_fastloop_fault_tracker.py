from __future__ import annotations

from typing import Any

import pytest

from benchmarks.sregym.fastloop.fault_tracker import (
    COMPOSITE_FAULTS,
    FaultTracker,
    composite_faults,
    probes_for,
)


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _tracker(states: dict[str, list[bool]], clock: _Clock, stable: float = 20.0) -> FaultTracker:
    iterators = {name: iter(values) for name, values in states.items()}
    probes = {name: (lambda it=it: next(it)) for name, it in iterators.items()}
    return FaultTracker(probes, stable_seconds=stable, clock=clock)


def _run(tracker: FaultTracker, clock: _Clock, steps: int, interval: float = 10.0) -> None:
    for _ in range(steps):
        tracker.poll()
        clock.now += interval


def test_resolution_is_the_start_of_the_final_unbroken_green_run() -> None:
    clock = _Clock()
    tracker = _tracker(
        {"a": [False, True, True, True, True], "b": [False, False, False, True, True]}, clock, stable=10.0
    )
    _run(tracker, clock, 5)
    times = tracker.resolution_times()
    assert times == {"a": 10.0, "b": 30.0}
    assert tracker.all_resolved_at() == 30.0


def test_a_flap_after_green_invalidates_the_earlier_resolution() -> None:
    clock = _Clock()
    tracker = _tracker({"a": [True, True, True, False, True, True, True]}, clock)
    _run(tracker, clock, 7)
    assert tracker.resolution_times() == {"a": 40.0}
    assert tracker.first_green() == {"a": 0.0}


def test_green_shorter_than_the_stability_window_is_not_resolved() -> None:
    clock = _Clock()
    tracker = _tracker({"a": [False, True]}, clock, stable=20.0)
    _run(tracker, clock, 2)
    assert tracker.resolution_times() == {"a": None}
    assert tracker.all_resolved_at() is None
    assert tracker.all_green_now() is True


def test_unresolved_fault_blocks_all_resolved() -> None:
    clock = _Clock()
    tracker = _tracker({"a": [True, True, True], "b": [False, False, False]}, clock)
    _run(tracker, clock, 3)
    assert tracker.resolution_times() == {"a": 0.0, "b": None}
    assert tracker.all_resolved_at() is None
    assert tracker.all_green_now() is False


def test_probe_errors_count_as_unresolved_and_are_recorded() -> None:
    clock = _Clock()

    def broken() -> bool:
        raise RuntimeError("api down")

    tracker = FaultTracker({"a": broken}, stable_seconds=0.0, clock=clock)
    tracker.poll()
    assert tracker.samples[-1].states == {"a": False}
    assert "api down" in tracker.samples[-1].errors["a"]


def test_registered_composites_declare_faults_in_injection_order() -> None:
    assert [fault.kind for fault in composite_faults("composite3_hotel_geo_rate_recommendation")] == [
        "readiness",
        "configmap",
        "network_policy",
    ]
    five = composite_faults("composite5_hotel_geo_rate_recommendation_frontend_user")
    assert [(fault.kind, fault.component) for fault in five] == [
        ("readiness", "geo"),
        ("configmap", "mongodb-rate"),
        ("network_policy", "recommendation"),
        ("wrong_selector", "frontend"),
        ("resource_request", "user"),
    ]
    assert len({fault.component for fault in five}) == 5
    assert composite_faults("missing_configmap_hotel_reservation") == ()
    assert set(COMPOSITE_FAULTS) == {
        "composite3_hotel_geo_rate_recommendation",
        "composite5_hotel_geo_rate_recommendation_frontend_user",
    }


def _deployment(ready: int, replicas: int = 1, *, updated: int | None = None) -> dict[str, Any]:
    return {
        "metadata": {"generation": 2},
        "spec": {"replicas": replicas},
        "status": {
            "observedGeneration": 2,
            "readyReplicas": ready,
            "updatedReplicas": replicas if updated is None else updated,
            "replicas": replicas,
        },
    }


def _reader(objects: dict[tuple[str, str], dict[str, Any] | None]):
    return lambda kind, name: objects.get((kind, name))


def test_probes_report_per_fault_state_from_cluster_objects() -> None:
    faults = composite_faults("composite5_hotel_geo_rate_recommendation_frontend_user")
    blocking = {
        "spec": {"podSelector": {"matchLabels": {"io.kompose.service": "recommendation"}}, "ingress": [], "egress": []}
    }
    cluster = {
        ("deployment", "geo"): _deployment(0),
        ("deployment", "mongodb-rate"): _deployment(1),
        ("networkpolicy", "deny-all-recommendation"): blocking,
        ("endpoints", "frontend"): {"subsets": None},
        ("deployment", "user"): _deployment(1, updated=0),
        ("deployment", "recommendation"): _deployment(1),
    }
    probes = probes_for(faults, _reader(cluster))
    assert {name: probe() for name, probe in probes.items()} == {
        "readiness:geo": False,
        "configmap:mongodb-rate": True,
        "network_policy:recommendation": False,
        "wrong_selector:frontend": False,
        "resource_request:user": False,
    }
    cluster[("deployment", "geo")] = _deployment(1)
    cluster[("networkpolicy", "deny-all-recommendation")] = None
    cluster[("endpoints", "frontend")] = {"subsets": [{"addresses": [{"ip": "10.0.0.1"}]}]}
    cluster[("deployment", "user")] = _deployment(1)
    assert all(probe() for probe in probes.values())


def test_a_network_policy_that_allows_traffic_no_longer_blocks() -> None:
    faults = composite_faults("composite3_hotel_geo_rate_recommendation")
    relaxed = {"spec": {"podSelector": {"matchLabels": {}}, "ingress": [{}], "egress": [{}]}}
    cluster: dict[tuple[str, str], dict[str, Any] | None] = {
        ("networkpolicy", "deny-all-recommendation"): relaxed,
        ("deployment", "recommendation"): _deployment(1),
    }
    probe = probes_for(faults, _reader(cluster))["network_policy:recommendation"]
    assert probe() is True


def test_unreadable_deployment_is_unresolved() -> None:
    faults = composite_faults("composite3_hotel_geo_rate_recommendation")
    probe = probes_for(faults, _reader({}))["readiness:geo"]
    assert probe() is False


def test_unknown_fault_kind_is_rejected() -> None:
    from benchmarks.sregym.fastloop.fault_tracker import FaultSpec

    with pytest.raises(ValueError, match="no probe"):
        probes_for((FaultSpec("mystery", "x"),), _reader({}))
