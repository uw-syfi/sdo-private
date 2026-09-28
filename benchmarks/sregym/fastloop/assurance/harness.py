"""Run SDO's production controller against a warm kind lane, with no LLM anywhere.

The controller is the per-application binary the production builder compiles
from the seed's ``.sdo`` (its three health detectors and the health judge's
traffic generators), started through the production ``run.go`` path. Only the
two agent seams are replaced:

* the responder is a scripted local-mode dispatcher
  (:mod:`benchmarks.sregym.fastloop.assurance.responder`) that hands each
  incident request to the suite and returns the result the suite writes; and
* the broker is the production commit broker with reflection disabled
  (:mod:`benchmarks.sregym.fastloop.assurance.broker`).

The prober runs as an isolated pod in the control namespace, as in
production; the controller, the suite and ``sdo incident status`` reach it
through a supervised ``kubectl port-forward``.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import IO, Any

from controller.builder.go_runner import GoRunner
from controller.builder.paths import find_tool_paths
from controller.builder.workspace import BuildWorkspace, BuildWorkspaceConfig

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[4]
PROBER_NAME = "sdo-prober"
PROBER_PORT = 8080
PROBER_BINARY_DIR = ".sdo-prober"
REPOSITORY_PVC = "sdo-application-repository"
STATE_CONFIGMAP = "sdo-controller-state"
HELPER_LABEL = "sdo.dev/responder-helper"


class HarnessError(RuntimeError):
    """Raised when the assurance harness cannot bring up or observe the controller."""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class Kubectl:
    kubeconfig: Path

    def run(
        self,
        *args: str,
        namespace: str | None = None,
        stdin: str | None = None,
        check: bool = True,
        timeout: float = 120.0,
    ) -> subprocess.CompletedProcess[str]:
        command = ["kubectl", "--kubeconfig", str(self.kubeconfig)]
        if namespace is not None:
            command += ["--namespace", namespace]
        completed = subprocess.run(
            [*command, *args], input=stdin, capture_output=True, text=True, timeout=timeout, check=False
        )
        if check and completed.returncode != 0:
            raise HarnessError(
                f"kubectl {' '.join(args)} failed: {completed.stderr.strip() or completed.stdout.strip()}"
            )
        return completed

    def json(self, *args: str, namespace: str | None = None) -> dict[str, Any]:
        output = self.run(*args, "--output", "json", namespace=namespace).stdout
        decoded = json.loads(output)
        if not isinstance(decoded, dict):
            raise HarnessError(f"kubectl {' '.join(args)} did not return an object")
        return decoded

    def apply(self, manifest: dict[str, Any]) -> None:
        self.run("apply", "--filename", "-", stdin=json.dumps(manifest))


@dataclass(frozen=True)
class Layout:
    """Everything one assurance run writes, under ``<run-dir>/assurance``."""

    root: Path

    @property
    def bin_dir(self) -> Path:
        return self.root / "bin"

    @property
    def spool(self) -> Path:
        return self.root / "spool"

    @property
    def worktrees(self) -> Path:
        return self.root / "worktrees"

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    def create(self) -> Layout:
        for directory in (self.bin_dir, self.spool, self.worktrees, self.logs):
            directory.mkdir(parents=True, exist_ok=True)
        return self


@dataclass(frozen=True)
class Binaries:
    controller: Path
    prober: Path

    @property
    def prober_digest(self) -> str:
        return hashlib.sha256(self.prober.read_bytes()).hexdigest()[:16]


def build_binaries(app_root: Path, out_dir: Path) -> Binaries:
    """Compile the controller and the prober exactly as the production builder does."""

    tools = find_tool_paths()
    runner = GoRunner.from_environment()
    out_dir.mkdir(parents=True, exist_ok=True)
    controller = out_dir / "sdo-controller"
    prober = out_dir / "sdo-prober"
    with BuildWorkspace.create(
        BuildWorkspaceConfig(
            app_root=app_root, sdk_dir=tools.sdk_dir, core_dir=tools.core_dir, runtime_dir=tools.runtime_dir
        )
    ) as workspace:
        if not workspace.has_prober:
            raise HarnessError(f"{app_root} has no traffic generators; the assurance suite needs its prober")
        steps: list[tuple[list[str], dict[str, str] | None]] = [
            (["mod", "tidy"], None),
            (["build", "-buildvcs=false", "-o", str(controller), "./cmd/controller"], None),
            (["build", "-buildvcs=false", "-o", str(prober), "./cmd/prober"], {"CGO_ENABLED": "0"}),
        ]
        for command, env in steps:
            if runner.run(command, cwd=workspace.path, env=env) != 0:
                raise HarnessError(f"go {' '.join(command)} failed for {app_root}")
    return Binaries(controller=controller, prober=prober)


def prober_manifests(*, namespace: str, app_namespace: str, image: str, digest: str) -> list[dict[str, Any]]:
    """The prober's NetworkPolicy and Pod, as ``controller/runtime.ProberPod.Manifests`` builds them."""

    labels = {"app.kubernetes.io/name": PROBER_NAME, "app.kubernetes.io/managed-by": "sdo"}
    policy = {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "NetworkPolicy",
        "metadata": {"name": PROBER_NAME, "namespace": namespace, "labels": labels},
        "spec": {
            "podSelector": {"matchLabels": {"app.kubernetes.io/name": PROBER_NAME}},
            "policyTypes": ["Ingress", "Egress"],
            "ingress": [{"from": [{"podSelector": {}}]}],
            "egress": [
                {"to": [{"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": app_namespace}}}]},
                {
                    "to": [{"namespaceSelector": {}, "podSelector": {"matchLabels": {"k8s-app": "kube-dns"}}}],
                    "ports": [{"protocol": "UDP", "port": 53}, {"protocol": "TCP", "port": 53}],
                },
            ],
        },
    }
    pod = {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": PROBER_NAME,
            "namespace": namespace,
            "labels": {**labels, "sdo.dev/prober-fingerprint": digest},
        },
        "spec": {
            "automountServiceAccountToken": False,
            "enableServiceLinks": False,
            "restartPolicy": "Always",
            "securityContext": {"runAsNonRoot": True, "runAsUser": 65532, "seccompProfile": {"type": "RuntimeDefault"}},
            "containers": [
                {
                    "name": "prober",
                    "image": image,
                    "command": ["/opt/sdo-prober/sdo-prober"],
                    "args": ["--namespace", app_namespace, "--listen", f":{PROBER_PORT}"],
                    "imagePullPolicy": "IfNotPresent",
                    "ports": [{"name": "api", "containerPort": PROBER_PORT}],
                    "readinessProbe": {"httpGet": {"path": "/healthz", "port": PROBER_PORT}, "periodSeconds": 1},
                    "volumeMounts": [
                        {
                            "name": "prober",
                            "mountPath": "/opt/sdo-prober",
                            "subPath": f"{PROBER_BINARY_DIR}/{digest}",
                            "readOnly": True,
                        }
                    ],
                    "securityContext": {
                        "allowPrivilegeEscalation": False,
                        "readOnlyRootFilesystem": True,
                        "capabilities": {"drop": ["ALL"]},
                    },
                    "resources": {
                        "requests": {"cpu": "25m", "memory": "32Mi"},
                        "limits": {"cpu": "250m", "memory": "128Mi"},
                    },
                }
            ],
            "volumes": [{"name": "prober", "persistentVolumeClaim": {"claimName": REPOSITORY_PVC, "readOnly": True}}],
        },
    }
    return [policy, pod]


