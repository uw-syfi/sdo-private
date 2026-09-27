from __future__ import annotations

from pathlib import Path

import pytest

from sdo.controller_install import ControllerInstallConfig, controller_resources


def _config() -> ControllerInstallConfig:
    return ControllerInstallConfig(
        repository=Path("/tmp/application"),
        namespace="demo",
        application="demo",
        controller_image="controller:test",
        responder_image="responder:test",
        repository_pvc="repository",
        credentials_secret="credentials",
        model="gpt-test",
        timeout_seconds=60,
    )


def test_production_runtime_resources_have_no_benchmark_transport() -> None:
    resources = controller_resources(_config())
    serialized = repr(resources).upper()
    controller = next(resource for resource in resources if resource["kind"] == "Job")
    args = controller["spec"]["template"]["spec"]["containers"][0]["args"]

    assert "SREGYM" not in serialized
    assert "SUBMISSION" not in serialized
    assert not any(resource["metadata"]["name"] == "sdo-sregym-bridge" for resource in resources)
    assert "--exit-after-closure" not in args
    assert "--duration" not in args
    assert args[args.index("--response-timeout") + 1] == "60s"
    assert args[args.index("--verification-timeout") + 1] == "120s"
    assert "--responder-env=SDO_RESPONDER_MODEL=gpt-test" in args
    assert args[args.index("--repair-policy") + 1] == "commit"


def test_recorded_actions_policy_is_passed_to_controller() -> None:
    config = ControllerInstallConfig(**{**_config().__dict__, "repair_policy": "recorded-actions"})
    controller = next(resource for resource in controller_resources(config) if resource["kind"] == "Job")
    args = controller["spec"]["template"]["spec"]["containers"][0]["args"]

    assert args[args.index("--repair-policy") + 1] == "recorded-actions"


def _broker_args(config: ControllerInstallConfig) -> list[str]:
    controller = next(resource for resource in controller_resources(config) if resource["kind"] == "Job")
    args = controller["spec"]["template"]["spec"]["containers"][0]["args"]
    return [arg.removeprefix("--broker-arg=") for arg in args if arg.startswith("--broker-arg=")]


def test_reflection_session_mode_defaults_to_resume_and_is_passed_to_the_broker() -> None:
    broker = _broker_args(_config())

    assert broker[broker.index("--reflection-session") + 1] == "resume"
    # A fresh reflection brief quotes the responder's commands from its per-turn log.
    assert broker[broker.index("--responder-turn-log") + 1] == "/workspace/.sdo-runtime/usage/responder-turns.jsonl"


def test_fresh_reflection_session_mode_is_passed_to_the_broker() -> None:
    config = ControllerInstallConfig(**{**_config().__dict__, "reflection_session": "fresh"})
    broker = _broker_args(config)

    assert broker[broker.index("--reflection-session") + 1] == "fresh"


def test_unknown_reflection_session_mode_is_rejected() -> None:
    with pytest.raises(ValueError, match="reflection_session"):
        ControllerInstallConfig(**{**_config().__dict__, "reflection_session": "transcript"})


def test_production_runtime_module_has_no_benchmark_dependency() -> None:
    source = Path(__file__).parents[4] / "sdo" / "controller_install" / "kubernetes.py"
    contents = source.read_text(encoding="utf-8").upper()

    assert "SREGYM" not in contents
    assert "PRODUCTION_RECEIPT" not in contents


def test_broker_and_responder_pods_log_per_turn_usage_on_the_workspace_pvc() -> None:
    controller = next(resource for resource in controller_resources(_config()) if resource["kind"] == "Job")
    container = controller["spec"]["template"]["spec"]["containers"][0]
    args = container["args"]

    # The broker, and so every reflection turn, runs inside the controller pod.
    assert {
        "name": "SDO_TURN_USAGE_LOG",
        "value": "/workspace/.sdo-runtime/usage/controller-turns.jsonl",
    } in container["env"]
    assert "--responder-env=SDO_TURN_USAGE_LOG=/workspace/.sdo-runtime/usage/responder-turns.jsonl" in args
    assert {"name": "repository", "mountPath": "/workspace"} in container["volumeMounts"]
