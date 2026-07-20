from __future__ import annotations

from pathlib import Path

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


def test_production_runtime_module_has_no_benchmark_dependency() -> None:
    source = Path(__file__).parents[4] / "sdo" / "controller_install" / "kubernetes.py"
    contents = source.read_text(encoding="utf-8").upper()

    assert "SREGYM" not in contents
    assert "PRODUCTION_RECEIPT" not in contents