def deploy_prober(kubectl: Kubectl, *, namespace: str, app_namespace: str, image: str, binary: Path) -> str:
    """Publish the prober binary on the repository PVC and start the isolated prober pod; returns its digest."""

    digest = hashlib.sha256(binary.read_bytes()).hexdigest()[:16]
    kubectl.apply({"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": namespace}})
    kubectl.apply(
        {
            "apiVersion": "v1",
            "kind": "PersistentVolumeClaim",
            "metadata": {"name": REPOSITORY_PVC, "namespace": namespace},
            "spec": {"accessModes": ["ReadWriteOnce"], "resources": {"requests": {"storage": "1Gi"}}},
        }
    )
    existing = kubectl.run("get", "pod", PROBER_NAME, "--output", "json", namespace=namespace, check=False)
    if existing.returncode == 0:
        labels = json.loads(existing.stdout).get("metadata", {}).get("labels", {})
        if labels.get("sdo.dev/prober-fingerprint") == digest:
            kubectl.run("wait", "--for=condition=Ready", f"pod/{PROBER_NAME}", "--timeout=120s", namespace=namespace)
            return digest
        kubectl.run("delete", "pod", PROBER_NAME, "--wait=true", "--grace-period=0", namespace=namespace)
    loader = "sdo-assurance-prober-loader"
    kubectl.run("delete", "pod", loader, "--ignore-not-found=true", "--wait=true", namespace=namespace)
    kubectl.apply(
        {
            "apiVersion": "v1",
            "kind": "Pod",
            "metadata": {"name": loader, "namespace": namespace},
            "spec": {
                "restartPolicy": "Never",
                "containers": [
                    {
                        "name": "loader",
                        "image": image,
                        "imagePullPolicy": "IfNotPresent",
                        "command": ["/usr/bin/sleep", "3600"],
                        "volumeMounts": [{"name": "repository", "mountPath": "/repository"}],
                    }
                ],
                "volumes": [{"name": "repository", "persistentVolumeClaim": {"claimName": REPOSITORY_PVC}}],
            },
        }
    )
    kubectl.run("wait", "--for=condition=Ready", f"pod/{loader}", "--timeout=180s", namespace=namespace, timeout=200)
    target = f"/repository/{PROBER_BINARY_DIR}/{digest}"
    kubectl.run("exec", loader, "--", "/usr/bin/mkdir", "-p", target, namespace=namespace)
    kubectl.run("cp", str(binary), f"{namespace}/{loader}:{target}/sdo-prober", timeout=300)
    kubectl.run("exec", loader, "--", "/usr/bin/chmod", "0555", f"{target}/sdo-prober", namespace=namespace)
    kubectl.run("delete", "pod", loader, "--wait=true", "--grace-period=0", namespace=namespace)
    for manifest in prober_manifests(namespace=namespace, app_namespace=app_namespace, image=image, digest=digest):
        kubectl.apply(manifest)
    kubectl.run(
        "wait", "--for=condition=Ready", f"pod/{PROBER_NAME}", "--timeout=180s", namespace=namespace, timeout=200
    )
    return digest


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


