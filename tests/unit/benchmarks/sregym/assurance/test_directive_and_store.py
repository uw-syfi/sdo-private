from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

import pytest

from benchmarks.sregym.assurance.scenarios import SCENARIOS
from benchmarks.sregym.assurance.scripted_codex.directive import Binding, Directive, DirectiveError
from benchmarks.sregym.assurance.scripted_codex.store import DirectoryStore
from benchmarks.sregym.assurance.scripted_codex.usage import KIND_OFFSETS, request_usage

if TYPE_CHECKING:
    from pathlib import Path


def test_directive_round_trips_and_rejects_unknown_modes() -> None:
    directive = Directive(
        scenario="s", fault="missing_configmap", target="mongodb-geo", mitigation="wrong_then_correct"
    )
    assert Directive.from_json(directive.to_json()) == directive
    with pytest.raises(DirectiveError, match="mitigation"):
        Directive(scenario="s", fault="missing_configmap", target="x", mitigation="guess")
    with pytest.raises(DirectiveError, match="unknown fields"):
        Directive.from_json('{"scenario": "s", "fault": "missing_configmap", "target": "x", "typo": 1}')


def test_a_binding_is_never_replaced_by_a_later_directive(tmp_path: Path) -> None:
    store = DirectoryStore(tmp_path)
    first = Directive(scenario="first", fault="network_policy_block", target="recommendation")
    later = Directive(scenario="later", fault="missing_configmap", target="mongodb-geo")
    now = datetime.now(timezone.utc).isoformat()

    store.bind(Binding(incident_id="inc-1", directive=first, bound_at=now))
    kept = store.bind(Binding(incident_id="inc-1", directive=later, bound_at=now))

    assert kept.directive == first
    assert store.binding("inc-1") is not None


def test_a_one_shot_marker_is_claimed_once(tmp_path: Path) -> None:
    store = DirectoryStore(tmp_path)
    assert store.claim("inc-1:crash") is True
    assert store.claim("inc-1:crash") is False


def test_synthetic_usage_is_deterministic_and_distinct_per_turn_kind() -> None:
    assert request_usage(4, "responder", 2) == request_usage(4, "responder", 2)
    kinds = {request_usage(4, kind, 0).input_tokens for kind in KIND_OFFSETS}
    assert len(kinds) == len(KIND_OFFSETS)
    usage = request_usage(4, "reflection-resume", 3)
    assert usage.cached_input_tokens <= usage.input_tokens
    assert usage.reasoning_output_tokens <= usage.output_tokens


def test_every_scenario_incident_names_a_known_fault_and_target() -> None:
    for specs in SCENARIOS.values():
        for spec in specs:
            assert spec.directive.fault in {"network_policy_block", "missing_configmap"}
            assert spec.directive.target
