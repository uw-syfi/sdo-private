"""Single production operation path for a Self-Defining Operator."""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from sdo.agent_runtime.lifecycle import (
    ClaudeDeploymentBackend,
    ClaudeLifecycleBackend,
    CodexDeploymentBackend,
    CodexLifecycleBackend,
    DeploymentAttempt,
    DeploymentBackend,
    DeploymentError,
    DeploymentVerification,
    DeploymentVerifier,
    LifecycleAgentBackend,
    LifecycleError,
    SandboxResult,
    SandboxRunner,
    deploy_from_source,
    reuse_initial_lifecycle_if_valid,
    run_initial_lifecycle,
)
from sdo.agent_runtime.lifecycle import (
    check_detector_workspace as _check_detector_workspace,
)
from sdo.controller_install import (
    ControllerInstallConfig,
    ControllerInstallError,
    ControllerInstallResult,
    install_controller,
)


class OperationError(RuntimeError):
    """Raised when the production operation path cannot be started."""


def check_detector_workspace(app_root: Path, *, validator: SandboxRunner | None = None) -> SandboxResult:
    """Expose the lifecycle's isolated authoring check through the public command layer."""

    return _check_detector_workspace(app_root, validator=validator)


@dataclass(frozen=True)
class OperationConfig:
    repository: Path
    namespace: str
    application: str
    health_objective: str
    model: str = "gpt-5.4"
    controller_image: str = "sdo-controller:v0.1.0"
    responder_image: str = "sdo-responder:v0.1.0"
    validator_image: str = "sdo-detector-validator:v0.1.0"
    repository_pvc: str = "sdo-application-repository"
    credentials_secret: str = "sdo-codex-credentials"
    max_attempts: int = 3
    timeout_seconds: int = 1800
    repair_policy: str = "commit"
    agent_provider: str = "codex"

    def __post_init__(self) -> None:
        if not isinstance(self.repository, Path):
            raise TypeError("repository must be a Path")
        object.__setattr__(self, "repository", self.repository.resolve())
        for name in (
            "namespace",
            "application",
            "health_objective",
            "model",
            "controller_image",
            "responder_image",
            "validator_image",
            "repository_pvc",
            "credentials_secret",
            "repair_policy",
            "agent_provider",
        ):
            value = getattr(self, name)
            if not isinstance(value, str):
                raise TypeError(f"{name} must be a string")
            if not value.strip():
                raise ValueError(f"{name} must not be empty")
        if self.repair_policy not in ("commit", "recorded-actions"):
            raise ValueError("repair_policy must be 'commit' or 'recorded-actions'")
        if self.agent_provider not in ("codex", "claude"):
            raise ValueError("agent_provider must be 'codex' or 'claude'")
        if not isinstance(self.max_attempts, int):
            raise TypeError("max_attempts must be an integer")
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        if not isinstance(self.timeout_seconds, int):
            raise TypeError("timeout_seconds must be an integer")
        if self.timeout_seconds < 1:
            raise ValueError("timeout_seconds must be positive")


@dataclass(frozen=True)
class OperationResult:
    deployment: DeploymentAttempt
    runtime: ControllerInstallResult


class LifecycleReuser(Protocol):
    def __call__(
        self,
        app_root: Path,
        *,
        application: str,
        health_objective: str,
    ) -> bool: ...


class LifecycleRunner(Protocol):
    def __call__(
        self,
        app_root: Path,
        *,
        application: str,
        health_objective: str,
        backend: LifecycleAgentBackend | None = None,
    ) -> str: ...


CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


class DeploymentRunner(Protocol):
    def __call__(
        self,
        repository: Path,
        *,
        namespace: str,
        application: str,
        health_objective: str,
        verifier: DeploymentVerifier,
        backend: DeploymentBackend | None = None,
        max_attempts: int = 3,
    ) -> DeploymentAttempt: ...


class RuntimeRunner(Protocol):
    def __call__(self, config: ControllerInstallConfig) -> ControllerInstallResult: ...


