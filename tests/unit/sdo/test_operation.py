from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from sdo.__main__ import main
from sdo.agent_runtime.lifecycle.deployment import DeploymentAttempt, DeploymentVerification
from sdo.controller_install import ControllerInstallResult
from sdo.operation import (
    ControllerDeploymentVerifier,
    OperationConfig,
    OperationError,
    operate,
)
from sdo.operational_memory.sandbox import SandboxResult


def _repository(root: Path) -> Path:
    repository = root / "example"
    repository.mkdir()
    (repository / ".git").mkdir()
    return repository


def _config(repository: Path) -> OperationConfig:
    return OperationConfig(
        repository=repository,
        namespace="demo",
        application="example",
        health_objective="Users can complete requests.",
        model="gpt-test",
        controller_image="controller:test",
        responder_image="responder:test",
        validator_image="validator:test",
        repository_pvc="application-source",
        credentials_secret="codex-credentials",
        max_attempts=4,
        timeout_seconds=90,
    )


def test_operate_deploys_with_independent_verifier_then_starts_continuous_runtime(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    config = _config(repository)
    verifier = object()
    backend = object()
    calls: dict[str, object] = {}
    accepted = DeploymentAttempt(
        deployed=True,
        source_commit="a" * 40,
        agent_session_id="deployment-session",
        summary="deployed",
    )

    def deployment_runner(root: Path, **kwargs: object) -> DeploymentAttempt:
        calls["deployment_root"] = root
        calls["deployment_kwargs"] = kwargs
        return accepted

    def runtime_runner(runtime_config: object) -> ControllerInstallResult:
        calls["runtime_config"] = runtime_config
        return ControllerInstallResult(controller_logs="")

    result = operate(
        config,
        deployment_backend=backend,
        verifier=verifier,
        deployment_runner=deployment_runner,
        runtime_runner=runtime_runner,
    )

    assert result.deployment == accepted
    assert calls["deployment_root"] == repository.resolve()
    deployment_kwargs = calls["deployment_kwargs"]
    assert isinstance(deployment_kwargs, dict)
    assert deployment_kwargs == {
        "namespace": "demo",
        "application": "example",
        "health_objective": "Users can complete requests.",
        "backend": backend,
        "verifier": verifier,
        "max_attempts": 4,
    }
    runtime_config = calls["runtime_config"]
    assert runtime_config.repository == repository.resolve()
    assert runtime_config.namespace == "demo"
    assert runtime_config.application == "example"
    assert runtime_config.controller_image == "controller:test"
    assert runtime_config.responder_image == "responder:test"
    assert runtime_config.validator_image == "validator:test"
    assert runtime_config.repository_pvc == "application-source"
    assert runtime_config.credentials_secret == "codex-credentials"
    assert runtime_config.model == "gpt-test"
    assert runtime_config.timeout_seconds == 90
    assert runtime_config.wait_for_completion is False
    assert runtime_config.repair_policy == "commit"


def test_controller_verifier_bootstraps_memory_and_accepts_empty_finding_stream(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    calls: dict[str, object] = {}
    backend = object()

    def reuse(root: Path, **kwargs: object) -> bool:
        calls["reuse"] = (root, kwargs)
        return False

    def lifecycle(root: Path, **kwargs: object) -> str:
        calls["lifecycle"] = (root, kwargs)
        return "memory-commit"

    def command(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls["command"] = (command, kwargs)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    verifier = ControllerDeploymentVerifier(
        lifecycle_backend=backend,
        lifecycle_reuser=reuse,
        lifecycle_runner=lifecycle,
        command_runner=command,
        timeout_seconds=90,
    )
    verification = verifier.verify(
        repository=repository,
        namespace="demo",
        application="example",
        health_objective="Users can complete requests.",
        attempt=DeploymentAttempt(
            deployed=True,
            source_commit="a" * 40,
            agent_session_id="deployment-session",
            summary="deployed",
        ),
    )

    assert verification == DeploymentVerification(healthy=True, feedback="independent detector found no failures")
    assert calls["reuse"] == (
        repository.resolve(),
        {"application": "example", "health_objective": "Users can complete requests."},
    )
    assert calls["lifecycle"] == (
        repository.resolve(),
        {
            "application": "example",
            "health_objective": "Users can complete requests.",
            "backend": backend,
        },
    )
    command_args, command_kwargs = calls["command"]
    assert command_args == [
        sys.executable,
        "-m",
        "controller.builder.check_cli",
        "run-once",
        "--app",
        str(repository.resolve()),
        "--namespace",
        "demo",
    ]
    assert command_kwargs["timeout"] == 90


@pytest.mark.parametrize(
    ("completed", "feedback"),
    [
        (
            subprocess.CompletedProcess(
                ["python"],
                0,
                stdout='{"rule_id":"no-endpoints","summary":"Service has no endpoints"}\n',
                stderr="",
            ),
            "Service has no endpoints",
        ),
        (
            subprocess.CompletedProcess(["python"], 1, stdout="", stderr="unable to read cluster"),
            "unable to read cluster",
        ),
        (
            subprocess.CompletedProcess(["python"], 0, stdout="not-json\n", stderr=""),
            "invalid finding JSON",
        ),
    ],
)
def test_controller_verifier_rejects_findings_and_errors(
    tmp_path: Path,
    completed: subprocess.CompletedProcess[str],
    feedback: str,
) -> None:
    repository = _repository(tmp_path)
    verifier = ControllerDeploymentVerifier(
        lifecycle_reuser=lambda *_args, **_kwargs: True,
        command_runner=lambda *_args, **_kwargs: completed,
    )

    result = verifier.verify(
        repository=repository,
        namespace="demo",
        application="example",
        health_objective="Users can complete requests.",
        attempt=DeploymentAttempt(
            deployed=True,
            source_commit="a" * 40,
            agent_session_id="deployment-session",
            summary="deployed",
        ),
    )

    assert result.healthy is False
    assert feedback in result.feedback


def test_cli_exposes_sdo_operate_and_maps_all_flags(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    captured: list[OperationConfig] = []

    exit_code = main(
        [
            "operate",
            str(repository),
            "--namespace",
            "demo",
            "--goal",
            "Users can complete requests.",
            "--application",
            "store",
            "--model",
            "gpt-test",
            "--controller-image",
            "controller:test",
            "--responder-image",
            "responder:test",
            "--validator-image",
            "validator:test",
            "--repository-pvc",
            "source-pvc",
            "--credentials-secret",
            "credentials",
            "--attempts",
            "5",
            "--timeout-seconds",
            "120",
        ],
        operation_runner=lambda config: captured.append(config),
    )

    assert exit_code == 0
    assert captured == [
        OperationConfig(
            repository=repository,
            namespace="demo",
            application="store",
            health_objective="Users can complete requests.",
            model="gpt-test",
            controller_image="controller:test",
            responder_image="responder:test",
            validator_image="validator:test",
            repository_pvc="source-pvc",
            credentials_secret="credentials",
            max_attempts=5,
            timeout_seconds=120,
        )
    ]


def test_cli_detector_check_validates_only_the_current_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repository = _repository(tmp_path)
    checked: list[Path] = []

    class Validator:
        def run(self, app_root: Path) -> SandboxResult:
            checked.append(app_root)
            return SandboxResult(returncode=0, stdout="detector checks passed\n")

    monkeypatch.chdir(repository)

    exit_code = main(["detector", "check"], detector_check_runner=Validator())

    assert exit_code == 0
    assert checked == [repository.resolve()]
    assert capsys.readouterr().out == "detector checks passed\n"


def test_cli_reads_goal_file_and_returns_clear_operation_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repository = _repository(tmp_path)
    goal_file = tmp_path / "goal.md"
    goal_file.write_text("Users can complete requests.\n", encoding="utf-8")

    def fail(_config: OperationConfig) -> None:
        raise OperationError("controller rollout failed")

    exit_code = main(
        [
            "operate",
            str(repository),
            "--namespace",
            "demo",
            "--goal-file",
            str(goal_file),
        ],
        operation_runner=fail,
    )

    assert exit_code == 1
    assert "controller rollout failed" in capsys.readouterr().err


def test_cli_goal_inputs_are_mutually_exclusive(tmp_path: Path) -> None:
    repository = _repository(tmp_path)

    with pytest.raises(SystemExit, match="2"):
        main(
            [
                "operate",
                str(repository),
                "--namespace",
                "demo",
                "--goal",
                "healthy",
                "--goal-file",
                "goal.md",
            ]
        )


def test_public_operation_source_has_no_legacy_or_benchmark_transport() -> None:
    root = Path(__file__).parents[3]
    sources = [(root / "sdo/operation.py").read_text(), (root / "sdo/__main__.py").read_text()]
    normalized = "\n".join(sources).upper()

    assert "SREGYM" not in normalized
    assert "SUBMISSION" not in normalized
    assert not (root / "sdo/commands/run.py").exists()


def test_operate_can_install_the_controller_in_its_own_namespace(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    captured: list[OperationConfig] = []

    exit_code = main(
        [
            "operate",
            str(repository),
            "--namespace",
            "demo",
            "--goal",
            "Users can complete requests.",
            "--controller-namespace",
            "demo-sdo",
        ],
        operation_runner=lambda config: captured.append(config),
    )

    assert exit_code == 0
    assert captured[0].controller_namespace == "demo-sdo"
    installed: list[object] = []
    operate(
        captured[0],
        deployment_backend=object(),
        verifier=object(),
        deployment_runner=lambda *_args, **_kwargs: DeploymentAttempt(
            deployed=True, source_commit="a" * 40, agent_session_id="s", summary="deployed"
        ),
        runtime_runner=lambda config: installed.append(config) or ControllerInstallResult(controller_logs=""),
    )
    assert installed[0].control_namespace == "demo-sdo"  # type: ignore[attr-defined]
    assert installed[0].namespace == "demo"  # type: ignore[attr-defined]
