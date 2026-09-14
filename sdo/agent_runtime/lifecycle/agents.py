"""Structured, fresh-session agents used during SDO lifecycle bootstrap."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from libs.agent_cli.claude_structured import ClaudeStructuredExecutionError, run_claude_structured
from libs.agent_cli.codex import (
    CodexSessionIdError,
    CodexStructuredExecutionError,
    CodexStructuredOutputError,
    run_codex_structured,
)


class LifecycleAgentError(RuntimeError):
    """Raised when a lifecycle agent cannot produce a valid structured handoff."""


class TopologyResourceDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str = Field(min_length=1)
    name: str = Field(min_length=1)
    namespace: str = Field(min_length=1)
    source: str = Field(min_length=1)
    dependencies: list[str]


class ActiveTopologyResourceDTO(BaseModel):
    """A resource selected by the deployment backend for the active variant."""

    model_config = ConfigDict(extra="forbid")

    kind: str = Field(min_length=1)
    name: str = Field(min_length=1)


class DeployerDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_commit: str = Field(min_length=1)
    topology_fingerprint: str = Field(min_length=64, max_length=64)
    resources: list[TopologyResourceDTO]
    architecture_summary_markdown: str = Field(min_length=20)


class DeployerAssessment(DeployerDraft):
    session_id: str = Field(min_length=1)


class HealthJudgeDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    round: int = Field(ge=1)
    objective_digest: str = Field(min_length=64, max_length=64)
    source_commit: str = Field(min_length=1)
    covered_resources: list[TopologyResourceDTO]
    failure_patterns: list[str] = Field(min_length=1)
    detector_source: str = Field(min_length=40)
    detector_test_source: str = Field(min_length=40)


class HealthJudgeArtifact(HealthJudgeDraft):
    session_id: str = Field(min_length=1)


class HealthJudgeWorkspaceDraft(BaseModel):
    """Metadata handoff from a judge that authored source directly in a workspace."""

    model_config = ConfigDict(extra="forbid")

    round: int = Field(ge=1)
    objective_digest: str = Field(min_length=64, max_length=64)
    source_commit: str = Field(min_length=1)
    covered_resources: list[TopologyResourceDTO]
    failure_patterns: list[str] = Field(min_length=1)


class HealthJudgeWorkspaceArtifact(HealthJudgeWorkspaceDraft):
    session_id: str = Field(min_length=1)


class LifecycleAgentBackend(Protocol):
    def run_deployer(
        self,
        *,
        repository: Path,
        application: str,
        correction_feedback: str | None,
    ) -> DeployerAssessment: ...

    def run_health_judge(
        self,
        *,
        repository: Path,
        application: str,
        health_objective: str,
        deployer: DeployerAssessment,
        round_index: int,
        previous: HealthJudgeArtifact | None,
        correction_feedback: str | None,
        active_resources: list[ActiveTopologyResourceDTO] | None = None,
    ) -> HealthJudgeArtifact: ...


class WorkspaceHealthJudgeBackend(Protocol):
    def run_health_judge_workspace(
        self,
        *,
        repository: Path,
        application: str,
        health_objective: str,
        deployer: DeployerAssessment,
        round_index: int,
        previous: HealthJudgeArtifact | None,
        correction_feedback: str | None,
        active_resources: list[ActiveTopologyResourceDTO] | None = None,
    ) -> HealthJudgeWorkspaceArtifact: ...


CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


_DETECTOR_SDK_REFERENCE = """Trusted controller/sdk API reference (do not search outside the application checkout):

