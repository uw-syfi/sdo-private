"""Structured, fresh-session agents used during SDO lifecycle bootstrap."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Protocol

from pydantic import BaseModel, ConfigDict, Field

from libs.agent_cli.structured import (
    AccessMode,
    AgentProvider,
    StructuredTurnError,
    StructuredTurnTimeout,
    run_structured_turn,
)
from sdo.operational_memory import DETECTOR_SDK_REFERENCE

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from agentshim import CommandExecutor


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
    """Structured deployer output.

    The resource inventory is deliberately absent: it is a deterministic controller
    fact derived from tracked manifests, so the controller attaches it instead of
    asking the model to transcribe it.
    """

    model_config = ConfigDict(extra="forbid")

    source_commit: str = Field(min_length=1)
    topology_fingerprint: str = Field(min_length=64, max_length=64)
    architecture_summary_markdown: str = Field(min_length=20)


class DeployerHandoff(DeployerDraft):
    """A deployer draft attributed to the fresh agent session that produced it."""

    session_id: str = Field(min_length=1)


class DeployerAssessment(DeployerHandoff):
    """A published deployer handoff with the controller-derived resource inventory."""

    resources: list[TopologyResourceDTO]


class AuthoredTrafficFile(BaseModel):
    """One judge-authored synthetic-traffic file under ``.sdo/diagnostics/traffic/``.

    ``path`` is relative to that directory: ``generators/<file>.go`` for the
    Go generator package or ``workloads/<name>.yaml`` for a workload profile.
    """

    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1)
    content: str = Field(min_length=1)


class HealthJudgeDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    round: int = Field(ge=1)
    objective_digest: str = Field(min_length=64, max_length=64)
    source_commit: str = Field(min_length=1)
    covered_resources: list[TopologyResourceDTO]
    failure_patterns: list[str] = Field(min_length=1)
    detector_source: str = Field(min_length=40)
    detector_test_source: str = Field(min_length=40)
    # Required in the structured-output schema (strict turns require every
    # property); an application with no HTTP entrypoint returns an empty list.
    traffic_files: list[AuthoredTrafficFile]


class HealthJudgeArtifact(HealthJudgeDraft):
    session_id: str = Field(min_length=1)
    # Provenance recorded before synthetic traffic existed has none.
    traffic_files: list[AuthoredTrafficFile] = Field(default_factory=list)


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


#: Health-judge instructions for synthetic traffic, shared by both judge sessions.
TRAFFIC_AUTHORING = """Synthetic traffic (health-judge owned). For an application that serves HTTP, also author
end-to-end synthetic traffic under .sdo/diagnostics/traffic/. Ground it in the application source and the deployer's
architecture summary (which becomes .sdo/arch.md): open the user-facing entrypoint's route handlers (its HTTP mux or
router) and derive paths it really serves, their methods, and parameter values its handlers accept, including the
records the application seeds at startup. Cover the main user journeys, read and write paths both when the
application has write paths. An isolated prober runs these journeys continuously at a few requests per second; the
traffic health detector opens an incident when a scenario violates its SLO, and a responder's repair is accepted only
when the same scenarios recover. Every scenario must therefore succeed against the healthy application.

1. Generators: Go package `generators` in .sdo/diagnostics/traffic/generators/ exporting
   `func Scenarios() traffic.Catalog` (import "sdo.dev/controller/sdk/traffic"). Each traffic.Scenario has ID,
   Description, Target {Service, Port} (a source-backed Service and a port it exposes), DependsOn (the Services on
   the request path per the architecture summary, for localization), SideEffect (traffic.SideEffectRead,
   SideEffectIdempotentWrite, or SideEffectWriteWithCleanup with Cleanup steps), Marker (writes only: a string such
   as "sdo-synthetic" that appears in every write request and names dedicated synthetic data), Steps, and Detects
   (extra fault classes: traffic.FaultWrongBody when a step checks the body, traffic.FaultSlow). A simple step is one
   line: `{Name: "search", Endpoint: traffic.GET("/hotels", traffic.Params{"inDate": traffic.DateRange("2015-04-09",
   "2015-04-23"), "outDate": traffic.DaysAfter("inDate", 1, 3)}).Contains("expected text")}`. Other parameter
   generators: Const, OneOf, IntBetween, FloatBetween, FromSeed(count, render), FromSeedPair(key, count, first,
   second) for matching credentials, FromPrevious(key); chain steps with .SaveJSON(field, key) and "{key}" path
   segments. Use .Contains(...) whenever the application reports failures inside a successful status. Generators only
   build requests and check responses: never import net, os, or the clock, and draw randomness only from the rng the
   engine passes. Never read or modify real users' data beyond the fixtures the application's own source seeds.
