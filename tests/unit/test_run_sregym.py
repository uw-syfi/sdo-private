from collections.abc import Callable
from pathlib import Path
from types import ModuleType

import pytest

from libs.sregym_lib import NOOP_EXP_STAGE_LIFECYCLE, ExperimentConfig, ExpStageLifecycle
from sregym_agents import run_sregym


class _ModuleWithLifecycle(ModuleType):
    def __init__(self, name: str, getter: Callable[[], object]) -> None:
        super().__init__(name)
        self.get_exp_stage_lifecycle = getter


class _Lifecycle:
    def before_stage(self, exp_dir: Path, config: ExperimentConfig) -> None:
        del exp_dir, config

    def snapshot_before_drain(self, exp_dir: Path, config: ExperimentConfig) -> object | None:
        del exp_dir, config
        return None

    def wait_for_drain(self, exp_dir: Path, baseline: object | None) -> None:
        del exp_dir, baseline


def test_load_exp_stage_lifecycle_from_agent_package(monkeypatch: pytest.MonkeyPatch) -> None:
    lifecycle: ExpStageLifecycle = _Lifecycle()
    module = _ModuleWithLifecycle("sregym_agents.fake_agent", lambda: lifecycle)

    monkeypatch.setattr(run_sregym.importlib, "import_module", lambda name: module)

    assert run_sregym._load_exp_stage_lifecycle("fake_agent") is lifecycle


def test_load_exp_stage_lifecycle_falls_back_to_noop_when_getter_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = ModuleType("sregym_agents.fake_agent")

    monkeypatch.setattr(run_sregym.importlib, "import_module", lambda name: module)

    assert run_sregym._load_exp_stage_lifecycle("fake_agent") is NOOP_EXP_STAGE_LIFECYCLE


def test_load_exp_stage_lifecycle_falls_back_to_noop_when_module_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise(name: str) -> ModuleType:
        raise ModuleNotFoundError(name=name)

    monkeypatch.setattr(run_sregym.importlib, "import_module", _raise)

    assert run_sregym._load_exp_stage_lifecycle("missing_agent") is NOOP_EXP_STAGE_LIFECYCLE


def test_load_exp_stage_lifecycle_rejects_invalid_return_type(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _ModuleWithLifecycle("sregym_agents.fake_agent", lambda: "bad")

    monkeypatch.setattr(run_sregym.importlib, "import_module", lambda name: module)

    with pytest.raises(TypeError, match="must return ExpStageLifecycle or None"):
        run_sregym._load_exp_stage_lifecycle("fake_agent")
