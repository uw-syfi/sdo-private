from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import cast

import pytest

from sdo.agent_runtime.lifecycle.deployment import (
    CodexDeploymentBackend,
    DeploymentAttempt,
    DeploymentError,
    DeploymentVerification,
    deploy_from_source,
)


def _git(repository: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repository), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _repository(root: Path) -> Path:
    repository = root / "application"
    repository.mkdir()
    _git(repository, "init", "-q")
    _git(repository, "config", "user.name", "Deployment Test")
    _git(repository, "config", "user.email", "deployment-test@localhost")
    (repository / "README.md").write_text("# Example\n", encoding="utf-8")
    _git(repository, "add", "README.md")
    _git(repository, "commit", "-q", "-m", "application source")
    return repository


def _commit_deployment(repository: Path, revision: int) -> str:
    deployment_dir = repository / "deploy/kubernetes"
    deployment_dir.mkdir(parents=True, exist_ok=True)
    (deployment_dir / "application.yaml").write_text(
        f"apiVersion: apps/v1\nkind: Deployment\nmetadata: {{name: example-{revision}}}\n",
        encoding="utf-8",
    )
    _git(repository, "add", "deploy/kubernetes/application.yaml")
    _git(
        repository,
        "commit",
        "-q",
        "-m",
        f"deploy: attempt {revision}",
        "-m",
        "SDO-Role: source-deployer",
    )
    return _git(repository, "rev-parse", "HEAD")


class SequencedBackend:
    def __init__(self) -> None:
        self.calls: list[str | None] = []

    def deploy(
        self,
        *,
        repository: Path,
        namespace: str,
        application: str,
        health_objective: str,
        correction_feedback: str | None,
    ) -> DeploymentAttempt:
        assert namespace == "demo"
        assert application == "example"
        assert health_objective == "The application serves traffic."
        self.calls.append(correction_feedback)
        call = len(self.calls)
        if call == 1:
            return DeploymentAttempt(
                deployed=False,
                source_commit=None,
                agent_session_id="agent-1",
                summary="image build failed",
            )
        return DeploymentAttempt(
            deployed=True,
            source_commit=_commit_deployment(repository, call),
            agent_session_id=f"agent-{call}",
            summary="Kubernetes rollout completed",
        )


class SequencedVerifier:
    def __init__(self) -> None:
        self.calls: list[DeploymentAttempt] = []

    def verify(
        self,
        *,
        repository: Path,
        namespace: str,
        application: str,
        health_objective: str,
        attempt: DeploymentAttempt,
    ) -> DeploymentVerification:
        del repository, namespace, application, health_objective
        self.calls.append(attempt)
        if len(self.calls) == 1:
            return DeploymentVerification(healthy=False, feedback="Service has no ready endpoints")
        return DeploymentVerification(healthy=True, feedback="Independent detector is healthy")


def test_deployment_retries_failures_with_independent_verifier_feedback(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    backend = SequencedBackend()
    verifier = SequencedVerifier()

    accepted = deploy_from_source(
        repository,
        namespace="demo",
        application="example",
        health_objective="The application serves traffic.",
        backend=backend,
        verifier=verifier,
        max_attempts=3,
    )

    assert accepted.agent_session_id == "agent-3"
    assert accepted.source_commit == _git(repository, "rev-parse", "HEAD")
    assert backend.calls[0] is None
    assert "image build failed" in str(backend.calls[1])
    assert backend.calls[2] == "Service has no ready endpoints"
    assert [attempt.agent_session_id for attempt in verifier.calls] == ["agent-2", "agent-3"]


class NoCommitBackend:
    def deploy(self, **_: object) -> DeploymentAttempt:
        return DeploymentAttempt(
            deployed=True,
            source_commit=None,
            agent_session_id="agent-without-commit",
            summary="claimed success",
        )


class NeverCalledVerifier:
    def verify(self, **_: object) -> DeploymentVerification:
        raise AssertionError("an uncommitted attempt must not be verified")


def test_deployment_rejects_claimed_success_without_a_new_commit(tmp_path: Path) -> None:
    repository = _repository(tmp_path)

    with pytest.raises(DeploymentError, match="did not create a new attributable Git commit"):
        deploy_from_source(
            repository,
            namespace="demo",
            application="example",
            health_objective="The application serves traffic.",
            backend=NoCommitBackend(),
            verifier=NeverCalledVerifier(),
            max_attempts=2,
        )


class UnattributedCommitBackend:
    def deploy(self, *, repository: Path, **_: object) -> DeploymentAttempt:
        deployment = repository / "deploy.yaml"
        deployment.write_text("apiVersion: v1\nkind: Service\n", encoding="utf-8")
        _git(repository, "add", "deploy.yaml")
        _git(repository, "commit", "-q", "-m", "unattributed deployment")
        return DeploymentAttempt(
            deployed=True,
            source_commit=_git(repository, "rev-parse", "HEAD"),
            agent_session_id="agent-unattributed",
            summary="claimed success",
        )


def test_deployment_rejects_commit_without_sdo_role_attribution(tmp_path: Path) -> None:
    repository = _repository(tmp_path)

    with pytest.raises(DeploymentError, match="SDO-Role: source-deployer"):
        deploy_from_source(
            repository,
            namespace="demo",
            application="example",
            health_objective="The application serves traffic.",
            backend=UnattributedCommitBackend(),
            verifier=NeverCalledVerifier(),
            max_attempts=1,
        )


def test_deployment_models_validate_structured_boundaries() -> None:
    with pytest.raises(ValueError, match="at least 1 character"):
        DeploymentAttempt(deployed=True, source_commit=None, agent_session_id="", summary="ok")
    with pytest.raises(ValueError, match="at least 1 character"):
        DeploymentVerification(healthy=False, feedback="")


def test_codex_backend_runs_writable_kubernetes_session_with_structured_output(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    captured: dict[str, object] = {}

    def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["command"] = command
        captured["prompt"] = kwargs["input"]
        output_path = Path(command[command.index("--output-last-message") + 1])
        output_path.write_text(
            json.dumps(
                {
                    "deployed": True,
                    "source_commit": "a" * 40,
                    "summary": "rollout complete",
                }
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps({"type": "thread.started", "thread_id": "codex-session"}),
            stderr="",
        )

    attempt = CodexDeploymentBackend(command_runner=runner).deploy(
        repository=repository,
        namespace="demo",
        application="example",
        health_objective="The application serves traffic.",
        correction_feedback="Service has no endpoints",
    )

    assert attempt.agent_session_id == "codex-session"
    assert attempt.source_commit == "a" * 40
    command = cast("list[str]", captured["command"])
    assert command[0:4] == ["codex", "exec", "--sandbox", "danger-full-access"]
    prompt = str(captured["prompt"])
    assert "Kubernetes" in prompt
    assert "deploy/, k8s/, or manifests/" in prompt
    assert "Reserve .sdo for the SDO goal, architecture, outcomes, diagnostics, and playbooks" in prompt
    assert ".sdo/deployment" not in prompt
    assert "demo" in prompt
    assert "The application serves traffic." in prompt
    assert "Service has no endpoints" in prompt
    assert "SDO-Role: source-deployer" in prompt
