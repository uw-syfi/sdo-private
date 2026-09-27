"""Install the production SDO controller on Kubernetes."""

from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Generic, Protocol, TypeVar, overload

import yaml


class ControllerInstallError(RuntimeError):
    """Raised when the in-cluster SDO controller cannot be installed or completed."""


CODEX_HOME_PATH = "/workspace/.sdo-runtime/codex"
CLAUDE_CONFIG_PATH = "/workspace/.sdo-runtime/claude"
RUNTIME_BUILD_ROOT = "/workspace/.sdo-runtime/build"
RUNTIME_TMPDIR = f"{RUNTIME_BUILD_ROOT}/tmp"
RUNTIME_GO_TMPDIR = f"{RUNTIME_BUILD_ROOT}/go-tmp"
RUNTIME_GO_CACHE = f"{RUNTIME_BUILD_ROOT}/go-cache"


@dataclass(frozen=True)
class ControllerInstallConfig:
    repository: Path
    namespace: str
    application: str
    controller_image: str
    responder_image: str
    repository_pvc: str
    credentials_secret: str
    model: str
    timeout_seconds: int
    validator_image: str = "sdo-detector-validator:v0.1.0"
    verification_timeout_seconds: int = 120
    wait_for_completion: bool = False
    repair_policy: str = "commit"
    agent_provider: str = "codex"

    def __post_init__(self) -> None:
        if self.repair_policy not in ("commit", "recorded-actions"):
            raise ValueError("repair_policy must be 'commit' or 'recorded-actions'")
        if self.agent_provider not in ("codex", "claude"):
            raise ValueError("agent_provider must be 'codex' or 'claude'")


@dataclass(frozen=True)
class ControllerInstallResult:
    """Artifacts produced by one completed controller execution."""

    controller_logs: str


ResultT = TypeVar("ResultT", covariant=True)


class ControllerInstallExtension(Protocol, Generic[ResultT]):
    """Optional transport integration layered around the controller installation."""

    def controller_args(self, config: ControllerInstallConfig) -> list[str]: ...

    def resources(
        self,
        config: ControllerInstallConfig,
        pod_security: dict[str, Any],
        container_security: dict[str, Any],
    ) -> list[dict[str, Any]]: ...

    def wait_until_ready(self, config: ControllerInstallConfig) -> None: ...

    def complete(self, config: ControllerInstallConfig, result: ControllerInstallResult) -> ResultT: ...

    def cleanup(self, config: ControllerInstallConfig) -> None: ...


def controller_security_contexts() -> tuple[dict[str, Any], dict[str, Any]]:
    pod_security = {
        "runAsNonRoot": True,
        "runAsUser": 65532,
        "fsGroup": 65532,
        "seccompProfile": {"type": "RuntimeDefault"},
    }
    container_security = {
        "allowPrivilegeEscalation": False,
        "readOnlyRootFilesystem": True,
        "capabilities": {"drop": ["ALL"]},
    }
    return pod_security, container_security


