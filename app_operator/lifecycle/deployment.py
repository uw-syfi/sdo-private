"""Source-to-Kubernetes deployment role for the production SDO lifecycle."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

_DEPLOYMENT_ROLE_TRAILER = "SDO-Role: source-deployer"


class DeploymentError(RuntimeError):
    """Raised when bounded source-deployment attempts cannot be accepted."""


class DeploymentAgentError(DeploymentError):
    """Raised when the coding-agent deployment backend fails."""


class DeploymentAttempt(BaseModel):
    """Structured outcome published by a deployment backend."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    deployed: bool
    source_commit: str | None = None
    agent_session_id: str = Field(min_length=1)
    summary: str = Field(min_length=1)


class DeploymentVerification(BaseModel):
    """Independent health decision for one deployed source commit."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    healthy: bool
    feedback: str = Field(min_length=1)


class DeploymentBackend(Protocol):
    """Backend-neutral coding-agent boundary for source deployment."""

    def deploy(
        self,
        *,
        repository: Path,
        namespace: str,
        application: str,
        health_objective: str,
        correction_feedback: str | None,
    ) -> DeploymentAttempt: ...


class DeploymentVerifier(Protocol):
    """Independent controller/judge boundary used to accept deployments."""

    def verify(
        self,
        *,
        repository: Path,
        namespace: str,
        application: str,
        health_objective: str,
        attempt: DeploymentAttempt,
    ) -> DeploymentVerification: ...


CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


class _DeploymentAttemptDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deployed: bool
    source_commit: str | None = None
    summary: str = Field(min_length=1)


class CodexDeploymentBackend:
    """Use a writable Codex CLI session to deploy an application to Kubernetes."""

    def __init__(
        self,
        *,
        executable: str = "codex",
        model: str | None = None,
        reasoning_effort: str = "high",
        timeout_seconds: int = 1800,
        command_runner: CommandRunner = subprocess.run,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("deployment agent timeout must be positive")
        self.executable = executable
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.timeout_seconds = timeout_seconds
        self.command_runner = command_runner

    def deploy(
        self,
        *,
        repository: Path,
        namespace: str,
        application: str,
        health_objective: str,
        correction_feedback: str | None,
    ) -> DeploymentAttempt:
        feedback = correction_feedback or "No prior deployment attempt has been independently evaluated."
        prompt = f"""You are the SDO source deployer for application {application!r}.

Bring the application from this Git repository to a running state in Kubernetes namespace {namespace!r}. Inspect the
source, build the required images, author or correct Kubernetes resources in the repository's existing conventional
deployment layout (such as deploy/, k8s/, or manifests/), apply them with kubectl, and wait for the rollout. Use
Kubernetes only. Reserve .sdo for the SDO goal, architecture, outcomes, diagnostics, and playbooks; deployment
manifests are application source and do not belong there. Do not create a second health-check script: the controller
runs an independent detector against this human-owned health objective:

{health_objective}

Feedback from the independent verifier for the previous bounded attempt:
{feedback}

