from __future__ import annotations

from benchmarks.sregym.analysis.composite_sequence import first_activations, learned_before_dispatch


def _firing(detector_class: str, fingerprint: str, relation: str, event: str = "activated") -> dict[str, object]:
    return {
        "event": event,
        "detector_class": detector_class,
        "fingerprint": fingerprint,
        "dispatch_relation": relation,
    }


def test_learned_detector_before_dispatch_is_distinguished_from_health() -> None:
    firings = [
        _firing("health", "health-objective/configmap-missing/hotel-reservation/mongodb-geo", "before_dispatch"),
        _firing("incident", "required-configmap/hotel-reservation/mongodb-geo", "before_dispatch"),
        _firing(
            "health", "health-objective/network-policy/hotel-reservation/deny-all-recommendation", "after_dispatch"
        ),
    ]
    assert learned_before_dispatch(firings, "mongodb-geo")
    assert not learned_before_dispatch(firings, "recommendation")
    assert set(first_activations(firings, "recommendation")) == {"health"}


def test_cleared_events_do_not_count_as_activation() -> None:
    firings = [_firing("incident", "x/geo", "before_dispatch", event="cleared")]
    assert first_activations(firings, "geo") == {}
