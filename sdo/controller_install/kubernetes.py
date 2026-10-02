"""Install the production SDO controller on Kubernetes."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Generic, Protocol, TypeVar, overload

import yaml

from sdo.operational_memory import (
    DEFAULT_REFLECTION_SESSION,
    LATE_FINDINGS_MODES,
    REFLECTION_GUIDANCE_MODES,
    REFLECTION_SESSION_MODES,
    SOURCE_REPAIR_CHECK_COMMAND,
    require_matching_image_tags,
)


class ControllerInstallError(RuntimeError):
    """Raised when the in-cluster SDO controller cannot be installed or completed."""


RUNTIME_STATE_ROOT = "/workspace/.sdo-runtime"
CODEX_HOME_PATH = f"{RUNTIME_STATE_ROOT}/codex"
CLAUDE_CONFIG_PATH = f"{RUNTIME_STATE_ROOT}/claude"
RUNTIME_BUILD_ROOT = f"{RUNTIME_STATE_ROOT}/build"
RUNTIME_TMPDIR = f"{RUNTIME_BUILD_ROOT}/tmp"
RUNTIME_GO_TMPDIR = f"{RUNTIME_BUILD_ROOT}/go-tmp"
RUNTIME_GO_CACHE = f"{RUNTIME_BUILD_ROOT}/go-cache"
#: Read-only warm build cache baked into the controller image, copied into GOCACHE at start.
CONTROLLER_GO_CACHE_SEED = "/opt/sdo/go-build-cache"
#: Per-turn agent usage logs (``SDO_TURN_USAGE_LOG``) on the workspace PVC. The
#: controller pod hosts the broker and its reflection turns; responder Jobs
#: append to their own file so concurrent pods never share one writer.
RUNTIME_USAGE_ROOT = f"{RUNTIME_STATE_ROOT}/usage"
CONTROLLER_TURN_USAGE_LOG = f"{RUNTIME_USAGE_ROOT}/controller-turns.jsonl"
RESPONDER_TURN_USAGE_LOG = f"{RUNTIME_USAGE_ROOT}/responder-turns.jsonl"
TURN_USAGE_LOG_ENV = "SDO_TURN_USAGE_LOG"
#: Detector firing telemetry (``--firing-telemetry-path``): the controller's durable
#: JSONL record of when each detector activated, cleared or never persisted. It lives
#: on the workspace PVC outside ``.sdo/`` and is the Go runtime's job-mode default.
RUNTIME_TELEMETRY_ROOT = f"{RUNTIME_STATE_ROOT}/telemetry"
DETECTOR_FIRING_STREAM = f"{RUNTIME_TELEMETRY_ROOT}/detector-firings.jsonl"
#: Opt-in healthy-baseline gate: snapshots of the healthy application sit on the repository
#: volume (outside the application repository, so they are never memory artifacts) and the broker
#: stages them into the validated worktree under the relative directory while it validates.
HEALTHY_BASELINE_SOURCE = "/workspace/.sdo-baseline/healthy"
HEALTHY_BASELINE_DIRECTORY = ".sdo-baseline/healthy"
CONTROLLER_JOB_NAME = "sdo-controller-run"
REPOSITORY_SYNC_POD = "sdo-repository-sync"
MAINTENANCE_CONFIGMAP = "sdo-controller-maintenance"
INSTALL_FINGERPRINT_ANNOTATION = "sdo.dev/install-fingerprint"
APPLICATION_NAMESPACE_LABEL = "sdo.dev/application-namespace"
CONTROLLER_NAMESPACE_LABEL = "sdo.dev/controller-namespace"
_NAMESPACE_PATTERN = re.compile(r"^[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?$")
_READ_VERBS = ["get", "list", "watch"]


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
    # First reflection attempt: "resume" the responder session, or "fresh" (opt-in).
    reflection_session: str = DEFAULT_REFLECTION_SESSION
    # Reflection guidance: "baseline" per-cause learning, or "generalize" across parameter variants (opt-in).
    reflection_guidance: str = "baseline"
    # Late findings: "off", or "pull" so the responder can pull findings that activated after dispatch (opt-in).
    late_findings: str = "off"
    # Bounded follow-up responders for health findings that stay active after a response (opt-in; 0 = off).
    max_follow_ups: int = 0
    follow_up_cooldown_seconds: int = 30
    # Reject a proposed incident detector that fires on recorded healthy-application snapshots (opt-in).
    # The caller must record the snapshots at HEALTHY_BASELINE_SOURCE before the first incident.
    healthy_baseline: bool = False
    # Namespace for the controller, its repository PVC, state, credentials,
    # and responder/validator Jobs. ``None`` co-locates them with the
    # application; a separate namespace survives application redeploys.
    controller_namespace: str | None = None
    # Keep a healthy controller whose install fingerprint matches instead of
    # reseeding it. Off by default: an explicit reinstall reseeds memory.
    reuse_existing: bool = False

    def __post_init__(self) -> None:
        require_matching_image_tags(controller_image=self.controller_image, validator_image=self.validator_image)
        if self.controller_namespace is not None and not _NAMESPACE_PATTERN.fullmatch(self.controller_namespace):
            raise ValueError(f"controller_namespace must be a Kubernetes namespace name: {self.controller_namespace!r}")
        if self.repair_policy not in ("commit", "recorded-actions"):
            raise ValueError("repair_policy must be 'commit' or 'recorded-actions'")
        if self.agent_provider not in ("codex", "claude"):
            raise ValueError("agent_provider must be 'codex' or 'claude'")
        if self.reflection_session not in REFLECTION_SESSION_MODES:
            raise ValueError(f"reflection_session must be one of {', '.join(REFLECTION_SESSION_MODES)}")
        if self.reflection_guidance not in REFLECTION_GUIDANCE_MODES:
            raise ValueError(f"reflection_guidance must be one of {', '.join(REFLECTION_GUIDANCE_MODES)}")
        if self.late_findings not in LATE_FINDINGS_MODES:
            raise ValueError(f"late_findings must be one of {', '.join(LATE_FINDINGS_MODES)}")
        if self.max_follow_ups < 0:
            raise ValueError("max_follow_ups must not be negative")
        if self.follow_up_cooldown_seconds < 0:
            raise ValueError("follow_up_cooldown_seconds must not be negative")

    @property
    def control_namespace(self) -> str:
        """Namespace that holds the controller and all of its durable state."""

        return self.controller_namespace or self.namespace

    @property
    def split_namespaces(self) -> bool:
        return self.control_namespace != self.namespace


@dataclass(frozen=True)
class ControllerInstallResult:
    """Artifacts produced by one completed controller execution."""

    controller_logs: str
    # True when a healthy controller with the same install fingerprint was kept.
    reused: bool = False


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
        *(["--control-namespace", config.control_namespace] if config.split_namespaces else []),
        "--application",
        config.application,
        "--responder-image",
        config.responder_image,
        # The isolated traffic prober runs its static binary from the controller image.
        "--prober-image",
        config.controller_image,
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
        f"--responder-env={TURN_USAGE_LOG_ENV}={RESPONDER_TURN_USAGE_LOG}",
        "--broker-arg=-m",
        "--broker-arg=sdo.agent_runtime.responder.broker_cli",
        "--broker-arg=--proposal-command",
        f"--broker-arg={SOURCE_REPAIR_CHECK_COMMAND}",
        "--broker-arg=--validator-mode",
        "--broker-arg=kubernetes",
        "--broker-arg=--validator-namespace",
        f"--broker-arg={config.control_namespace}",
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
        "--broker-arg=--reflection-session",
        f"--broker-arg={config.reflection_session}",
        "--broker-arg=--reflection-guidance",
        f"--broker-arg={config.reflection_guidance}",
        "--broker-arg=--responder-turn-log",
        f"--broker-arg={RESPONDER_TURN_USAGE_LOG}",
    ]
    if config.late_findings != "off":
        controller_args.extend(
            [
                f"--responder-env=SDO_LATE_FINDINGS={config.late_findings}",
                "--broker-arg=--late-findings",
                f"--broker-arg={config.late_findings}",
                "--broker-arg=--late-findings-log",
                f"--broker-arg={RUNTIME_TELEMETRY_ROOT}/late-findings-pulls.jsonl",
            ]
        )
    if config.max_follow_ups > 0:
        controller_args.extend(
            [
                "--max-follow-ups",
                str(config.max_follow_ups),
                "--follow-up-cooldown",
                f"{config.follow_up_cooldown_seconds}s",
            ]
        )
    if config.healthy_baseline:
        controller_args.extend(
            [
                "--broker-arg=--healthy-baseline-source",
                f"--broker-arg={HEALTHY_BASELINE_SOURCE}",
                "--broker-arg=--healthy-baseline-dir",
                f"--broker-arg={HEALTHY_BASELINE_DIRECTORY}",
            ]
        )
    controller_args.extend(extra_controller_args or [])
    if not any(arg in controller_args for arg in ("--exit-after-closure", "--duration")):
        # A long-running controller rolls out learned detectors after each closure.
        controller_args.append("--supervise")
    pod_security, container_security = controller_security_contexts()
    namespace_resources: list[dict[str, Any]] = []
    if config.split_namespaces:
        namespace_resources.append(
            {
                "apiVersion": "v1",
                "kind": "Namespace",
                "metadata": {
                    "name": config.control_namespace,
                    "labels": {
                        "app.kubernetes.io/managed-by": "sdo",
                        CONTROLLER_NAMESPACE_LABEL: "true",
                        APPLICATION_NAMESPACE_LABEL: config.namespace,
                    },
                },
            }
        )
    control = config.control_namespace
    job = _controller_job(config, controller_args, pod_security, container_security)
    job["metadata"]["annotations"] = {INSTALL_FINGERPRINT_ANNOTATION: _install_fingerprint(job)}
    return [
        *namespace_resources,
        {
            "apiVersion": "v1",
            "kind": "PersistentVolumeClaim",
            "metadata": {"name": config.repository_pvc, "namespace": control},
            "spec": {"accessModes": ["ReadWriteOnce"], "resources": {"requests": {"storage": "10Gi"}}},
        },
        *_rbac_resources(config),
        *(additional_resources or []),
        _repository_sync_pod(config, pod_security, container_security),
        job,
    ]


def _install_fingerprint(job: dict[str, Any]) -> str:
    """Identify the controller workload; transport resources can be re-applied independently."""

    payload = json.dumps(job, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:32]


def _repository_sync_pod(
    config: ControllerInstallConfig,
    pod_security: dict[str, Any],
    container_security: dict[str, Any],
) -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {"name": REPOSITORY_SYNC_POD, "namespace": config.control_namespace},
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
    }


def _controller_job(
    config: ControllerInstallConfig,
    controller_args: list[str],
    pod_security: dict[str, Any],
    container_security: dict[str, Any],
) -> dict[str, Any]:
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {"name": CONTROLLER_JOB_NAME, "namespace": config.control_namespace},
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
                                # The controller image ships a warm, cgo-free build cache.
                                {"name": "SDO_GO_CACHE_SEED", "value": CONTROLLER_GO_CACHE_SEED},
                                {"name": TURN_USAGE_LOG_ENV, "value": CONTROLLER_TURN_USAGE_LOG},
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
    }


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
    control = config.control_namespace
    if config.reuse_existing and _controller_matches(config, controller_job):
        # Only namespace-scoped grants and transport can be missing: the
        # application namespace may have been recreated since installation.
        reapplied = [resource for resource in base if resource["kind"] != "Pod"]
        kubectl(["apply", "-f", "-"], namespace=None, input_text=yaml.safe_dump_all(reapplied))
        _ensure_credentials_secret(config)
        if extension is not None:
            extension.wait_until_ready(config)
        return ControllerInstallResult(controller_logs="", reused=True)
    kubectl(["apply", "-f", "-"], namespace=None, input_text=yaml.safe_dump_all(base))
    _ensure_credentials_secret(config)
    try:
        if extension is not None:
            extension.wait_until_ready(config)
        _wait_for_repository_sync(control)
        kubectl(
            [
                "exec",
                REPOSITORY_SYNC_POD,
                "--",
                "sh",
                "-c",
                "rm -rf /workspace/application /workspace/worktrees "
                "&& mkdir -p /workspace/application /workspace/worktrees",
            ],
            namespace=control,
        )
        _copy_repository_to_pod(config)
        kubectl(["delete", f"job/{CONTROLLER_JOB_NAME}", "--ignore-not-found=true"], namespace=control)
        kubectl(["apply", "-f", "-"], namespace=control, input_text=yaml.safe_dump(controller_job))
        if not config.wait_for_completion:
            return ControllerInstallResult(controller_logs="")
        try:
            _wait_for_controller_job(config)
        except ControllerInstallError:
            kubectl(["logs", f"job/{CONTROLLER_JOB_NAME}"], namespace=control, check=False)
            raise
        controller_logs = _controller_job_logs(control)
        _copy_repository_from_pod(config)
        result = ControllerInstallResult(controller_logs=controller_logs)
        if extension is None:
            return result
        return extension.complete(config, result)
    finally:
        _delete_repository_sync(control)
        if extension is not None:
            extension.cleanup(config)


def _controller_matches(config: ControllerInstallConfig, desired_job: dict[str, Any]) -> bool:
    """Return whether the running controller Job was installed from the same spec."""

    observed = kubectl(
        ["get", f"job/{CONTROLLER_JOB_NAME}", "-o", "json"], namespace=config.control_namespace, check=False
    )
    if observed.returncode != 0:
        return False
    try:
        job = json.loads(observed.stdout)
    except json.JSONDecodeError:
        return False
    annotations = job.get("metadata", {}).get("annotations") or {}
    wanted = desired_job["metadata"]["annotations"][INSTALL_FINGERPRINT_ANNOTATION]
    return (
        annotations.get(INSTALL_FINGERPRINT_ANNOTATION) == wanted
        and _job_state(job) == "running"
        and bool(job.get("status", {}).get("active"))
    )


def set_controller_maintenance(control_namespace: str, *, paused: bool, generation: str) -> None:
    """Declare whether the controller observes its application.

    Pausing is for planned application redeploys: the controller finishes any
    in-flight closure and reflection but stops evaluating detectors and stops
    observing the application namespace. Resuming with a new generation makes
    the controller re-list the namespace and evaluate every detector once.
    """

    if not generation.strip():
        raise ValueError("maintenance generation must be non-empty")
    document = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {"name": MAINTENANCE_CONFIGMAP, "namespace": control_namespace},
        "data": {"state": "paused" if paused else "active", "generation": generation},
    }
    kubectl(["apply", "-f", "-"], namespace=control_namespace, input_text=yaml.safe_dump(document))


def start_repository_sync(config: ControllerInstallConfig) -> None:
    """Start the helper pod that shares the controller's repository PVC."""

    pod_security, container_security = controller_security_contexts()
    pod = _repository_sync_pod(config, pod_security, container_security)
    kubectl(["apply", "-f", "-"], namespace=config.control_namespace, input_text=yaml.safe_dump(pod))
    _wait_for_repository_sync(config.control_namespace)


