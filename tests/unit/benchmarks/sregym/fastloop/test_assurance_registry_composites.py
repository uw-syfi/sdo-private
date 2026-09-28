"""The no-LLM suite runs the assurance program's registry composites (K1, K2) as SREGym problems."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
import tomllib

from benchmarks.sregym.fastloop.assurance.catalog import (
    MISSING_CONFIGMAP,
    WRONG_SELECTOR,
    CompositeCase,
    composite,
)
from benchmarks.sregym.fastloop.assurance.suite import AssuranceSuite
from benchmarks.sregym.fastloop.loop import InjectionWindow

CATALOG = (
    Path(__file__).resolve().parents[5] / "benchmarks" / "sregym" / "experiments" / "assurance" / "composites.toml"
)


def _assurance_composites() -> dict[str, dict]:
    raw = tomllib.loads(CATALOG.read_text(encoding="utf-8"))
    return {entry["id"]: entry for entry in raw["composite"]}


@pytest.mark.parametrize("identifier", ["K1", "K2"])
def test_each_phase_one_composite_is_a_suite_case_with_the_catalogs_faults_in_order(identifier: str) -> None:
    entry = _assurance_composites()[identifier]

    case = composite(identifier)

    assert case.registry_id == entry["problem_id"]
    assert [fault.problem_id for fault in case.faults] == [
        component["problem"] for component in entry["components"] if component["role"] == "fault"
    ]
    required = {item["object"] for item in entry["expected_diff"] if item["required"]}
    assert set(case.faulted_objects) == required


def test_a_registry_composite_id_names_a_composite_problem() -> None:
    with pytest.raises(ValueError, match="composite_"):
        CompositeCase(name="bad", faults=(MISSING_CONFIGMAP, WRONG_SELECTOR), registry_id="policy_and_rate")


class _Driver:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def _window(self) -> InjectionWindow:
        now = datetime.now(tz=timezone.utc)
        return InjectionWindow(started_at=now, finished_at=now)

    def inject(self, problem_id: str) -> InjectionWindow:
        self.calls.append(("inject", problem_id))
        return self._window()

    def inject_composite(self, problem_ids: list[str]):
        self.calls.append(("inject_composite", tuple(problem_ids)))
        raise AssertionError("a registry composite is injected as one problem")


def test_a_registry_composite_is_injected_as_one_problem() -> None:
    suite = AssuranceSuite.__new__(AssuranceSuite)
    suite.driver = _Driver()
    case = composite("K2")

    suite.inject_case(case)

    assert suite.driver.calls == [("inject", case.registry_id)]