def controller_resources(
    config: ControllerInstallConfig,
    *,
    extra_controller_args: list[str] | None = None,
    additional_resources: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    controller_args = [
        "controller",
        "--app",
        "/workspace/application",
        "--namespace",
        config.namespace,
        "--application",
        config.application,
        "--responder-image",
        config.responder_image,
        "--repository-pvc",
        config.repository_pvc,
        "--repository-mount-path",
        "/workspace",
        "--credentials-secret",
        config.credentials_secret,
        "--worktree-root",
        "/workspace/worktrees",
        "--response-timeout",
        f"{config.timeout_seconds}s",
        "--verification-timeout",
        f"{config.verification_timeout_seconds}s",
        "--repair-policy",
        config.repair_policy,
        f"--responder-env=CODEX_HOME={CODEX_HOME_PATH}",
        f"--responder-env=CLAUDE_CONFIG_DIR={CLAUDE_CONFIG_PATH}",
        f"--responder-env=SDO_RESPONDER_MODEL={config.model}",
        f"--responder-env=SDO_AGENT_PROVIDER={config.agent_provider}",
        "--broker-arg=-m",
        "--broker-arg=sdo.agent_runtime.responder.broker_cli",
        "--broker-arg=--proposal-command",
        "--broker-arg=git diff --check HEAD --",
        "--broker-arg=--validator-mode",
        "--broker-arg=kubernetes",
        "--broker-arg=--validator-namespace",
        f"--broker-arg={config.namespace}",
        "--broker-arg=--validator-image",
        f"--broker-arg={config.validator_image}",
        "--broker-arg=--validator-repository-pvc",
        f"--broker-arg={config.repository_pvc}",
        "--broker-arg=--validator-repository-mount-path",
        "--broker-arg=/workspace",
        "--broker-arg=--responder-model",
        f"--broker-arg={config.model}",
        "--broker-arg=--agent-provider",
        f"--broker-arg={config.agent_provider}",
        "--broker-arg=--reflection-model",
        f"--broker-arg={config.model}",
    ]
    controller_args.extend(extra_controller_args or [])
    pod_security, container_security = controller_security_contexts()
    return [
        {
            "apiVersion": "v1",
            "kind": "PersistentVolumeClaim",
            "metadata": {"name": config.repository_pvc, "namespace": config.namespace},
            "spec": {"accessModes": ["ReadWriteOnce"], "resources": {"requests": {"storage": "10Gi"}}},
        },
        *_rbac_resources(config.namespace),
        *(additional_resources or []),
        {
            "apiVersion": "v1",
            "kind": "Pod",
            "metadata": {"name": "sdo-repository-sync", "namespace": config.namespace},
            "spec": {
                "restartPolicy": "Never",
                "automountServiceAccountToken": False,
                "securityContext": pod_security,
                "containers": [
                    {
                        "name": "sync",
                        "image": config.controller_image,
                        "imagePullPolicy": "IfNotPresent",
                        "command": ["sleep", "3600"],
                        "securityContext": container_security,
                        "resources": {
                            "requests": {"cpu": "50m", "memory": "64Mi"},
                            "limits": {"cpu": "250m", "memory": "256Mi"},
                        },
                        "volumeMounts": [
                            {"name": "repository", "mountPath": "/workspace"},
                            {"name": "scratch", "mountPath": "/tmp"},
                        ],
                    }
                ],
                "volumes": [
                    {"name": "repository", "persistentVolumeClaim": {"claimName": config.repository_pvc}},
                    {"name": "scratch", "emptyDir": {}},
                ],
            },
        },
        {
            "apiVersion": "batch/v1",
            "kind": "Job",
            "metadata": {"name": "sdo-controller-run", "namespace": config.namespace},
            "spec": {
                "backoffLimit": 6,
                "podFailurePolicy": {
                    "rules": [
                        {
                            "action": "Ignore",
                            "onPodConditions": [{"type": "DisruptionTarget", "status": "True"}],
                        }
                    ]
                },
                "ttlSecondsAfterFinished": 600,
                "template": {
                    "metadata": {"labels": {"app.kubernetes.io/name": "sdo-controller"}},
                    "spec": {
                        "restartPolicy": "Never",
                        "serviceAccountName": "sdo-controller",
                        "securityContext": pod_security,
                        "initContainers": [
                            {
                                "name": "prepare-build-cache",
                                "image": config.controller_image,
                                "imagePullPolicy": "IfNotPresent",
                                "command": [
                                    "mkdir",
                                    "-p",
                                    RUNTIME_TMPDIR,
                                    RUNTIME_GO_TMPDIR,
                                    RUNTIME_GO_CACHE,
                                ],
                                "securityContext": container_security,
                                "resources": {
                                    "requests": {"cpu": "10m", "memory": "16Mi"},
                                    "limits": {"cpu": "100m", "memory": "64Mi"},
                                },
                                "volumeMounts": [{"name": "repository", "mountPath": "/workspace"}],
                            }
                        ],
                        "containers": [
                            {
                                "name": "controller",
                                "image": config.controller_image,
                                "imagePullPolicy": "IfNotPresent",
                                "command": ["python3", "-m", "controller.builder.check_cli"],
                                "args": controller_args,
                                "envFrom": [{"secretRef": {"name": config.credentials_secret}}],
                                "env": [
                                    {"name": "CODEX_HOME", "value": CODEX_HOME_PATH},
                                    {"name": "CLAUDE_CONFIG_DIR", "value": CLAUDE_CONFIG_PATH},
                                    {"name": "TMPDIR", "value": RUNTIME_TMPDIR},
                                    {"name": "GOTMPDIR", "value": RUNTIME_GO_TMPDIR},
                                    {"name": "GOCACHE", "value": RUNTIME_GO_CACHE},
                                    {"name": "SDO_CONTROLLER_JOB", "value": "sdo-controller-run"},
                                    {
                                        "name": "SDO_CONTROLLER_POD_UID",
                                        "valueFrom": {"fieldRef": {"fieldPath": "metadata.uid"}},
                                    },
                                    {"name": "GIT_AUTHOR_NAME", "value": "sdo-controller"},
                                    {"name": "GIT_AUTHOR_EMAIL", "value": "sdo-controller@invalid"},
                                    {"name": "GIT_COMMITTER_NAME", "value": "sdo-controller"},
                                    {"name": "GIT_COMMITTER_EMAIL", "value": "sdo-controller@invalid"},
                                ],
                                "securityContext": container_security,
                                "resources": {
                                    "requests": {"cpu": "250m", "memory": "512Mi"},
                                    "limits": {"cpu": "2", "memory": "4Gi"},
                                },
                                "volumeMounts": [
                                    {"name": "repository", "mountPath": "/workspace"},
                                    {"name": "scratch", "mountPath": "/tmp"},
                                    {"name": "credentials", "mountPath": "/sdo/credentials", "readOnly": True},
                                ],
                            }
                        ],
                        "volumes": [
                            {"name": "repository", "persistentVolumeClaim": {"claimName": config.repository_pvc}},
                            {"name": "scratch", "emptyDir": {"sizeLimit": "4Gi"}},
                            {"name": "credentials", "secret": {"secretName": config.credentials_secret}},
                        ],
                    },
                },
            },
        },
    ]


@overload
def install_controller(
    config: ControllerInstallConfig,
    extension: None = None,
) -> ControllerInstallResult: ...


@overload
def install_controller(
    config: ControllerInstallConfig,
    extension: ControllerInstallExtension[ResultT],
) -> ResultT: ...


def install_controller(
    config: ControllerInstallConfig,
    extension: ControllerInstallExtension[ResultT] | None = None,
) -> ControllerInstallResult | ResultT:
    extra_args = extension.controller_args(config) if extension is not None else []
    pod_security, container_security = controller_security_contexts()
    extra_resources = extension.resources(config, pod_security, container_security) if extension is not None else []
    resources = controller_resources(
        config,
        extra_controller_args=extra_args,
        additional_resources=extra_resources,
    )
    base = resources[:-1]
    controller_job = resources[-1]
    kubectl(["apply", "-f", "-"], namespace=config.namespace, input_text=yaml.safe_dump_all(base))
    _ensure_credentials_secret(config)
    try:
        if extension is not None:
            extension.wait_until_ready(config)
        _wait_for_repository_sync(config.namespace)
        kubectl(
            [
                "exec",
                "sdo-repository-sync",
                "--",
                "sh",
                "-c",
                "rm -rf /workspace/application /workspace/worktrees "
                "&& mkdir -p /workspace/application /workspace/worktrees",
            ],
            namespace=config.namespace,
        )
        _copy_repository_to_pod(config)
        kubectl(["delete", "job/sdo-controller-run", "--ignore-not-found=true"], namespace=config.namespace)
        kubectl(["apply", "-f", "-"], namespace=config.namespace, input_text=yaml.safe_dump(controller_job))
        if not config.wait_for_completion:
            return ControllerInstallResult(controller_logs="")
        try:
            _wait_for_controller_job(config)
        except ControllerInstallError:
            kubectl(["logs", "job/sdo-controller-run"], namespace=config.namespace, check=False)
            raise
        controller_logs = _controller_job_logs(config.namespace)
        _copy_repository_from_pod(config)
        result = ControllerInstallResult(controller_logs=controller_logs)
        if extension is None:
            return result
        return extension.complete(config, result)
    finally:
        _delete_repository_sync(config.namespace)
        if extension is not None:
            extension.cleanup(config)


def _delete_repository_sync(namespace: str) -> None:
    """Request cleanup without waiting on watch behavior unsupported by the filtered proxy."""

    kubectl(
        ["delete", "pod/sdo-repository-sync", "--ignore-not-found=true", "--wait=false"],
        namespace=namespace,
        check=False,
    )


def _controller_job_logs(namespace: str) -> str:
    """Read the successful retry pod so transient failed-pod logs cannot hide rollout evidence."""

    pods_result = kubectl(
        ["get", "pods", "--selector", "job-name=sdo-controller-run", "-o", "json"],
        namespace=namespace,
        check=False,
    )
    if pods_result.returncode == 0:
        items = json.loads(pods_result.stdout).get("items", [])
        succeeded = sorted(
            str(item.get("metadata", {}).get("name", ""))
            for item in items
            if isinstance(item, dict) and item.get("status", {}).get("phase") == "Succeeded"
        )
        if succeeded:
            return kubectl(["logs", f"pod/{succeeded[-1]}"], namespace=namespace, check=False).stdout
    return kubectl(["logs", "job/sdo-controller-run"], namespace=namespace, check=False).stdout


def _rbac_resources(namespace: str) -> list[dict[str, Any]]:
    root = Path(__file__).resolve().parents[2]
    path = root / "controller" / "runtime" / "deploy" / "rbac.yaml"
    resources = [document for document in yaml.safe_load_all(path.read_text(encoding="utf-8")) if document]
    for resource in resources:
        resource.setdefault("metadata", {})["namespace"] = namespace
    return resources


def _wait_for_controller_job(config: ControllerInstallConfig) -> None:
    deadline = time.monotonic() + config.timeout_seconds + 300
    while time.monotonic() < deadline:
        completed = kubectl(
            ["get", "job/sdo-controller-run", "-o", "json"],
            namespace=config.namespace,
            check=False,
        )
        if completed.returncode != 0:
            time.sleep(2)
            continue
        state = _job_state(json.loads(completed.stdout))
        if state == "complete":
            return
        if state == "failed":
            raise ControllerInstallError("controller Job failed")
        time.sleep(2)
    raise ControllerInstallError("controller Job did not complete before its runtime deadline")


def _wait_for_repository_sync(namespace: str, timeout_seconds: int = 180) -> None:
    """Poll readiness without relying on Kubernetes watch support."""

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        completed = kubectl(
            ["get", "pod/sdo-repository-sync", "-o", "json"],
            namespace=namespace,
            check=False,
        )
        if completed.returncode == 0 and _pod_is_ready(json.loads(completed.stdout)):
            return
        time.sleep(2)
    raise ControllerInstallError("repository sync Pod did not become ready")


def _pod_is_ready(pod: dict[str, Any]) -> bool:
    status = pod.get("status")
    if not isinstance(status, dict):
        return False
    conditions = status.get("conditions", [])
    return isinstance(conditions, list) and any(
        isinstance(condition, dict) and condition.get("type") == "Ready" and condition.get("status") == "True"
        for condition in conditions
    )


def _job_state(job: dict[str, Any]) -> str:
    status = job.get("status")
    if not isinstance(status, dict):
        return "running"
    if status.get("succeeded", 0):
        return "complete"
    conditions = status.get("conditions", [])
    if isinstance(conditions, list) and any(
        isinstance(condition, dict) and condition.get("type") == "Failed" and condition.get("status") == "True"
        for condition in conditions
    ):
        return "failed"
    return "running"


def _ensure_credentials_secret(config: ControllerInstallConfig) -> None:
    completed = kubectl(
        ["get", f"secret/{config.credentials_secret}"],
        namespace=config.namespace,
        check=False,
    )
    if completed.returncode == 0:
        return
    secret_data: dict[str, str] = {}
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if api_key:
        secret_data["OPENAI_API_KEY"] = api_key
    codex_home = Path(os.getenv("CODEX_HOME", str(Path.home() / ".codex")))
    auth_file = codex_home / "auth.json"
    if auth_file.is_file():
        secret_data["auth.json"] = auth_file.read_text(encoding="utf-8")
    anthropic_api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if anthropic_api_key:
        secret_data["ANTHROPIC_API_KEY"] = anthropic_api_key
    claude_credentials = Path.home() / ".claude" / ".credentials.json"
    if claude_credentials.is_file():
        secret_data[".credentials.json"] = claude_credentials.read_text(encoding="utf-8")
    if not secret_data:
        raise ControllerInstallError(
            f"Secret {config.credentials_secret!r} does not exist and no agent credentials are available"
        )
    secret = {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {"name": config.credentials_secret, "namespace": config.namespace},
        "type": "Opaque",
        "stringData": secret_data,
    }
    kubectl(["apply", "-f", "-"], namespace=config.namespace, input_text=yaml.safe_dump(secret))


def _copy_repository_to_pod(config: ControllerInstallConfig) -> None:
    producer = subprocess.Popen(
        ["tar", "-C", str(config.repository), "-cf", "-", "."],
        stdout=subprocess.PIPE,
        env=_tar_environment(),
    )
    if producer.stdout is None:
        raise ControllerInstallError("failed to open repository tar stream")
    consumer = subprocess.run(
        [
            "kubectl",
            "--namespace",
            config.namespace,
            "exec",
            "-i",
            "sdo-repository-sync",
            "--",
            "tar",
            "-C",
            "/workspace/application",
            "-xf",
            "-",
        ],
        stdin=producer.stdout,
        check=False,
        capture_output=True,
        text=False,
        env=_tar_environment(),
    )
    producer.stdout.close()
    producer_returncode = producer.wait()
    if producer_returncode != 0 or consumer.returncode != 0:
        raise ControllerInstallError("failed to seed application repository into the shared PVC")


def _copy_repository_from_pod(config: ControllerInstallConfig) -> None:
    producer = subprocess.Popen(
        [
            "kubectl",
            "--namespace",
            config.namespace,
            "exec",
            "sdo-repository-sync",
            "--",
            "tar",
            "-C",
            "/workspace/application",
            "-cf",
            "-",
            ".",
        ],
        stdout=subprocess.PIPE,
    )
    if producer.stdout is None:
        raise ControllerInstallError("failed to open runtime repository tar stream")
    consumer = subprocess.run(
        ["tar", "-C", str(config.repository), "-xf", "-"],
        stdin=producer.stdout,
        check=False,
        capture_output=True,
        text=False,
        env=_tar_environment(),
    )
    producer.stdout.close()
    producer_returncode = producer.wait()
    if producer_returncode != 0 or consumer.returncode != 0:
        raise ControllerInstallError("failed to synchronize accepted runtime commits back to the source workspace")


def _tar_environment() -> dict[str, str]:
    return {**os.environ, "COPYFILE_DISABLE": "1"}


def kubectl(
    args: list[str],
    *,
    namespace: str,
    input_text: str | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        ["kubectl", "--namespace", namespace, *args],
        input=input_text,
        check=False,
        capture_output=True,
        text=True,
    )
    if check and completed.returncode != 0:
        details = completed.stderr.strip() or completed.stdout.strip()
        raise ControllerInstallError(f"kubectl {' '.join(args)} failed: {details}")
    return completed
