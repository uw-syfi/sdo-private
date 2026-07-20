from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Protocol

from sdo.operational_memory.models import ValidatorNetworkPolicyCanary


@dataclass(frozen=True)
class SandboxResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    network_policy_canaries: tuple[ValidatorNetworkPolicyCanary, ...] = ()


class SandboxRunner(Protocol):
    def run(self, app_root: Path) -> SandboxResult: ...


CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


_CONTAINER_CLEANUP_WATCHDOG = r"""
import os
import subprocess
import sys
import time

parent_pid = int(sys.argv[1])
runtime = sys.argv[2]
container_name = sys.argv[3]
while True:
    try:
        os.kill(parent_pid, 0)
    except ProcessLookupError:
        break
    time.sleep(0.25)
for _attempt in range(20):
    completed = subprocess.run(
        [runtime, "rm", "--force", container_name],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if completed.returncode == 0:
        break
    time.sleep(0.25)
"""


class ContainerSandboxRunner:
    """Run untrusted detector compilation in a locked-down OCI container."""

    def __init__(
        self,
        *,
        image: str = "sdo-detector-validator:v0.1.0",
        runtime: str | None = None,
        timeout_seconds: int = 600,
        cpu_limit: str = "1",
        memory_limit: str = "3g",
        command_runner: CommandRunner | None = None,
    ) -> None:
        self.image = image
        self.runtime = runtime or shutil.which("docker") or shutil.which("podman") or "docker"
        self.timeout_seconds = timeout_seconds
        self.cpu_limit = cpu_limit
        self.memory_limit = memory_limit
        self.command_runner = command_runner

    def run(self, app_root: Path) -> SandboxResult:
        root = app_root.resolve()
        container_name = f"sdo-detector-validator-{os.getpid()}-{uuid.uuid4().hex[:12]}"
        command = [
            self.runtime,
            "run",
            "--rm",
            "--name",
            container_name,
            "--network",
            "none",
            "--read-only",
            "--cpus",
            self.cpu_limit,
            "--memory",
            self.memory_limit,
            "--pids-limit",
            "256",
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "--security-opt",
            "no-new-privileges",
            "--cap-drop",
            "ALL",
            "--volume",
            f"{root}:/workspace:ro",
            "--tmpfs",
            "/tmp:rw,exec,nosuid,nodev,size=3g",
            self.image,
            "env",
            "-i",
            "PATH=/usr/local/go/bin:/usr/local/bin:/usr/bin:/bin",
            "HOME=/tmp",
            "GOCACHE=/tmp/go-cache",
            "GOMODCACHE=/go/pkg/mod",
            "GOPROXY=off",
            "GOSUMDB=off",
            "GOMAXPROCS=2",
            "GOFLAGS=-p=2",
            "PYTHONDONTWRITEBYTECODE=1",
            "python",
            "-m",
            "controller.builder.check_cli",
            "test",
            "--app",
            "/workspace",
        ]
        try:
            if self.command_runner is None:
                completed = self._run_managed_container(command, container_name)
            else:
                completed = self.command_runner(
                    command,
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_seconds,
                    env={"PATH": os.environ.get("PATH", "")},
                )
        except subprocess.TimeoutExpired as exc:
            return SandboxResult(
                returncode=124,
                stdout=_timeout_text(exc.stdout),
                stderr=_timeout_text(exc.stderr) or "detector sandbox timed out",
                timed_out=True,
            )
        return SandboxResult(
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )

    def _run_managed_container(
        self,
        command: list[str],
        container_name: str,
    ) -> subprocess.CompletedProcess[str]:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env={"PATH": os.environ.get("PATH", "")},
            start_new_session=True,
        )
        watchdog = self._start_cleanup_watchdog(container_name)
        previous_sigterm: signal.Handlers | None = None
        sigterm_handler_installed = False

        def terminate_after_cleanup(signum: int, _frame: object) -> None:
            raise SystemExit(128 + signum)

        try:
            previous_sigterm = signal.signal(signal.SIGTERM, terminate_after_cleanup)
            sigterm_handler_installed = True
        except ValueError:
            # Signal handlers can only be installed by the main thread. The
            # process-group timeout cleanup still applies in worker threads.
            pass
        try:
            try:
                stdout, stderr = process.communicate(timeout=self.timeout_seconds)
            except subprocess.TimeoutExpired:
                self._kill_container_process(process, container_name)
                stdout, stderr = process.communicate()
                raise subprocess.TimeoutExpired(
                    command,
                    self.timeout_seconds,
                    output=stdout,
                    stderr=stderr,
                ) from None
            except BaseException:
                self._kill_container_process(process, container_name)
                raise
        finally:
            if sigterm_handler_installed and previous_sigterm is not None:
                signal.signal(signal.SIGTERM, previous_sigterm)
            self._stop_cleanup_watchdog(watchdog)
        return subprocess.CompletedProcess(command, process.returncode, stdout=stdout, stderr=stderr)

    def _start_cleanup_watchdog(self, container_name: str) -> subprocess.Popen[str]:
        return subprocess.Popen(
            [sys.executable, "-c", _CONTAINER_CLEANUP_WATCHDOG, str(os.getpid()), self.runtime, container_name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
            env={"PATH": os.environ.get("PATH", "")},
            start_new_session=True,
        )

    @staticmethod
    def _stop_cleanup_watchdog(watchdog: subprocess.Popen[str]) -> None:
        watchdog.terminate()
        try:
            watchdog.wait(timeout=5)
        except subprocess.TimeoutExpired:
            watchdog.kill()
            watchdog.wait(timeout=5)

    def _kill_container_process(self, process: subprocess.Popen[str], container_name: str) -> None:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        subprocess.run(
            [self.runtime, "rm", "--force", container_name],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
            env={"PATH": os.environ.get("PATH", "")},
        )


class KubernetesJobSandboxRunner:
    """Validate generated diagnostics in an isolated, network-denied Kubernetes Job."""

    def __init__(
        self,
        *,
        namespace: str,
        image: str,
        repository_pvc: str,
        repository_mount_path: Path = Path("/workspace"),
        timeout_seconds: int = 600,
        poll_interval_seconds: float = 1,
        command_runner: CommandRunner = subprocess.run,
    ) -> None:
        if not namespace or not image or not repository_pvc:
            raise ValueError("validator namespace, image, and repository PVC are required")
        if timeout_seconds <= 0 or poll_interval_seconds < 0:
            raise ValueError("validator timeout must be positive and poll interval non-negative")
        self.namespace = namespace
        self.image = image
        self.repository_pvc = repository_pvc
        self.repository_mount_path = repository_mount_path.resolve()
        self.timeout_seconds = timeout_seconds
        self.poll_interval_seconds = poll_interval_seconds
        self.command_runner = command_runner

    def run(self, app_root: Path) -> SandboxResult:
        root = app_root.resolve()
        try:
            repository_subpath = root.relative_to(self.repository_mount_path).as_posix()
        except ValueError:
            return SandboxResult(
                returncode=125,
                stderr=f"validator worktree is outside shared repository mount: {root}",
            )
        if not repository_subpath or repository_subpath == ".":
            return SandboxResult(returncode=125, stderr="validator requires a repository PVC subpath")

        run_name = f"sdo-validator-{self._validation_digest(root)}"
        policy = self._network_policy(run_name)
        job = self._job(run_name, repository_subpath)
        allow_canary = self._network_canary_job(run_name, expect_denied=False)
        deny_canary = self._network_canary_job(run_name, expect_denied=True)
        canary_evidence: list[ValidatorNetworkPolicyCanary] = []
        try:
            # Prove both sides of NetworkPolicy enforcement before exposing
            # untrusted detector code to the production validator pod. The
            # positive control rules out a broken API target; the selected
            # negative canary rules out a CNI that silently ignores policy.
            for resource in (allow_canary,):
                created = self._kubectl(
                    ["create", "-f", "-"],
                    input_text=json.dumps(resource, separators=(",", ":")),
                    timeout_seconds=30,
                )
                if created.returncode != 0 and "AlreadyExists" not in created.stderr:
                    details = created.stderr.strip() or created.stdout.strip() or "resource creation failed"
                    return SandboxResult(returncode=125, stderr=details)
            allow_result = self._wait_for_job(f"{run_name}-allow")
            if allow_result.returncode != 0:
                canary_evidence.append(self._canary_evidence("allow", run_name, False, allow_result))
                return SandboxResult(
                    returncode=125,
                    stderr=f"network-policy preflight could not reach Kubernetes API: {allow_result.stderr}",
                    network_policy_canaries=tuple(canary_evidence),
                )
            canary_evidence.append(self._canary_evidence("allow", run_name, True, allow_result))
            for resource in (policy, deny_canary):
                created = self._kubectl(
                    ["create", "-f", "-"],
                    input_text=json.dumps(resource, separators=(",", ":")),
                    timeout_seconds=30,
                )
                if created.returncode != 0 and "AlreadyExists" not in created.stderr:
                    details = created.stderr.strip() or created.stdout.strip() or "resource creation failed"
                    return SandboxResult(returncode=125, stderr=details)
            deny_result = self._wait_for_job(f"{run_name}-deny")
            if deny_result.returncode != 0:
                canary_evidence.append(self._canary_evidence("deny", run_name, False, deny_result))
                return SandboxResult(
                    returncode=125,
                    stderr=f"network-policy egress denial was not enforced: {deny_result.stderr}",
                    network_policy_canaries=tuple(canary_evidence),
                )
            canary_evidence.append(self._canary_evidence("deny", run_name, True, deny_result))
            created = self._kubectl(
                ["create", "-f", "-"],
                input_text=json.dumps(job, separators=(",", ":")),
                timeout_seconds=30,
            )
            if created.returncode != 0 and "AlreadyExists" not in created.stderr:
                details = created.stderr.strip() or created.stdout.strip() or "resource creation failed"
                return SandboxResult(returncode=125, stderr=details)
            validation_result = self._wait_for_job(run_name)
            return SandboxResult(
                returncode=validation_result.returncode,
                stdout=validation_result.stdout,
                stderr=validation_result.stderr,
                timed_out=validation_result.timed_out,
                network_policy_canaries=tuple(canary_evidence),
            )
        finally:
            self._kubectl(
                [
                    "delete",
                    f"job/{run_name}",
                    f"job/{run_name}-allow",
                    f"job/{run_name}-deny",
                    f"networkpolicy/{run_name}",
                    "--ignore-not-found=true",
                    "--wait=false",
                ],
                timeout_seconds=30,
            )

    @staticmethod
    def _canary_evidence(
        mode: Literal["allow", "deny"],
        run_name: str,
        passed: bool,
        result: SandboxResult,
    ) -> ValidatorNetworkPolicyCanary:
        details = result.stdout.strip() or result.stderr.strip()
        if not details:
            details = "network-policy canary completed" if passed else "network-policy canary failed"
        return ValidatorNetworkPolicyCanary(
            mode=mode,
            passed=passed,
            job_name=f"{run_name}-{mode}",
            observed_at=datetime.now(timezone.utc),
            details=details,
        )

    def _wait_for_job(self, run_name: str) -> SandboxResult:
        deadline = time.monotonic() + self.timeout_seconds
        while time.monotonic() < deadline:
            observed = self._kubectl(
                ["get", f"job/{run_name}", "-o", "json"],
                timeout_seconds=30,
            )
            if observed.returncode != 0:
                details = observed.stderr.strip() or observed.stdout.strip()
                return SandboxResult(returncode=125, stderr=details or "validator Job disappeared")
            try:
                status = json.loads(observed.stdout).get("status", {})
            except json.JSONDecodeError as exc:
                return SandboxResult(returncode=125, stderr=f"invalid validator Job status: {exc}")
            if int(status.get("succeeded", 0) or 0) > 0:
                return SandboxResult(returncode=0, stdout=f"validator Job {run_name} completed")
            if int(status.get("failed", 0) or 0) > 0:
                details = self._failure_message(run_name)
                return SandboxResult(
                    returncode=1,
                    stderr=details or f"validator Job {run_name} failed",
                )
            if self.poll_interval_seconds:
                time.sleep(self.poll_interval_seconds)
        return SandboxResult(
            returncode=124,
            stderr=f"validator Job {run_name} timed out",
            timed_out=True,
        )

    def _failure_message(self, run_name: str) -> str:
        observed = self._kubectl(
            [
                "get",
                "pods",
                "--selector",
                f"job-name={run_name}",
                "-o",
                "json",
            ],
            timeout_seconds=30,
        )
        if observed.returncode != 0:
            return ""
        try:
            pods = json.loads(observed.stdout).get("items", [])
        except json.JSONDecodeError:
            return ""
        for pod in pods:
            if not isinstance(pod, dict):
                continue
            statuses = pod.get("status", {}).get("containerStatuses", [])
            if not isinstance(statuses, list):
                continue
            for status in statuses:
                if not isinstance(status, dict):
                    continue
                terminated = status.get("state", {}).get("terminated", {})
                if not isinstance(terminated, dict):
                    continue
                message = terminated.get("message")
                if isinstance(message, str) and message.strip():
                    return message.strip()
        return ""

    def _kubectl(
        self,
        arguments: list[str],
        *,
        input_text: str | None = None,
        timeout_seconds: int,
    ) -> subprocess.CompletedProcess[str]:
        command = ["kubectl", "--namespace", self.namespace, *arguments]
        service_account_root = Path("/var/run/secrets/kubernetes.io/serviceaccount")
        token_path = service_account_root / "token"
        host = os.environ.get("KUBERNETES_SERVICE_HOST", "")
        port = os.environ.get("KUBERNETES_SERVICE_PORT_HTTPS", "443")
        if token_path.is_file() and host:
            kubeconfig = {
                "apiVersion": "v1",
                "kind": "Config",
                "clusters": [
                    {
                        "name": "in-cluster",
                        "cluster": {
                            "server": f"https://{host}:{port}",
                            "certificate-authority": str(service_account_root / "ca.crt"),
                        },
                    }
                ],
                "contexts": [
                    {
                        "name": "in-cluster",
                        "context": {"cluster": "in-cluster", "user": "controller"},
                    }
                ],
                "current-context": "in-cluster",
                "users": [
                    {
                        "name": "controller",
                        "user": {"token": token_path.read_text(encoding="utf-8").strip()},
                    }
                ],
            }
            with tempfile.NamedTemporaryFile(mode="w", prefix="sdo-kubeconfig-", suffix=".json") as stream:
                json.dump(kubeconfig, stream)
                stream.flush()
                return self.command_runner(
                    [command[0], "--kubeconfig", stream.name, *command[1:]],
                    check=False,
                    capture_output=True,
                    text=True,
                    input=input_text,
                    timeout=timeout_seconds,
                    env={"PATH": os.environ.get("PATH", ""), "HOME": "/tmp"},
                )
        return self.command_runner(
            command,
            check=False,
            capture_output=True,
            text=True,
            input=input_text,
            timeout=timeout_seconds,
            env={"PATH": os.environ.get("PATH", ""), "HOME": "/tmp"},
        )

    @staticmethod
    def _validation_digest(root: Path) -> str:
        digest = hashlib.sha256(str(root).encode())
        memory_root = root / ".sdo"
        if memory_root.is_dir():
            for path in sorted(memory_root.rglob("*")):
                relative = path.relative_to(root).as_posix()
                digest.update(relative.encode())
                digest.update(b"\0")
                if path.is_symlink():
                    digest.update(os.readlink(path).encode())
                elif path.is_file():
                    digest.update(path.read_bytes())
                digest.update(b"\0")
        return digest.hexdigest()[:16]

    def _network_policy(self, run_name: str) -> dict[str, object]:
        return {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "NetworkPolicy",
            "metadata": {"name": run_name, "namespace": self.namespace},
            "spec": {
                "podSelector": {"matchLabels": {"sdo.dev/validator-isolation": run_name}},
                "policyTypes": ["Ingress", "Egress"],
                "ingress": [],
                "egress": [],
            },
        }

    def _job(self, run_name: str, repository_subpath: str) -> dict[str, object]:
        return {
            "apiVersion": "batch/v1",
            "kind": "Job",
            "metadata": {"name": run_name, "namespace": self.namespace},
            "spec": {
                "backoffLimit": 0,
                "activeDeadlineSeconds": self.timeout_seconds,
                "ttlSecondsAfterFinished": 300,
                "template": {
                    "metadata": {"labels": {"sdo.dev/validator-isolation": run_name}},
                    "spec": {
                        "restartPolicy": "Never",
                        "automountServiceAccountToken": False,
                        "enableServiceLinks": False,
                        "terminationGracePeriodSeconds": 5,
                        "securityContext": {
                            "runAsNonRoot": True,
                            "runAsUser": 65532,
                            "seccompProfile": {"type": "RuntimeDefault"},
                        },
                        "containers": [
                            {
                                "name": "validator",
                                "image": self.image,
                                "imagePullPolicy": "IfNotPresent",
                                "command": ["python", "-m", "controller.builder.check_cli"],
                                "args": ["test", "--app", "/workspace"],
                                "workingDir": "/workspace",
                                "terminationMessagePolicy": "FallbackToLogsOnError",
                                "env": [
                                    {"name": "HOME", "value": "/tmp"},
                                    {"name": "GOCACHE", "value": "/tmp/go-cache"},
                                    {"name": "GOMODCACHE", "value": "/go/pkg/mod"},
                                    {"name": "GOPROXY", "value": "off"},
                                    {"name": "GOSUMDB", "value": "off"},
                                    {"name": "GOMAXPROCS", "value": "1"},
                                    {"name": "GOFLAGS", "value": "-p=1"},
                                    {"name": "PYTHONDONTWRITEBYTECODE", "value": "1"},
                                ],
                                "securityContext": {
                                    "allowPrivilegeEscalation": False,
                                    "readOnlyRootFilesystem": True,
                                    "capabilities": {"drop": ["ALL"]},
                                },
                                "resources": {
                                    "requests": {"cpu": "250m", "memory": "512Mi"},
                                    "limits": {
                                        "cpu": "500m",
                                        "memory": "3Gi",
                                        "ephemeral-storage": "4Gi",
                                    },
                                },
                                "volumeMounts": [
                                    {
                                        "name": "application",
                                        "mountPath": "/workspace",
                                        "subPath": repository_subpath,
                                        "readOnly": True,
                                    },
                                    {"name": "scratch", "mountPath": "/tmp"},
                                ],
                            }
                        ],
                        "volumes": [
                            {
                                "name": "application",
                                "persistentVolumeClaim": {
                                    "claimName": self.repository_pvc,
                                    "readOnly": True,
                                },
                            },
                            {
                                "name": "scratch",
                                "emptyDir": {"sizeLimit": "3Gi"},
                            },
                        ],
                    },
                },
            },
        }

    def _network_canary_job(self, run_name: str, *, expect_denied: bool) -> dict[str, object]:
        mode = "deny" if expect_denied else "allow"
        if expect_denied:
            script = """import os, socket, sys, time
host = os.environ.get("KUBERNETES_SERVICE_HOST")
port = os.environ.get("KUBERNETES_SERVICE_PORT_HTTPS", "443")
if not host:
    sys.stderr.write("KUBERNETES_SERVICE_HOST is unavailable\\n")
    raise SystemExit(2)
time.sleep(2)
for _ in range(3):
    try:
        connection = socket.create_connection((host, int(port)), timeout=1)
    except OSError:
        continue
    connection.close()
    sys.stderr.write("egress unexpectedly reachable despite default-deny NetworkPolicy\\n")
    raise SystemExit(3)
print("default-deny egress enforced")
"""
        else:
            script = """import os, socket, sys, time
host = os.environ.get("KUBERNETES_SERVICE_HOST")
port = os.environ.get("KUBERNETES_SERVICE_PORT_HTTPS", "443")
if not host:
    sys.stderr.write("KUBERNETES_SERVICE_HOST is unavailable\\n")
    raise SystemExit(2)
deadline = time.monotonic() + 10
while time.monotonic() < deadline:
    try:
        connection = socket.create_connection((host, int(port)), timeout=1)
    except OSError:
        time.sleep(.25)
        continue
    connection.close()
    print("Kubernetes API positive control reachable")
    raise SystemExit(0)
sys.stderr.write("Kubernetes API positive control unreachable\\n")
raise SystemExit(4)
"""
        pod_labels = {"sdo.dev/network-canary": mode}
        if expect_denied:
            pod_labels["sdo.dev/validator-isolation"] = run_name
        return {
            "apiVersion": "batch/v1",
            "kind": "Job",
            "metadata": {
                "name": f"{run_name}-{mode}",
                "namespace": self.namespace,
                "labels": {"sdo.dev/network-canary": mode},
            },
            "spec": {
                "backoffLimit": 0,
                "activeDeadlineSeconds": 30,
                "ttlSecondsAfterFinished": 300,
                "template": {
                    "metadata": {"labels": pod_labels},
                    "spec": {
                        "restartPolicy": "Never",
                        "automountServiceAccountToken": False,
                        "enableServiceLinks": False,
                        "securityContext": {
                            "runAsNonRoot": True,
                            "runAsUser": 65532,
                            "seccompProfile": {"type": "RuntimeDefault"},
                        },
                        "containers": [
                            {
                                "name": "network-canary",
                                "image": self.image,
                                "imagePullPolicy": "IfNotPresent",
                                "command": ["python", "-c", script],
                                "terminationMessagePolicy": "FallbackToLogsOnError",
                                "securityContext": {
                                    "allowPrivilegeEscalation": False,
                                    "readOnlyRootFilesystem": True,
                                    "capabilities": {"drop": ["ALL"]},
                                },
                                "resources": {
                                    "requests": {"cpu": "10m", "memory": "32Mi"},
                                    "limits": {"cpu": "100m", "memory": "64Mi"},
                                },
                            }
                        ],
                    },
                },
            },
        }


class LocalSandboxRunner:
    """Explicit development-only runner; production uses ContainerSandboxRunner."""

    def __init__(self, *, timeout_seconds: int = 120, command_runner: CommandRunner = subprocess.run) -> None:
        self.timeout_seconds = timeout_seconds
        self.command_runner = command_runner

    def run(self, app_root: Path) -> SandboxResult:
        with tempfile.TemporaryDirectory(prefix="sdo-validator-cache-") as temp_dir:
            cache_root = Path(temp_dir)
            environment = {
                "PATH": os.environ.get("PATH", ""),
                "HOME": str(cache_root),
                "GOCACHE": str(cache_root / "go-build"),
                "GOMODCACHE": os.environ.get("GOMODCACHE", str(cache_root / "go-mod")),
                "PYTHONPATH": os.environ.get("PYTHONPATH", ""),
                "PYTHONDONTWRITEBYTECODE": "1",
            }
            for name in ("GOPROXY", "GOSUMDB", "GOMAXPROCS", "GOFLAGS"):
                if value := os.environ.get(name):
                    environment[name] = value
            try:
                completed = self.command_runner(
                    [sys.executable, "-m", "controller.builder.check_cli", "test", "--app", str(app_root.resolve())],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_seconds,
                    env=environment,
                )
            except subprocess.TimeoutExpired as exc:
                return SandboxResult(
                    returncode=124,
                    stdout=_timeout_text(exc.stdout),
                    stderr=_timeout_text(exc.stderr) or "local detector validation timed out",
                    timed_out=True,
                )
        return SandboxResult(
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )


def _timeout_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return value