Commit every accepted source or deployment-artifact change to Git. The commit message must contain the exact trailer
"{_DEPLOYMENT_ROLE_TRAILER}" so the SDO can attribute it to this role. Report source_commit as the full current HEAD
only after the commit and rollout succeed. If deployment does not succeed, set deployed to false, explain the
failure in summary, and do not claim an uncommitted revision. Return only the requested structured result.
"""
        draft, session_id = self._execute(repository, prompt)
        return DeploymentAttempt(
            **draft.model_dump(),
            agent_session_id=session_id,
        )

    def _execute(self, repository: Path, prompt: str) -> tuple[_DeploymentAttemptDraft, str]:
        with tempfile.TemporaryDirectory(prefix="sdo-deployment-agent-") as temp_dir:
            root = Path(temp_dir)
            schema_path = root / "output.schema.json"
            output_path = root / "output.json"
            schema_path.write_text(json.dumps(_DeploymentAttemptDraft.model_json_schema()), encoding="utf-8")
            command = [
                self.executable,
                "exec",
                "--sandbox",
                "danger-full-access",
                "--cd",
                str(repository.resolve()),
                "--output-schema",
                str(schema_path),
                "--output-last-message",
                str(output_path),
                "--json",
            ]
            if self.model:
                command.extend(["--model", self.model])
            command.extend(["-c", f'model_reasoning_effort="{self.reasoning_effort}"', "-"])
            try:
                completed = self.command_runner(
                    command,
                    input=prompt,
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_seconds,
                )
            except subprocess.TimeoutExpired as exc:
                raise DeploymentAgentError(f"Codex deployment session timed out after {self.timeout_seconds}s") from exc
            if completed.returncode != 0:
                details = "\n".join(
                    part.strip()
                    for part in (completed.stderr, completed.stdout)
                    if isinstance(part, str) and part.strip()
                )
                raise DeploymentAgentError(details or "Codex deployment session failed")
            session_id = _codex_session_id(completed.stdout)
            if session_id is None:
                raise DeploymentAgentError("Codex deployment session did not report a fresh thread id")
            try:
                draft = _DeploymentAttemptDraft.model_validate_json(output_path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise DeploymentAgentError(f"invalid structured Codex deployment output: {exc}") from exc
        return draft, session_id


def deploy_from_source(
    repository: Path,
    *,
    namespace: str,
    application: str,
    health_objective: str,
    verifier: DeploymentVerifier,
    backend: DeploymentBackend | None = None,
    max_attempts: int = 3,
) -> DeploymentAttempt:
    """Deploy source with bounded correction attempts and independent acceptance."""

    if max_attempts < 1:
        raise ValueError("deployment attempts must be positive")
    root = repository.resolve()
    if not root.is_dir():
        raise ValueError(f"deployment repository is not a directory: {root}")
    _required_text("namespace", namespace)
    _required_text("application", application)
    _required_text("health objective", health_objective)
    if not _is_git_repository(root):
        raise ValueError(f"deployment repository is not a Git worktree: {root}")

    selected_backend = backend or CodexDeploymentBackend(model=os.getenv("SDO_DEPLOYMENT_MODEL") or None)
    correction_feedback: str | None = None
    last_error = "deployment was not attempted"
    for _attempt_index in range(1, max_attempts + 1):
        previous_head = _git_head(root)
        try:
            attempt = selected_backend.deploy(
                repository=root,
                namespace=namespace,
                application=application,
                health_objective=health_objective,
                correction_feedback=correction_feedback,
            )
            attempt = DeploymentAttempt.model_validate(attempt)
        except (DeploymentAgentError, ValueError) as exc:
            last_error = f"deployment backend failed: {exc}"
            correction_feedback = last_error
            continue

        if not attempt.deployed:
            last_error = f"deployment attempt reported failure: {attempt.summary}"
            correction_feedback = last_error
            continue

        commit_error = _commit_error(root, attempt, previous_head)
        if commit_error is not None:
            last_error = commit_error
            correction_feedback = last_error
            continue

        try:
            verification = verifier.verify(
                repository=root,
                namespace=namespace,
                application=application,
                health_objective=health_objective,
                attempt=attempt,
            )
            verification = DeploymentVerification.model_validate(verification)
        except ValueError as exc:
            last_error = f"independent deployment verifier failed: {exc}"
            correction_feedback = last_error
            continue
        if verification.healthy:
            return attempt
        last_error = verification.feedback
        correction_feedback = verification.feedback

    raise DeploymentError(
        f"source deployment exhausted {max_attempts} bounded attempt(s); last rejection: {last_error}"
    )


def _commit_error(repository: Path, attempt: DeploymentAttempt, previous_head: str | None) -> str | None:
    current_head = _git_head(repository)
    if attempt.source_commit is None or current_head is None or current_head == previous_head:
        return "deployment attempt did not create a new attributable Git commit"
    resolved_commit = _resolve_commit(repository, attempt.source_commit)
    if resolved_commit is None or resolved_commit != current_head:
        return "deployment attempt source_commit is not the current Git HEAD"
    message = _run_git(repository, "show", "-s", "--format=%B", current_head)
    if _DEPLOYMENT_ROLE_TRAILER not in message.splitlines():
        return f"deployment commit is missing required attribution trailer {_DEPLOYMENT_ROLE_TRAILER!r}"
    return None


def _is_git_repository(repository: Path) -> bool:
    completed = subprocess.run(
        ["git", "-C", str(repository), "rev-parse", "--is-inside-work-tree"],
        check=False,
        capture_output=True,
        text=True,
    )
    return completed.returncode == 0 and completed.stdout.strip() == "true"


def _git_head(repository: Path) -> str | None:
    completed = subprocess.run(
        ["git", "-C", str(repository), "rev-parse", "--verify", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip() if completed.returncode == 0 else None


def _resolve_commit(repository: Path, revision: str) -> str | None:
    completed = subprocess.run(
        ["git", "-C", str(repository), "rev-parse", "--verify", f"{revision}^{{commit}}"],
        check=False,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip() if completed.returncode == 0 else None


def _run_git(repository: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repository), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _required_text(name: str, value: str) -> None:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    if not value.strip():
        raise ValueError(f"{name} must not be empty")


def _codex_session_id(stdout: str) -> str | None:
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict) or event.get("type") != "thread.started":
            continue
        thread_id = event.get("thread_id")
        if isinstance(thread_id, str) and thread_id:
            return thread_id
    return None