- import path `sdo.dev/controller/sdk`; test helper import path `sdo.dev/controller/sdk/sdktest`.
- `type Detector interface { Spec() DetectorSpec; Detect(context.Context, DetectionContext) ([]Finding, error) }`.
- `DetectionContext` exposes `Namespace() string`, `ConfigMaps() []corev1.ConfigMap`,
  `Services() []corev1.Service`, `Pods() []corev1.Pod`, `Deployments() []appsv1.Deployment`,
  `ReplicaSets() []appsv1.ReplicaSet`, `Endpoints() []corev1.Endpoints`,
  `EndpointSlices() []discoveryv1.EndpointSlice`, `NetworkPolicies() []networkingv1.NetworkPolicy`,
  `Events() []corev1.Event`, `ReadyEndpointCountForService(namespace, service string) int`,
  `PodsForService(namespace, service string) []corev1.Pod`, and
  `RecentEventsFor(namespace, kind, name string) []corev1.Event`.
- `sdk.ConfigMapReferencesForDeployment(appsv1.Deployment) []sdk.ConfigMapReference` returns sorted,
  deduplicated volume, projected-volume, envFrom, and env ConfigMap references; each reference has `Name string`
  and `Optional bool`.
- `sdk.Finding` has string fields `RuleID`, `Summary`, `Evidence`, and `Fingerprint`; enum fields `Status` and
  `Severity`; `PrimaryResource sdk.ObjectRef`; `RelatedResources []sdk.ObjectRef`; `Playbooks []string`;
  `ParameterBindings map[string]sdk.ObjectRef`; and `Metadata map[string]any`. Use `sdk.FindingActive`,
  `sdk.SeverityCritical`, and stable fingerprints. In particular, never use `map[string]string` for Metadata.
- `sdk.ObjectRef` fields are `APIVersion`, `Kind`, `Namespace`, and `Name`.
- `sdktest.Snapshot` implements DetectionContext. Its fields are `NamespaceName`, `ConfigMapList`, `ServiceList`,
  `PodList`, `DeploymentList`, `ReplicaSetList`, `EndpointList`, `EndpointSliceList`, `NetworkPolicyList`, and
  `EventList`.
"""


_ABSOLUTE_PATH = re.compile(r"(?<![A-Za-z0-9_.$~-])(/[A-Za-z0-9_./*?{}$@%+=:,~-]+)")
_PARENT_PATH = re.compile(r"(?:^|[\s'\"=;(])\.\.(?:/[^\s'\";|&)]*)?(?=$|[\s'\";|&)])")
_WRITE_REDIRECT_ABSOLUTE_PATH = re.compile(r"(?:^|[ \t])(?:\d*>>?|&>)\s*['\"]?(/[A-Za-z0-9_./*?{}$@%+=:,~-]+)")
_GIT_OBJECT_PATH = re.compile(r"\b[0-9a-fA-F]{7,64}:(/[A-Za-z0-9_./*?{}$@%+=,~-]+)")
_SYSTEM_COMMAND_ROOTS = tuple(Path(path) for path in ("/bin", "/usr/bin", "/usr/local/bin"))


class CodexLifecycleBackend:
    """Run each lifecycle handoff as a new read-only Codex CLI session."""

    def __init__(
        self,
        *,
        executable: str = "codex",
        model: str | None = None,
        reasoning_effort: str = "medium",
        timeout_seconds: int = 900,
        command_runner: CommandRunner | None = None,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("lifecycle agent timeout must be positive")
        self.executable = executable
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.timeout_seconds = timeout_seconds
        self.command_runner = command_runner

    def run_deployer(
        self,
        *,
        repository: Path,
        application: str,
        correction_feedback: str | None,
    ) -> DeployerAssessment:
        feedback = correction_feedback or "No prior attempt; inspect the source from first principles."
        prompt = f"""You are the SDO deployer for application {application!r}.

This is a fresh, independent, read-only session. Inspect the Git repository and its tracked deployment artifacts.
Return a structured source-grounded inventory and a complete architecture summary that names every source-backed
component, its selectors and dependencies, configuration inputs, images, and build/deployment relationships. In
architecture_summary_markdown, mention every resource name from the trusted controller feedback verbatim, including
low-level data stores and observability resources; do not collapse named resources into categories. Do not edit any
file and do not infer resources absent from tracked source. The controller will independently compare your source
commit, topology fingerprint, complete resource inventory, and summary coverage with deterministic repository
inspection.