class PortForward:
    """A supervised ``kubectl port-forward`` to the prober pod; restarts are counted, not hidden."""

    def __init__(self, kubectl: Kubectl, *, namespace: str, log_path: Path, port: int | None = None) -> None:
        self._kubectl = kubectl
        self._namespace = namespace
        self._log_path = log_path
        self.port = port or free_port()
        self.restarts = 0
        self._stop = threading.Event()
        self._process: subprocess.Popen[str] | None = None
        self._thread = threading.Thread(target=self._supervise, name="prober-port-forward", daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self, *, ready_timeout: float = 30.0) -> PortForward:
        self._thread.start()
        deadline = time.monotonic() + ready_timeout
        while time.monotonic() < deadline:
            with contextlib.suppress(OSError), socket.create_connection(("127.0.0.1", self.port), timeout=1):
                return self
            time.sleep(0.2)
        raise HarnessError(f"port-forward to {PROBER_NAME} never listened on {self.port}")

    def _supervise(self) -> None:
        with self._log_path.open("a", encoding="utf-8") as log:
            while not self._stop.is_set():
                self._process = subprocess.Popen(
                    [
                        "kubectl",
                        "--kubeconfig",
                        str(self._kubectl.kubeconfig),
                        "--namespace",
                        self._namespace,
                        "port-forward",
                        f"pod/{PROBER_NAME}",
                        f"{self.port}:{PROBER_PORT}",
                    ],
                    stdout=log,
                    stderr=log,
                    text=True,
                )
                self._process.wait()
                if self._stop.is_set():
                    return
                self.restarts += 1
                log.write(f"{utcnow().isoformat()} port-forward exited ({self._process.returncode}); restarting\n")
                log.flush()
                time.sleep(0.5)

    def stop(self) -> None:
        self._stop.set()
        if self._process is not None and self._process.poll() is None:
            self._process.terminate()
            with contextlib.suppress(subprocess.TimeoutExpired):
                self._process.wait(timeout=5)
        self._thread.join(timeout=10)


@dataclass
class ControllerEvent:
    at: datetime
    record: dict[str, Any]


@dataclass
class ControllerSettings:
    binary: Path
    app_root: Path
    namespace: str
    control_namespace: str
    application: str
    prober_url: str
    spool: Path
    worktrees: Path
    logs: Path
    kubeconfig: Path
    response_timeout: str = "20m"
    verification_timeout: str = "2m"
    extra_env: dict[str, str] = field(default_factory=dict)


