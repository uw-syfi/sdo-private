"""The host-side validator image is configured, never silently a stale shared default."""

from __future__ import annotations

import os
import subprocess
from typing import TYPE_CHECKING

import pytest

from sdo.operational_memory.sandbox import (
    DEFAULT_VALIDATOR_IMAGE,
    ContainerSandboxRunner,
    StaleValidatorImageError,
    require_matching_image_tags,
    validator_image_environment,
)

if TYPE_CHECKING:
    from pathlib import Path


def _runner_failing_with(stderr: str, *, image: str = "sdo-detector-validator:v0.1.0") -> ContainerSandboxRunner:
    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 1, stdout="", stderr=stderr)

    return ContainerSandboxRunner(image=image, runtime="docker", command_runner=fake_run)


def test_scoped_environment_exports_the_configured_image_and_restores_the_previous_value(monkeypatch) -> None:
    monkeypatch.delenv("SDO_VALIDATOR_IMAGE", raising=False)
    with validator_image_environment("sdo-detector-validator:private"):
        assert os.environ["SDO_VALIDATOR_IMAGE"] == "sdo-detector-validator:private"
        assert ContainerSandboxRunner().image == "sdo-detector-validator:private"
    assert "SDO_VALIDATOR_IMAGE" not in os.environ

    monkeypatch.setenv("SDO_VALIDATOR_IMAGE", "sdo-detector-validator:outer")
    with validator_image_environment("sdo-detector-validator:inner"):
        assert os.environ["SDO_VALIDATOR_IMAGE"] == "sdo-detector-validator:inner"
    assert os.environ["SDO_VALIDATOR_IMAGE"] == "sdo-detector-validator:outer"


def test_scoped_environment_without_an_image_leaves_the_environment_alone(monkeypatch) -> None:
    monkeypatch.setenv("SDO_VALIDATOR_IMAGE", "sdo-detector-validator:outer")
    with validator_image_environment(None):
        assert os.environ["SDO_VALIDATOR_IMAGE"] == "sdo-detector-validator:outer"
    monkeypatch.delenv("SDO_VALIDATOR_IMAGE")
    with validator_image_environment(None):
        assert ContainerSandboxRunner().image == DEFAULT_VALIDATOR_IMAGE


def test_scoped_environment_rejects_an_empty_image() -> None:
    with pytest.raises(ValueError, match="validator image"), validator_image_environment("  "):
        pass


def test_unknown_field_the_checkout_sdk_defines_means_a_stale_validator_image(tmp_path: Path) -> None:
    runner = _runner_failing_with('traffic workload: json: unknown field "links"')

    with pytest.raises(StaleValidatorImageError) as raised:
        runner.run(tmp_path)

    message = str(raised.value)
    assert "sdo-detector-validator:v0.1.0" in message
    assert '"links"' in message
    assert "SDO_VALIDATOR_IMAGE" in message


def test_unknown_field_the_checkout_sdk_does_not_define_is_the_authors_mistake(tmp_path: Path) -> None:
    result = _runner_failing_with('traffic workload: json: unknown field "no_such_field_anywhere"').run(tmp_path)

    assert result.returncode == 1
    assert "no_such_field_anywhere" in result.stderr


def test_a_passing_validation_is_untouched_by_the_staleness_check(tmp_path: Path) -> None:
    def ok(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    result = ContainerSandboxRunner(image="x:y", runtime="docker", command_runner=ok).run(tmp_path)

    assert result.returncode == 0


@pytest.mark.parametrize(
    ("controller", "validator"),
    [
        ("sdo-controller:mx1", "sdo-detector-validator:mx1"),
        ("registry.example/sdo-controller:v2", "registry.example/sdo-detector-validator:v2"),
        ("sdo-controller@sha256:" + "a" * 64, "sdo-detector-validator:v0.1.0"),
        ("custom-controller", "custom-validator"),
    ],
)
def test_matching_or_unjudgeable_image_tags_are_accepted(controller: str, validator: str) -> None:
    require_matching_image_tags(controller_image=controller, validator_image=validator)


def test_controller_and_validator_images_on_different_tags_fail_loudly() -> None:
    with pytest.raises(ValueError, match=r"sdo-controller:mx1.*sdo-detector-validator:v0\.1\.0"):
        require_matching_image_tags(
            controller_image="sdo-controller:mx1",
            validator_image="sdo-detector-validator:v0.1.0",
        )