Repository isolation is part of the evidence contract. Inspect only the current application checkout. Never use
`..`, an absolute path outside this checkout, a sibling experiment, a package cache, or another SDO source tree.
The controller audits command events and rejects the entire fresh session if any command escapes this checkout.
Do not create or execute helper scripts in temporary directories. Do not use `$TMPDIR` or `/tmp`, even for files
you create yourself: the audit treats a compound command that later reads or executes such a file as an escape.
Use the built-in Read, Glob, and Grep tools for inspection and perform small calculations directly. If shell scratch
space is essential, keep it under `.sdo/session-scratch/` in the current checkout; the session remains read-only, so
prefer not to create scratch files at all.

Correction feedback from the prior fresh attempt:
{feedback}
"""
        draft, session_id = self._execute(repository, prompt, DeployerDraft)
        return DeployerAssessment(**draft.model_dump(), session_id=session_id)

    def run_health_judge(
        self,
        *,
        repository: Path,
        application: str,
        health_objective: str,
        deployer: DeployerAssessment,
        round_index: int,
        previous: HealthJudgeArtifact | None,
        correction_feedback: str | None,
        active_resources: list[ActiveTopologyResourceDTO] | None = None,
    ) -> HealthJudgeArtifact:
        objective_digest = hashlib.sha256(health_objective.strip().encode()).hexdigest()
        previous_payload = previous.model_dump_json(indent=2) if previous else "null"
        active_payload = (
            json.dumps([resource.model_dump(mode="json") for resource in active_resources], indent=2)
            if active_resources is not None
            else "null"
        )
        feedback = correction_feedback or "No validator feedback is available for the first round."
        prompt = f"""You are the independent SDO health judge for application {application!r}, authoring round
{round_index} of a bounded three-round adversarial refinement. This is a new read-only Codex session with no
conversation state. Continuity comes only from the structured deployer handoff and prior detector artifact below.

Human-owned health objective:
{health_objective}

Authoritative objective SHA-256: {objective_digest}
Copy this exact value into objective_digest and the detector's healthObjectiveDigest constant; do not recompute it.

Published deployer assessment:
{deployer.model_dump_json(indent=2)}

Controller-observed active topology (null when no deployment observation is available):
{active_payload}

Prior judge artifact (null on round one):
{previous_payload}

Validation feedback:
{feedback}

Author deterministic Go detector code and deterministic Go tests using only sdo.dev/controller/sdk's
DetectionContext snapshot methods and Kubernetes API types already available to SDO diagnostics. Use the trusted
SDK reference below; the application checkout is intentionally not expected to contain the controller SDK.

{_DETECTOR_SDK_REFERENCE}

Repository isolation is part of the evidence contract. Inspect only the current application checkout. Never use
`..`, an absolute path outside this checkout, a sibling experiment, a package cache, or another SDO source tree.
The controller audits command events and rejects the entire fresh session if any command escapes this checkout.

The detector must compile as package objective, export New() sdk.Detector,
encode the SHA-256 digest of the exact human objective in a healthObjectiveDigest constant, and detect only
objective-specific observable failure conditions. Its tests must include matching and near-miss cases. On later
rounds, identify failure patterns missed by the prior code and revise it rather than merely describing them.
Every Go func declaration must be package-level; Go does not permit named helper functions inside test functions.
Close every composite literal, control-flow block, and function before starting the next declaration. Mentally parse
both complete files before returning them, paying special attention to the line immediately before each `func`.

The detector's Spec() is a fixed controller contract, not a design choice. Return exactly ID "health-objective",
Class sdk.DetectorClassHealth, Owner sdk.DetectorOwnerHealthJudge, Interval 30*time.Second; watches for v1 Pod,
v1 ConfigMap, v1 Service, apps/v1 Deployment, networking.k8s.io/v1 NetworkPolicy, v1 Endpoints, and discovery.k8s.io/v1
EndpointSlice; persistence Firing 2 and Clearing 2; batching Severity sdk.SeverityCritical and Debounce
500*time.Millisecond; playbooks []string{{".sdo/playbooks/health-objective/README.md"}}; no originating incident;
and OriginatingCommit "lifecycle-bootstrap". Do not substitute incident/responder ownership or another ID.