def stop_repository_sync(config: ControllerInstallConfig) -> None:
    _delete_repository_sync(config.control_namespace)


def sync_repository_from_controller(config: ControllerInstallConfig) -> None:
    """Copy the controller's operational repository back to ``config.repository``.

    Requires a running repository sync pod (see ``start_repository_sync``).
    """

    _copy_repository_from_pod(config)


def _delete_repository_sync(namespace: str) -> None:
    """Request cleanup without waiting on watch behavior unsupported by the filtered proxy."""

    kubectl(
        ["delete", f"pod/{REPOSITORY_SYNC_POD}", "--ignore-not-found=true", "--wait=false"],
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


def _rbac_resources(config: ControllerInstallConfig) -> list[dict[str, Any]]:
    """Grant the controller and responders access only to their own application.

    Single-namespace installs use ``controller/runtime/deploy/rbac.yaml``
    unchanged. A separate controller namespace holds both ServiceAccounts;
    the application namespace receives a read-only observer Role for the
    controller and the responder's repair Role, each bound to the controller
    namespace's ServiceAccount. No cluster-scoped grant is created.
    """

    root = Path(__file__).resolve().parents[2]
    path = root / "controller" / "runtime" / "deploy" / "rbac.yaml"
    resources = [document for document in yaml.safe_load_all(path.read_text(encoding="utf-8")) if document]
    control = config.control_namespace
    for resource in resources:
        resource.setdefault("metadata", {})["namespace"] = control
    if not config.split_namespaces:
        return resources
    roles = {resource["metadata"]["name"]: resource for resource in resources if resource["kind"] == "Role"}
    # In its own namespace a responder only publishes its result ConfigMap.
    roles["sdo-responder"]["rules"] = [
        {"apiGroups": [""], "resources": ["configmaps"], "verbs": ["get", "list", "watch", "create", "update", "patch"]}
    ]
    observer_rules = [
        {**rule, "verbs": list(_READ_VERBS)}
        for rule in roles["sdo-controller"]["rules"]
        if not set(rule.get("apiGroups", [])) & {"coordination.k8s.io", "batch"}
    ]
    # Beyond observation, the controller may only delete the responder's
    # labelled helper pods and Jobs (the label is enforced in the controller).
    helper_cleanup_rules = [
        {"apiGroups": [""], "resources": ["pods"], "verbs": ["list", "delete"]},
        {"apiGroups": ["batch"], "resources": ["jobs"], "verbs": ["list", "delete"]},
    ]
    app_grants: list[dict[str, Any]] = []
    for name, rules in (
        ("sdo-controller", observer_rules),
        ("sdo-responder", _app_responder_rules(path)),
        ("sdo-controller-helper-cleanup", helper_cleanup_rules),
    ):
        subject = "sdo-controller" if name == "sdo-controller-helper-cleanup" else name
        app_grants.append(
            {
                "apiVersion": "rbac.authorization.k8s.io/v1",
                "kind": "Role",
                "metadata": {"name": name, "namespace": config.namespace},
                "rules": rules,
            }
        )
        app_grants.append(
            {
                "apiVersion": "rbac.authorization.k8s.io/v1",
                "kind": "RoleBinding",
                "metadata": {"name": name, "namespace": config.namespace},
                "subjects": [{"kind": "ServiceAccount", "name": subject, "namespace": control}],
                "roleRef": {"apiGroup": "rbac.authorization.k8s.io", "kind": "Role", "name": name},
            }
        )
    for resource in resources:
        if resource["kind"] == "RoleBinding":
            for subject in resource.get("subjects", []):
                subject["namespace"] = control
    return [*resources, *app_grants]


def _app_responder_rules(path: Path) -> list[dict[str, Any]]:
    documents = [document for document in yaml.safe_load_all(path.read_text(encoding="utf-8")) if document]
    role = next(
        document
        for document in documents
        if document.get("kind") == "Role" and document.get("metadata", {}).get("name") == "sdo-responder"
    )
    return role["rules"]


def _wait_for_controller_job(config: ControllerInstallConfig) -> None:
    deadline = time.monotonic() + config.timeout_seconds + 300
    while time.monotonic() < deadline:
        completed = kubectl(
            ["get", f"job/{CONTROLLER_JOB_NAME}", "-o", "json"],
            namespace=config.control_namespace,
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
        namespace=config.control_namespace,
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
        "metadata": {"name": config.credentials_secret, "namespace": config.control_namespace},
        "type": "Opaque",
        "stringData": secret_data,
    }
    kubectl(["apply", "-f", "-"], namespace=config.control_namespace, input_text=yaml.safe_dump(secret))


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
            config.control_namespace,
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
            config.control_namespace,
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
    namespace: str | None,
    input_text: str | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run kubectl; ``namespace=None`` applies documents that carry their own namespaces."""

    namespace_args = ["--namespace", namespace] if namespace is not None else []
    completed = subprocess.run(
        ["kubectl", *namespace_args, *args],
        input=input_text,
        check=False,
        capture_output=True,
        text=True,
    )
    if check and completed.returncode != 0:
        details = completed.stderr.strip() or completed.stdout.strip()
        raise ControllerInstallError(f"kubectl {' '.join(args)} failed: {details}")
    return completed