2. Workloads: .sdo/diagnostics/traffic/workloads/<name>.yaml with apiVersion sdo.dev/v1alpha1, kind
   TrafficWorkload, name equal to the file name, purpose, ratePerSecond, and scenarios [{id, weight}]. Write
   `health` (purpose health-probe, ratePerSecond at most 4, read scenarios only unless a write is idempotent) and
   `verify` (purpose verify-burst, ratePerSecond about 12, duration 3s, the same scenarios). Optional: timeout (2s),
   slo {window, minSamples, maxErrorRate, maxTimeoutRate, latencyPercentile, maxLatency}, and journey workloads
   (purpose journey, bounded duration) for writes that should not run continuously.
3. Firing policy: a traffic-health detector's window is re-evaluated on every probe poll (about every 500ms), far
   faster than the 2-evaluation firing threshold, so a brief data-plane stall (observed around 3s, roughly 1 in
   6-10 pod-network changes on a small cluster) can otherwise reach it and dispatch a responder for nothing.
   `traffic.NewDetector` therefore defaults `Persistence.MinDuration` to `traffic.DefaultHealthMinDuration` (9s) for
   any health-class detector that leaves it unset, so a scenario must violate its SLO continuously for that long,
   not just reach the evaluation count, before it fires; real faults still fire within it plus about one poll. Do
   not set `Persistence.MinDuration` back to a lower value for a traffic-health detector.
Omit synthetic traffic only for an application that serves no HTTP."""


class LifecycleAgentBackend(Protocol):
    def run_deployer(
        self,
        *,
        repository: Path,
        application: str,
        correction_feedback: str | None,
    ) -> DeployerHandoff: ...

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


_ABSOLUTE_PATH = re.compile(r"(?<![A-Za-z0-9_.$~*?{}-])(/[A-Za-z0-9_./*?{}$@%+=:,~-]+)")
_PARENT_PATH = re.compile(r"(?:^|[\s'\"=;(])\.\.(?:/[^\s'\";|&)]*)?(?=$|[\s'\";|&)])")
_WRITE_REDIRECT_ABSOLUTE_PATH = re.compile(r"(?:^|[ \t])(?:\d*>>?|&>)\s*['\"]?(/[A-Za-z0-9_./*?{}$@%+=:,~-]+)")
_GIT_OBJECT_PATH = re.compile(r"\b[0-9a-fA-F]{7,64}:(/[A-Za-z0-9_./*?{}$@%+=,~-]+)")
_SYSTEM_COMMAND_ROOTS = tuple(Path(path) for path in ("/bin", "/usr/bin", "/usr/local/bin"))
# One literal path segment: no globs, variables, or ``.``/``..`` traversal.
_LITERAL_SEGMENT = r"[A-Za-z0-9_-][A-Za-z0-9_.-]*"


@dataclass(frozen=True)
class ClaudeTaskOutputs:
    """Background-task output files that one Claude Code session may read back.

    Claude Code writes the output of a backgrounded shell command to
    ``<tmp>/claude-<uid>/<project-slug>/<session-id>/tasks/<task-id>.output`` and
    reads it back with ordinary shell commands. Only this session's output files
    are exempt from the repository audit; the rest of the per-uid directory, and
    other sessions' outputs, stay outside the application repository.
    """

    root: Path
    session_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.root, Path):
            raise TypeError(f"root must be a Path, got {type(self.root).__name__}")
        if not self.root.is_absolute():
            raise ValueError(f"root must be absolute, got {self.root}")
        if self.session_id in {"", ".", ".."} or "/" in self.session_id:
            raise ValueError(f"session_id must be one path segment, got {self.session_id!r}")

    @classmethod
    def for_session(cls, session_id: str, *, environ: Mapping[str, str], uid: int) -> ClaudeTaskOutputs:
        """Resolve the per-uid root the way Claude Code does.

        Claude Code uses ``$CLAUDE_CODE_TMPDIR`` when set and otherwise Node's
        ``os.tmpdir()`` (``$TMPDIR``, ``$TMP``, ``$TEMP``, then ``/tmp``).
        """
        tmpdir = next(
            (
                value.rstrip("/") or "/"
                for name in ("CLAUDE_CODE_TMPDIR", "TMPDIR", "TMP", "TEMP")
                if (value := environ.get(name))
            ),
            "/tmp",
        )
        return cls(root=Path(tmpdir) / f"claude-{uid}", session_id=session_id)

    def allows(self, raw_path: str) -> bool:
        pattern = (
            re.escape(str(self.root))
            + f"/{_LITERAL_SEGMENT}/{re.escape(self.session_id)}/tasks/{_LITERAL_SEGMENT}\\.output"
        )
        return re.fullmatch(pattern, raw_path) is not None


class CodexLifecycleBackend:
    """Run each lifecycle handoff as a new confined Codex CLI session."""

    provider: ClassVar[AgentProvider] = "codex"

    def __init__(
        self,
        *,
        model: str | None = None,
        reasoning_effort: str = "medium",
        timeout_seconds: int = 900,
        executor: CommandExecutor | None = None,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("lifecycle agent timeout must be positive")
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.timeout_seconds = timeout_seconds
        self.executor = executor

    def run_deployer(
        self,
        *,
        repository: Path,
        application: str,
        correction_feedback: str | None,
    ) -> DeployerHandoff:
        feedback = correction_feedback or "No prior attempt; inspect the source from first principles."
        prompt = f"""You are the SDO deployer for application {application!r}.