Never read environment variables, benchmark results, SREGym data, verdict files, hidden fault labels, or any external
oracle. Never call an LLM at detector runtime. Return source text in the structured fields; do not edit repository
files. Set round exactly to {round_index}; set source_commit to the deployer's commit; cover only resources present
in the deployer handoff; and copy the authoritative objective SHA-256 above exactly.
The last structured response is the only response the controller accepts. It must repeat both complete Go files;
never return a placeholder such as "pending", "superseded", or a reference to an earlier commentary payload.
For covered_resources, copy every resource required by the objective exactly from the deployer handoff. Never invent
a covered ConfigMap object for a dependency that has no standalone object in the handoff. When the
controller-observed active topology is non-null, the controller will canonicalize this provenance to exact matching
active Deployment, Service, ConfigMap, and NetworkPolicy objects and ignore source variants that are not active. In
particular, "all source-backed Deployments" requires every Deployment and "all selected Services" requires every
Service in that handoff, together with every source-backed ConfigMap and NetworkPolicy that can determine those
workloads' health; do not reduce coverage to only user-facing or application-tier names. For this global objective,
covered_resources must contain exactly those Deployment, Service, ConfigMap, and NetworkPolicy objects—do not add
PersistentVolumes, PersistentVolumeClaims, Routes, or other kinds. Preserve each kind, name,
source-manifest namespace,
source path, and complete dependencies list. Do not substitute the configured runtime namespace into this provenance
field.

ConfigMaps created imperatively at deployment time may have no standalone source manifest and therefore no ConfigMap
object in the deployer inventory. Derive these required dependencies dynamically from every observed Deployment's pod
template volumes (deployment.Spec.Template.Spec.Volumes[*].ConfigMap.Name), compare them with
DetectionContext.ConfigMaps(), and emit a stable missing-ConfigMap finding for any absent reference. Do not rely only
on covered_resources or hard-coded ConfigMap names. Include matching and near-miss tests for that behavior.

The controller evaluates one configured runtime namespace. A source manifest namespace of "default" means the
resource will be applied into that configured namespace; it is not a literal runtime namespace. Never embed the
literal string "default" in detector source. Use DetectionContext.Namespace() whenever synthesizing an ObjectRef or
checking for a required resource that is absent, and use each observed object's Namespace for resources that exist.
"""
        draft, session_id = self._execute(repository, prompt, HealthJudgeDraft)
        return HealthJudgeArtifact(**draft.model_dump(), session_id=session_id)

    def run_health_judge_workspace(
        self,
        *,
        repository: Path,
        application: str,
        health_objective: str,
        deployer: DeployerAssessment,
        round_index: int,
        previous: HealthJudgeArtifact | None,
        correction_feedback: str | None,
        active_resources: list[ActiveTopologyResourceDTO] | None = None,
    ) -> HealthJudgeWorkspaceArtifact:
        objective_digest = hashlib.sha256(health_objective.strip().encode()).hexdigest()
        previous_payload = (
            previous.model_dump_json(indent=2, exclude={"detector_source", "detector_test_source"})
            if previous
            else "null"
        )
        active_payload = (
            json.dumps([resource.model_dump(mode="json") for resource in active_resources], indent=2)
            if active_resources is not None
            else "null"
        )
        feedback = correction_feedback or "No validator feedback is available for the first round."
        prompt = f"""You are the independent SDO health judge for application {application!r}, authoring round
{round_index} of bounded adversarial refinement. You have an isolated, writable copy of the application repository.

Human-owned health objective:
{health_objective}