def controller_argv(settings: ControllerSettings, *, python: str = sys.executable) -> list[str]:
    """The production controller's flags with the scripted responder and the no-LLM broker."""

    return [
        str(settings.binary),
        "--namespace",
        settings.namespace,
        "--control-namespace",
        settings.control_namespace,
        "--app-root",
        str(settings.app_root),
        "--application",
        settings.application,
        "--dispatcher-mode",
        "local",
        "--dispatcher",
        python,
        "--dispatcher-arg=-m",
        "--dispatcher-arg=benchmarks.sregym.fastloop.assurance.responder",
        f"--dispatcher-arg=--spool={settings.spool}",
        "--broker",
        python,
        "--broker-arg=-m",
        "--broker-arg=benchmarks.sregym.fastloop.assurance.broker",
        "--broker-worktree-root",
        str(settings.worktrees),
        "--repair-policy",
        "recorded-actions",
        "--prober-url",
        settings.prober_url,
        "--response-timeout",
        settings.response_timeout,
        "--verification-timeout",
        settings.verification_timeout,
    ]


class ControllerProcess:
    """The controller subprocess; every stdout JSON record is kept with the time it was read."""

    def __init__(self, settings: ControllerSettings) -> None:
        self.settings = settings
        self._process: subprocess.Popen[str] | None = None
        self._events: list[ControllerEvent] = []
        self._lock = threading.Lock()
        self._events_log: IO[str] | None = None
        self._stderr_log: IO[str] | None = None
        self.started_at: datetime | None = None

    def start(self) -> ControllerProcess:
        settings = self.settings
        self._events_log = (settings.logs / "controller.events.jsonl").open("a", encoding="utf-8")
        self._stderr_log = (settings.logs / "controller.stderr.log").open("a", encoding="utf-8")
        env = {
            **os.environ,
            "KUBECONFIG": str(settings.kubeconfig),
            "PYTHONPATH": os.pathsep.join(filter(None, [str(REPO_ROOT), os.environ.get("PYTHONPATH", "")])),
            **settings.extra_env,
        }
        self.started_at = utcnow()
        self._process = subprocess.Popen(
            controller_argv(settings),
            cwd=REPO_ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=self._stderr_log,
            text=True,
            bufsize=1,
        )
        threading.Thread(target=self._read, name="controller-events", daemon=True).start()
        return self

    def _read(self) -> None:
        if self._process is None or self._process.stdout is None:
            raise HarnessError("controller output is not piped")
        for line in self._process.stdout:
            at = utcnow()
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                record = {"raw": line.rstrip("\n")}
            if not isinstance(record, dict):
                record = {"raw": record}
            with self._lock:
                self._events.append(ControllerEvent(at=at, record=record))
            if self._events_log is not None:
                self._events_log.write(json.dumps({"at": at.isoformat(), **record}, default=str) + "\n")
                self._events_log.flush()

    @property
    def pid(self) -> int:
        if self._process is None:
            raise HarnessError("controller is not running")
        return self._process.pid

    def alive(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def events(self, since: datetime | None = None) -> list[ControllerEvent]:
        with self._lock:
            return [event for event in self._events if since is None or event.at >= since]

    def wait_for(self, predicate: Any, *, timeout: float, since: datetime | None = None) -> ControllerEvent:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for event in self.events(since):
                if predicate(event.record):
                    return event
            if not self.alive():
                raise HarnessError(f"controller exited ({self._process.returncode if self._process else '?'})")
            time.sleep(0.1)
        raise HarnessError(f"no controller event matched within {timeout:.0f}s")

    def stop(self) -> None:
        process = self._process
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        for log in (self._events_log, self._stderr_log):
            if log is not None:
                log.close()


def controller_state(kubectl: Kubectl, control_namespace: str) -> dict[str, Any]:
    configmap = kubectl.json("get", "configmap", STATE_CONFIGMAP, namespace=control_namespace)
    payload = configmap.get("data", {}).get("runtime-state.json")
    if not isinstance(payload, str):
        raise HarnessError(f"{STATE_CONFIGMAP} has no runtime state")
    decoded = json.loads(payload)
    if not isinstance(decoded, dict):
        raise HarnessError("controller runtime state is not an object")
    return decoded


def broker_ledger(app_root: Path, incident_id: str) -> dict[str, Any] | None:
    # BrokerService names each ledger by the SHA-256 of its incident id.
    digest = hashlib.sha256(incident_id.encode()).hexdigest()
    path = app_root / ".git" / "sdo-broker" / f"{digest}.json"
    if not path.is_file():
        return None
    decoded = json.loads(path.read_text(encoding="utf-8"))
    return decoded if isinstance(decoded, dict) else None


def clean_directory(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)
