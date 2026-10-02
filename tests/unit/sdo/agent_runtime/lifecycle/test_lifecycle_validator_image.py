"""The lifecycle validates with the validator image it is configured with."""

from __future__ import annotations

import os
import subprocess
from typing import TYPE_CHECKING

import pytest

from sdo.agent_runtime.lifecycle import operational_memory as lifecycle
from sdo.operational_memory.sandbox import ContainerSandboxRunner

if TYPE_CHECKING:
    from pathlib import Path


class _RecordingRunner:
    def __init__(self) -> None:
        self.calls: list[Path] = []

    def run(self, app_root: Path) -> object:
        self.calls.append(app_root)
        raise AssertionError("not reached")


def _git_repository(path: Path) -> Path:
    path.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    (path / "README.md").write_text("app\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", "init"],
        cwd=path,
        check=True,
    )
    return path


def test_selected_validator_uses_the_configured_image_when_the_environment_is_unset(monkeypatch) -> None:
    monkeypatch.delenv("SDO_VALIDATOR_IMAGE", raising=False)

    selected = lifecycle._select_validator(None, "sdo-detector-validator:mx1")

    assert isinstance(selected, ContainerSandboxRunner)
    assert selected.image == "sdo-detector-validator:mx1"


def test_an_injected_validator_wins_over_the_configured_image() -> None:
    injected = _RecordingRunner()

    assert lifecycle._select_validator(injected, "sdo-detector-validator:mx1") is injected


def test_run_initial_lifecycle_exports_the_image_to_agent_sessions_and_builds_a_matching_runner(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("SDO_VALIDATOR_IMAGE", raising=False)
    repository = _git_repository(tmp_path / "app")
    seen: dict[str, object] = {}

    def fake_inner(_root: Path, *, validator: object, **_kwargs: object) -> str:
        seen["validator"] = validator
        seen["environment"] = os.environ.get("SDO_VALIDATOR_IMAGE")
        return "done"

    monkeypatch.setattr(lifecycle, "_run_initial_lifecycle", fake_inner)

    lifecycle.run_initial_lifecycle(
        repository,
        application="app",
        health_objective="up",
        backend=object(),  # type: ignore[arg-type]
        validator_image="sdo-detector-validator:mx1",
    )

    assert isinstance(seen["validator"], ContainerSandboxRunner)
    assert seen["validator"].image == "sdo-detector-validator:mx1"  # type: ignore[attr-defined]
    # The judge's `sdo detector check` runs in a child of the agent CLI and reads this variable.
    assert seen["environment"] == "sdo-detector-validator:mx1"
    assert "SDO_VALIDATOR_IMAGE" not in os.environ


def test_run_initial_lifecycle_keeps_an_injected_validator_and_the_environment(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SDO_VALIDATOR_IMAGE", "sdo-detector-validator:outer")
    repository = _git_repository(tmp_path / "app")
    injected = _RecordingRunner()
    seen: dict[str, object] = {}

    def fake_inner(_root: Path, *, validator: object, **_kwargs: object) -> str:
        seen["validator"] = validator
        return "done"

    monkeypatch.setattr(lifecycle, "_run_initial_lifecycle", fake_inner)

    lifecycle.run_initial_lifecycle(
        repository,
        application="app",
        health_objective="up",
        backend=object(),  # type: ignore[arg-type]
        validator=injected,
    )

    assert seen["validator"] is injected
    assert os.environ["SDO_VALIDATOR_IMAGE"] == "sdo-detector-validator:outer"


@pytest.mark.parametrize("image", ["", "   "])
def test_run_initial_lifecycle_rejects_an_empty_configured_image(tmp_path: Path, image: str) -> None:
    with pytest.raises(ValueError, match="validator image"):
        lifecycle.run_initial_lifecycle(
            _git_repository(tmp_path / "app"),
            application="app",
            health_objective="up",
            backend=object(),  # type: ignore[arg-type]
            validator_image=image,
        )