Authoritative objective SHA-256: {objective_digest}
Published deployer assessment:
{deployer.model_dump_json(indent=2)}
Controller-observed active topology:
{active_payload}
Prior judge metadata (the current source files contain the prior implementation):
{previous_payload}
Validation feedback:
{feedback}

Edit these files directly:
- .sdo/diagnostics/detectors/health/objective/detector.go
- .sdo/diagnostics/detectors/health/objective/detector_test.go

Inspect the application source and existing detector, implement deterministic objective-specific checks, and add
matching plus near-miss tests. Use `sdo detector check` to compile and run the detector tests in the isolated
no-network validator. Read its diagnostics, revise the files, and repeat until it exits successfully. Do not run Go
source or tests by any other route. Do not edit the manifest or any application file. The controller will independently
validate the resulting files after your session ends; your self-check is not acceptance evidence.

{_DETECTOR_SDK_REFERENCE}

The detector must remain package objective, export New() sdk.Detector, and copy the exact objective digest above into
healthObjectiveDigest. Its Spec is controller-owned and already present in detector.go; preserve it. Never read
environment variables, benchmark results, SREGym data, verdict files, hidden fault labels, or an external oracle.
Inspect only this checkout; never use `..` or paths outside it.

All named helper functions must be package-level. Derive required ConfigMaps from every observed Deployment pod
template, including volume, projected-volume, envFrom, and env references, and compare them with the snapshot rather
than relying only on hard-coded names. Use DetectionContext.Namespace() for absent runtime objects and each observed
object's namespace for objects that exist; never embed "default" as a runtime namespace. When active topology is
provided, cover the matching Deployment, Service, ConfigMap, and NetworkPolicy resources required by the objective,
including low-level dependencies rather than only user-facing workloads. Do not invent resource objects absent from
the deployer handoff.

