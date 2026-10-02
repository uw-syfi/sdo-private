from __future__ import annotations

import pytest

from controller.builder import check_cli
from sdo.controller_install import controller_launch_flags


def test_default_features_pass_the_required_controller_flags() -> None:
    flags = controller_launch_flags()

    assert {"--app", "--namespace", "--responder-image", "--repository-pvc", "--supervise"} <= flags
    assert "--closeout-state-gate" not in flags
    assert "--max-follow-ups" not in flags


def test_each_opt_in_feature_adds_its_flag() -> None:
    assert "--closeout-state-gate" in controller_launch_flags(closeout_state_gate=True)
    assert {"--max-follow-ups", "--follow-up-cooldown"} <= controller_launch_flags(max_follow_ups=2)
    assert "--control-namespace" in controller_launch_flags(controller_namespace="demo-control")


def test_nested_broker_and_responder_fragments_are_not_controller_flags() -> None:
    flags = controller_launch_flags(late_findings="pull", healthy_baseline=True)

    assert "--late-findings" not in flags
    assert "--healthy-baseline-source" not in flags
    assert {"--broker-arg", "--responder-env"} <= flags


@pytest.mark.parametrize("closeout_state_gate", [False, True])
def test_the_sdo_detector_check_launcher_defines_every_flag(
    closeout_state_gate: bool, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit):
        check_cli.main(["controller", "--help"])
    help_text = capsys.readouterr().out

    flags = controller_launch_flags(closeout_state_gate=closeout_state_gate, max_follow_ups=1)

    assert sorted(flag for flag in flags if flag not in help_text) == []


def test_invalid_feature_values_are_rejected_like_the_installer_rejects_them() -> None:
    with pytest.raises(ValueError, match="late_findings"):
        controller_launch_flags(late_findings="sometimes")
