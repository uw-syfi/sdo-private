"""Structured, fresh-session agents used during SDO lifecycle bootstrap."""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field


class LifecycleAgentError(RuntimeError):
    """Raised when a lifecycle agent cannot produce a valid structured handoff."""


class TopologyResourceDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str = Field(min_length=1)
    name: str = Field(min_length=1)
    namespace: str = Field(min_length=1)
    source: str = Field(min_length=1)
    dependencies: list[str]


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
    ) -> HealthJudgeArtifact: ...


class CommandRunner(Protocol):
    def __call__(self, *args: object, **kwargs: object) -> subprocess.CompletedProcess[str]: ...


class CodexLifecycleBackend:
    """Run each lifecycle handoff as a new read-only Codex CLI session."""

    def __init__(
        self,
        *,
        executable: str = "codex",
        model: str | None = None,
        reasoning_effort: str = "medium",
        timeout_seconds: int = 900,
        command_runner: CommandRunner = subprocess.run,
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
    ) -> HealthJudgeArtifact:
        previous_payload = previous.model_dump_json(indent=2) if previous else "null"
        feedback = correction_feedback or "No validator feedback is available for the first round."
        prompt = f"""You are the independent SDO health judge for application {application!r}, authoring round
{round_index} of a bounded three-round adversarial refinement. This is a new read-only Codex session with no
conversation state. Continuity comes only from the structured deployer handoff and prior detector artifact below.

Human-owned health objective:
{health_objective}

Published deployer assessment:
{deployer.model_dump_json(indent=2)}

Prior judge artifact (null on round one):
{previous_payload}

Validation feedback:
{feedback}

Author deterministic Go detector code and deterministic Go tests using only sds.dev/observer/sdk's
DetectionContext snapshot methods and Kubernetes API types already available to observer diagnostics. Inspect the
SDK in this repository before writing. The detector must compile as package objective, export New() sdk.Detector,
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
in the deployer handoff; and set objective_digest to the exact objective SHA-256.
For covered_resources, copy every resource required by the objective exactly from the deployer handoff. In
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

    def _execute(
        self,
        repository: Path,
        prompt: str,
        output_type: type[DeployerDraft] | type[HealthJudgeDraft],
    ) -> tuple[DeployerDraft | HealthJudgeDraft, str]:
        with tempfile.TemporaryDirectory(prefix="sdo-lifecycle-agent-") as temp_dir:
            root = Path(temp_dir)
            schema_path = root / "output.schema.json"
            output_path = root / "output.json"
            schema_path.write_text(json.dumps(output_type.model_json_schema()), encoding="utf-8")
            command = [
                self.executable,
                "exec",
                "--sandbox",
                "read-only",
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
                raise LifecycleAgentError(f"Codex lifecycle session timed out after {self.timeout_seconds}s") from exc
            if completed.returncode != 0:
                details = "\n".join(
                    part.strip()
                    for part in (completed.stderr, completed.stdout)
                    if isinstance(part, str) and part.strip()
                )
                raise LifecycleAgentError(details or "Codex lifecycle session failed")
            session_id = _codex_session_id(completed.stdout)
            if session_id is None:
                raise LifecycleAgentError("Codex lifecycle session did not report a fresh thread id")
            try:
                output = output_type.model_validate_json(output_path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise LifecycleAgentError(f"invalid structured Codex lifecycle output: {exc}") from exc
        return output, session_id


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
