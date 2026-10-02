"""Fixtures for the runner tests, and a permissive launch assurance for orchestration tests.

Runner tests exercise orchestration with stub configs (crucible, gemini) and a
patched ``subprocess.run``; they must never reach Docker, kind, npm or git, so
every runner call gets a warn-mode :class:`LaunchAssurance` over a fake host.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest
from sregym_fake_host import FakeHost

from benchmarks.sregym.runner import runner as runner_mod
from benchmarks.sregym.runner.preflight import LaunchAssurance

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def fake_host(tmp_path: Path) -> FakeHost:
    home = tmp_path / "home"
    (home / ".codex").mkdir(parents=True)
    (home / ".codex" / "auth.json").write_text("{}", encoding="utf-8")
    return FakeHost(home=home)


@pytest.fixture(autouse=True)
def _permissive_runner_assurance(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    home = tmp_path / "runner-home"
    home.mkdir(exist_ok=True)
    monkeypatch.setattr(
        runner_mod, "default_assurance", lambda env=None: LaunchAssurance(host=FakeHost(home=home), mode="warn")
    )
    monkeypatch.delenv("KUBECONFIG", raising=False)
    for name in (
        "SDO_PREFLIGHT",
        "JUDGE_REASONING_EFFORT",
        "MODEL_ID",
        *(k for k in os.environ if k.startswith("SDO_") and k.endswith("_MODEL")),
    ):
        monkeypatch.delenv(name, raising=False)