class ControllerDeploymentVerifier:
    """Accept deployments only when the independent health detector emits no findings."""

    def __init__(
        self,
        *,
        model: str | None = None,
        lifecycle_backend: LifecycleAgentBackend | None = None,
        lifecycle_reuser: LifecycleReuser = reuse_initial_lifecycle_if_valid,
        lifecycle_runner: LifecycleRunner = run_initial_lifecycle,
        command_runner: CommandRunner = subprocess.run,
        python_executable: str = sys.executable,
        timeout_seconds: int = 1800,
    ) -> None:
        if timeout_seconds < 1:
            raise ValueError("verifier timeout must be positive")
        self.lifecycle_backend = lifecycle_backend or CodexLifecycleBackend(model=model)
        self.lifecycle_reuser = lifecycle_reuser
        self.lifecycle_runner = lifecycle_runner
        self.command_runner = command_runner
        self.python_executable = python_executable
        self.timeout_seconds = timeout_seconds

    def verify(
        self,
        *,
        repository: Path,
        namespace: str,
        application: str,
        health_objective: str,
        attempt: DeploymentAttempt,
    ) -> DeploymentVerification:
        root = repository.resolve()
        try:
            reusable = self.lifecycle_reuser(
                root,
                application=application,
                health_objective=health_objective,
            )
            if not reusable:
                self.lifecycle_runner(
                    root,
                    application=application,
                    health_objective=health_objective,
                    backend=self.lifecycle_backend,
                )
        except (LifecycleError, OSError, TypeError, ValueError) as exc:
            return DeploymentVerification(
                healthy=False,
                feedback=f"independent detector bootstrap failed for {attempt.source_commit}: {exc}",
            )

        command = [
            self.python_executable,
            "-m",
            "controller.builder.check_cli",
            "run-once",
            "--app",
            str(root),
            "--namespace",
            namespace,
        ]
        try:
            completed = self.command_runner(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            return DeploymentVerification(
                healthy=False,
                feedback=f"independent detector timed out after {self.timeout_seconds}s",
            )
        except OSError as exc:
            return DeploymentVerification(healthy=False, feedback=f"independent detector could not run: {exc}")
        if completed.returncode != 0:
            details = completed.stderr.strip() or completed.stdout.strip() or "no diagnostic output"
            return DeploymentVerification(
                healthy=False,
                feedback=f"independent detector exited with code {completed.returncode}: {details}",
            )
        try:
            findings = _finding_payloads(completed.stdout)
        except ValueError as exc:
            return DeploymentVerification(healthy=False, feedback=str(exc))
        if findings:
            return DeploymentVerification(
                healthy=False,
                feedback=(
                    f"independent detector emitted {len(findings)} finding(s): "
                    + "; ".join(_finding_summary(finding) for finding in findings)
                ),
            )
        return DeploymentVerification(healthy=True, feedback="independent detector found no failures")


def operate(
    config: OperationConfig,
    *,
    deployment_backend: DeploymentBackend | None = None,
    verifier: DeploymentVerifier | None = None,
    deployment_runner: DeploymentRunner = deploy_from_source,
    runtime_runner: RuntimeRunner = install_controller,
) -> OperationResult:
    """Deploy, independently verify, then start the continuous controller runtime."""

    backend_type = ClaudeDeploymentBackend if config.agent_provider == "claude" else CodexDeploymentBackend
    lifecycle_type = ClaudeLifecycleBackend if config.agent_provider == "claude" else CodexLifecycleBackend
    selected_backend = deployment_backend or backend_type(model=config.model, timeout_seconds=config.timeout_seconds)
    selected_verifier = verifier or ControllerDeploymentVerifier(
        lifecycle_backend=lifecycle_type(model=config.model, timeout_seconds=config.timeout_seconds),
        timeout_seconds=config.timeout_seconds,
    )
    try:
        deployment = deployment_runner(
            config.repository,
            namespace=config.namespace,
            application=config.application,
            health_objective=config.health_objective,
            backend=selected_backend,
            verifier=selected_verifier,
            max_attempts=config.max_attempts,
        )
        runtime = runtime_runner(
            ControllerInstallConfig(
                repository=config.repository,
                namespace=config.namespace,
                application=config.application,
                controller_image=config.controller_image,
                responder_image=config.responder_image,
                validator_image=config.validator_image,
                repository_pvc=config.repository_pvc,
                credentials_secret=config.credentials_secret,
                model=config.model,
                timeout_seconds=config.timeout_seconds,
                repair_policy=config.repair_policy,
                agent_provider=config.agent_provider,
                wait_for_completion=False,
            )
        )
    except (ControllerInstallError, DeploymentError, LifecycleError, OSError, ValueError) as exc:
        raise OperationError(str(exc)) from exc
    return OperationResult(deployment=deployment, runtime=runtime)


def _finding_payloads(stdout: str) -> list[dict[str, object]]:
    findings: list[dict[str, object]] = []
    for line_number, raw_line in enumerate(stdout.splitlines(), start=1):
        if not raw_line.strip():
            continue
        try:
            payload = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid finding JSON on line {line_number}: {exc.msg}") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"invalid finding JSON on line {line_number}: expected an object")
        findings.append(payload)
    return findings


def _finding_summary(finding: dict[str, object]) -> str:
    rule = str(finding.get("rule_id") or finding.get("detector_id") or "unknown-rule")
    summary = str(finding.get("summary") or finding.get("evidence") or json.dumps(finding, sort_keys=True))
    return f"{rule}: {summary}"