This is a fresh, independent, read-only session. Inspect the Git repository and its tracked deployment artifacts.
Return a complete source-grounded architecture summary that names every source-backed component, its selectors and
dependencies, configuration inputs, images, and build/deployment relationships. In architecture_summary_markdown,
mention every resource name from the trusted controller feedback verbatim, including low-level data stores and
observability resources; do not collapse named resources into categories. Do not edit any file and do not infer
resources absent from tracked source. The response has no resource inventory field: the controller attaches the
deterministic inventory from tracked manifests itself. The controller will independently compare your source commit,
topology fingerprint, and summary coverage with deterministic repository inspection.

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
        return DeployerHandoff(**draft.model_dump(), session_id=session_id)

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

{DETECTOR_SDK_REFERENCE}

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

{TRAFFIC_AUTHORING}
Return each file in traffic_files as {{path, content}} with path relative to .sdo/diagnostics/traffic/ (for example
generators/generators.go or workloads/health.yaml); the controller writes the files, compiles and checks them in the
isolated validator, and installs the detector that judges each health-probe workload. Repeat every file in full on
later rounds.
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
- .sdo/diagnostics/traffic/generators/*.go and .sdo/diagnostics/traffic/workloads/<name>.yaml (synthetic traffic;
  see below)

Inspect the application source and existing detector, implement deterministic objective-specific checks, and add
matching plus near-miss tests. Use `sdo detector check` to compile and run the detector tests in the isolated
no-network validator. Read its diagnostics, revise the files, and repeat until it exits successfully. Do not run Go
source or tests by any other route. Do not edit the manifest or any application file. The controller will independently
validate the resulting files after your session ends; your self-check is not acceptance evidence.
The deployer assessment above is trusted and complete. Do not rediscover topology with repository-wide `find` or
`grep`; inspect the current detector files first and open only source manifests named in that assessment when needed.

{DETECTOR_SDK_REFERENCE}

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

{TRAFFIC_AUTHORING}
`sdo detector check` also compiles the generators, proves every scenario fails against unreachable and erroring
targets and its declared fault classes, and checks the workloads. The controller installs the detector that judges
each health-probe workload; do not write that detector or edit the manifest.

Return only metadata in the final structured response. Set round to {round_index}, source_commit to
{deployer.source_commit!r}, copy the objective digest exactly, list objective-relevant failure patterns, and use only
covered resources from the deployer handoff. For a global all-Deployments/all-Services objective, return an empty
covered_resources list: the controller deterministically fills it from trusted topology, avoiding a large duplicated
handoff. Do not include source code in the response because the files are the authoritative draft.
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
        sandbox: AccessMode = "read-only",
    ) -> tuple[DeployerDraft | HealthJudgeDraft | HealthJudgeWorkspaceDraft, str]:
        role = f"{self.provider.capitalize()} lifecycle session"
        try:
            turn = run_structured_turn(
                self.provider,
                prompt,
                output_schema=output_type.model_json_schema(),
                cwd=repository,
                access=sandbox,
                model=self.model,
                reasoning_effort=self.reasoning_effort,
                timeout_seconds=self.timeout_seconds,
                executor=self.executor,
            )
        except StructuredTurnTimeout as exc:
            raise LifecycleAgentError(f"{role} timed out after {self.timeout_seconds}s") from exc
        except StructuredTurnError as exc:
            raise LifecycleAgentError(f"{role} failed: {exc}") from exc
        task_outputs = ClaudeTaskOutputs.for_session(turn.session_id, environ=os.environ, uid=os.getuid())
        escaped_command = _first_repository_escape(turn.shell_commands, repository.resolve(), task_outputs=task_outputs)
        if escaped_command is not None:
            raise LifecycleAgentError(
                f"{role} read outside the application repository; discarding its output: {escaped_command[:300]}"
            )
        try:
            output = output_type.model_validate_json(turn.output_json)
        except ValueError as exc:
            raise LifecycleAgentError(f"invalid structured {role} output: {exc}") from exc
        return output, turn.session_id


class ClaudeLifecycleBackend(CodexLifecycleBackend):
    """Run lifecycle handoffs as fresh structured Claude Code sessions."""

    provider: ClassVar[AgentProvider] = "claude"


def _first_repository_escape(
    commands: Sequence[str], repository: Path, *, task_outputs: ClaudeTaskOutputs | None = None
) -> str | None:
    """Return the first shell command that reaches outside *repository*, if any."""
    return next(
        (
            command
            for command in commands
            if _command_escapes_repository(command, repository, task_outputs=task_outputs)
        ),
        None,
    )


_SHELL_WRAPPERS = frozenset({"bash", "sh", "/bin/bash", "/bin/sh", "/usr/bin/bash", "/usr/bin/sh"})


def _unwrap_shell_command(command: str) -> str:
    """Return the script of a ``bash -c``/``-lc`` wrapper, which Codex puts around every command."""
    try:
        argv = shlex.split(command)
    except ValueError:
        return command
    if len(argv) == 3 and argv[0] in _SHELL_WRAPPERS and argv[1] in {"-c", "-lc"}:
        return argv[2]
    return command


def _mask_quoted_pattern_alternatives(script: str) -> str:
    """Hide ``/route`` alternatives inside quoted strings, such as ``rg 'HandleFunc|/hotels'``.

    Inside a quoted argument a ``/`` right after ``|`` or ``(`` starts a regular-expression
    alternative or group, not a path the shell opens. Unquoted text, including a pipe into an
    absolute command, is left for the path audit.
    """
    masked: list[str] = []
    quote: str | None = None
    escaped = False
    for char in script:
        if escaped:
            escaped = False
        elif char == "\\" and quote != "'":
            escaped = True
        elif quote is None and char in {"'", '"'}:
            quote = char
        elif char == quote:
            quote = None
        elif quote is not None and char == "/" and masked and masked[-1] in {"|", "("}:
            masked.append(" ")
            continue
        masked.append(char)
    return "".join(masked)


def _command_escapes_repository(
    command: str, repository: Path, *, task_outputs: ClaudeTaskOutputs | None = None
) -> bool:
    if _PARENT_PATH.search(command):
        return True
    # ``git show <object>:/path`` addresses a path inside this repository's object
    # database. Mask only the path portion so unrelated absolute paths in the same
    # compound command remain subject to the confinement audit.
    audited_command = _GIT_OBJECT_PATH.sub(
        lambda match: match.group(0).replace(match.group(1), ".git-object-path"), command
    )
    audited_command = _mask_quoted_pattern_alternatives(_unwrap_shell_command(audited_command))
    write_only_paths = {match.group(1) for match in _WRITE_REDIRECT_ABSOLUTE_PATH.finditer(audited_command)}
    for raw_path in _ABSOLUTE_PATH.findall(audited_command):
        if raw_path in write_only_paths:
            continue
        if task_outputs is not None and task_outputs.allows(raw_path):
            continue
        candidate = Path(raw_path)
        # ``Path`` keeps ``..`` segments, so ``<repository>/../x`` would look contained.
        if ".." in candidate.parts:
            return True
        if candidate == repository or repository in candidate.parents:
            continue
        if any(candidate == root or root in candidate.parents for root in _SYSTEM_COMMAND_ROOTS):
            continue
        return True
    return False