Return only metadata in the final structured response. Set round to {round_index}, source_commit to
{deployer.source_commit!r}, copy the objective digest exactly, list objective-relevant failure patterns, and use only
covered resources from the deployer handoff. Do not include source code in the response because the files are the
authoritative draft.
"""
        draft, session_id = self._execute(
            repository,
            prompt,
            HealthJudgeWorkspaceDraft,
            sandbox="workspace-write",
        )
        return HealthJudgeWorkspaceArtifact(**draft.model_dump(), session_id=session_id)

    def _execute(
        self,
        repository: Path,
        prompt: str,
        output_type: type[DeployerDraft] | type[HealthJudgeDraft] | type[HealthJudgeWorkspaceDraft],
        *,
        sandbox: str = "read-only",
    ) -> tuple[DeployerDraft | HealthJudgeDraft | HealthJudgeWorkspaceDraft, str]:
        try:
            completed = run_codex_structured(
                prompt,
                output_schema=output_type.model_json_schema(),
                cwd=repository,
                executable=self.executable,
                model=self.model,
                reasoning_effort=self.reasoning_effort,
                timeout_seconds=self.timeout_seconds,
                sandbox=sandbox,  # type: ignore[arg-type]
                runner=self.command_runner,
            )
        except subprocess.TimeoutExpired as exc:
            raise LifecycleAgentError(f"Codex lifecycle session timed out after {self.timeout_seconds}s") from exc
        except CodexStructuredExecutionError as exc:
            raise LifecycleAgentError(str(exc) or "Codex lifecycle session failed") from exc
        except CodexSessionIdError as exc:
            raise LifecycleAgentError("Codex lifecycle session did not report a fresh thread id") from exc
        except CodexStructuredOutputError as exc:
            raise LifecycleAgentError(f"invalid structured Codex lifecycle output: {exc}") from exc
        escaped_command = _first_repository_escape(completed.stdout, repository.resolve())
        if escaped_command is not None:
            raise LifecycleAgentError(
                "Codex lifecycle session read outside the application repository; "
                f"discarding its output: {escaped_command[:300]}"
            )
        try:
            output = output_type.model_validate_json(completed.output_json)
        except (OSError, ValueError) as exc:
            raise LifecycleAgentError(f"invalid structured Codex lifecycle output: {exc}") from exc
        return output, completed.session_id


class ClaudeLifecycleBackend(CodexLifecycleBackend):
    """Run lifecycle handoffs as fresh structured Claude Code sessions."""

    def __init__(
        self,
        *,
        executable: str = "claude",
        model: str | None = None,
        reasoning_effort: str = "medium",
        timeout_seconds: int = 900,
        command_runner: CommandRunner = subprocess.run,
    ) -> None:
        super().__init__(
            executable=executable,
            model=model,
            reasoning_effort=reasoning_effort,
            timeout_seconds=timeout_seconds,
            command_runner=command_runner,
        )

    def _execute(
        self,
        repository: Path,
        prompt: str,
        output_type: type[DeployerDraft] | type[HealthJudgeDraft] | type[HealthJudgeWorkspaceDraft],
        *,
        sandbox: str = "read-only",
    ) -> tuple[DeployerDraft | HealthJudgeDraft | HealthJudgeWorkspaceDraft, str]:
        try:
            completed = run_claude_structured(
                prompt,
                output_schema=output_type.model_json_schema(),
                cwd=repository,
                executable=self.executable,
                model=self.model,
                effort=self.reasoning_effort,
                timeout_seconds=self.timeout_seconds,
                sandbox=sandbox,  # type: ignore[arg-type]
                runner=self.command_runner or subprocess.run,
            )
        except subprocess.TimeoutExpired as exc:
            raise LifecycleAgentError(f"Claude lifecycle session timed out after {self.timeout_seconds}s") from exc
        except ClaudeStructuredExecutionError as exc:
            raise LifecycleAgentError(str(exc) or "Claude lifecycle session failed") from exc
        escaped_command = _first_repository_escape(completed.stdout, repository.resolve())
        if escaped_command is not None:
            raise LifecycleAgentError(
                "Claude lifecycle session read outside the application repository; "
                f"discarding its output: {escaped_command[:300]}"
            )
        try:
            output = output_type.model_validate_json(completed.output_json)
        except (OSError, ValueError) as exc:
            raise LifecycleAgentError(f"invalid structured Claude lifecycle output: {exc}") from exc
        return output, completed.session_id


def _first_repository_escape(stdout: str, repository: Path) -> str | None:
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get("type") == "assistant":
            message = event.get("message")
            content = message.get("content", []) if isinstance(message, dict) else []
            for item in content if isinstance(content, list) else []:
                if not isinstance(item, dict) or item.get("type") != "tool_use" or item.get("name") != "Bash":
                    continue
                arguments = item.get("input")
                command = arguments.get("command") if isinstance(arguments, dict) else None
                if isinstance(command, str) and _command_escapes_repository(command, repository):
                    return command
            continue
        if event.get("type") != "item.completed":
            continue
        item = event.get("item")
        if not isinstance(item, dict) or item.get("type") != "command_execution":
            continue
        command = item.get("command")
        if isinstance(command, str) and _command_escapes_repository(command, repository):
            return command
    return None


def _command_escapes_repository(command: str, repository: Path) -> bool:
    if _PARENT_PATH.search(command):
        return True
    # ``git show <object>:/path`` addresses a path inside this repository's object
    # database. Mask only the path portion so unrelated absolute paths in the same
    # compound command remain subject to the confinement audit.
    audited_command = _GIT_OBJECT_PATH.sub(
        lambda match: match.group(0).replace(match.group(1), ".git-object-path"), command
    )
    write_only_paths = {match.group(1) for match in _WRITE_REDIRECT_ABSOLUTE_PATH.finditer(audited_command)}
    for raw_path in _ABSOLUTE_PATH.findall(audited_command):
        if raw_path in write_only_paths:
            continue
        candidate = Path(raw_path)
        if candidate == repository or repository in candidate.parents:
            continue
        if any(candidate == root or root in candidate.parents for root in _SYSTEM_COMMAND_ROOTS):
            continue
        return True
    return False
