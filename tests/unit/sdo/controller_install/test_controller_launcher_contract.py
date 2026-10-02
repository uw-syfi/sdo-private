"""The installer and the controller launcher are separate modules; this pins that they agree.

A launcher that does not define a flag the installer passes makes every controller pod exit with
"unrecognized arguments", which a cluster run only discovers after cluster up, image load and a cold lifecycle.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from controller.builder.check_cli import _build_parser
from sdo.controller_install.kubernetes import ControllerInstallConfig, controller_resources

_FEATURES: dict[str, dict[str, object]] = {
    "defaults": {},
    "late-findings": {"late_findings": "pull"},
    "follow-ups": {"max_follow_ups": 3, "follow_up_cooldown_seconds": 30},
    "closeout-gate": {"closeout_state_gate": True},
    "healthy-baseline": {"healthy_baseline": True},
    "split-namespaces": {"controller_namespace": "sdo-control"},
    "recorded-actions": {"repair_policy": "recorded-actions"},
    "generalizing-reflection": {"reflection_guidance": "generalize", "reflection_session": "fresh"},
    "claude-provider": {"agent_provider": "claude"},
}


def _config(**overrides: object) -> ControllerInstallConfig:
    values: dict[str, object] = {
        "repository": Path("/tmp/application"),
        "namespace": "demo",
        "application": "demo",
        "controller_image": "sdo-controller:t1",
        "responder_image": "responder:t1",
        "validator_image": "sdo-detector-validator:t1",
        "repository_pvc": "repository",
        "credentials_secret": "credentials",
        "model": "gpt-6-luna",
        "timeout_seconds": 60,
    }
    values.update(overrides)
    return ControllerInstallConfig(**values)  # type: ignore[arg-type]


def _controller_arguments(config: ControllerInstallConfig) -> list[str]:
    job = next(resource for resource in controller_resources(config) if resource["kind"] == "Job")
    return list(job["spec"]["template"]["spec"]["containers"][0]["args"])


@pytest.mark.parametrize("name", sorted(_FEATURES))
def test_every_argument_the_installer_passes_for_a_feature_is_accepted_by_the_launcher(
    name: str, capsys: pytest.CaptureFixture[str]
) -> None:
    arguments = _controller_arguments(_config(**_FEATURES[name]))

    try:
        _build_parser().parse_args(arguments)
    except SystemExit as exit_request:
        pytest.fail(f"launcher rejected the installer's arguments for {name!r}: {capsys.readouterr().err.strip()}")
        raise AssertionError from exit_request


def test_every_feature_together_is_accepted_by_the_launcher(capsys: pytest.CaptureFixture[str]) -> None:
    everything: dict[str, object] = {}
    for overrides in _FEATURES.values():
        everything.update(overrides)
    arguments = _controller_arguments(_config(**everything))

    try:
        _build_parser().parse_args(arguments)
    except SystemExit as exit_request:
        pytest.fail(f"launcher rejected the installer's arguments: {capsys.readouterr().err.strip()}")
        raise AssertionError from exit_request
