"""Tools for the Crucible dual-agent judge loop (pydantic-ai style)."""

from __future__ import annotations

import ast
import asyncio
import concurrent.futures
import fcntl
import json
import logging
import os
import re
import shlex
import signal
import subprocess
import uuid
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from pydantic_ai import ModelRetry, RunContext

logger = logging.getLogger(__name__)

MAX_OUTPUT_CHARS = 4000
MAX_READ_CHARS = 25000
APPLY_READ_CHAR_LIMIT = True
BASH_TIMEOUT = 60
THINKING_BUDGET = 4096
MAX_OUTPUT_TOKENS = 16_384
MUTATING_KUBECTL_VERBS: frozenset[str] = frozenset(
    {
        "apply",
        "delete",
        "patch",
        "edit",
        "scale",
        "set",
        "replace",
        "create",
        "rollout",
    }
)


class SRESubmission(BaseModel):
    answer: str = Field(
        description=(
            "Concise diagnosis of the fault (diagnosis stage) or description of applied mitigation (mitigation stage)."
        ),
    )
    justification: str = Field(
        description="Evidence and reasoning supporting the answer.",
    )
    causal_chain: str = Field(
        default="",
        description=(
            "Full causal chain: misconfigured field → mechanism → observed symptom "
            "(diagnosis stage only). Leave empty for mitigation stage."
        ),
    )
    reflection: str = Field(
        default="",
        description=(
            "Recovery only: 2-3 sentence analysis of why the original agent's "
            "diagnosis or mitigation was wrong — what investigative steps were missed "
            "or what evidence was misinterpreted, and what lesson follows. "
            "Leave empty for normal diagnosis and mitigation stages."
        ),
    )


class SharedFile:
    def __init__(self, path: Path) -> None:
        self._path = path

    def init(self, content: str) -> None:
        if self._path.exists():
            logger.warning("Shared file already exists, skipping init: %s", self._path)
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(content)
        tmp.rename(self._path)

    def append(self, text: str) -> None:
        with self._path.open("a") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                fh.write(text)
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)

    def read(self) -> str:
        return self._path.read_text()

    def read_text(self) -> str:
        return self._path.read_text()

    def write_text(self, text: str) -> None:
        self._path.write_text(text)

    def open(self, mode: str = "r"):
        return self._path.open(mode)

    def __str__(self) -> str:
        return str(self._path)


@dataclass
class SharedState:
    submitted: bool = False
    verdict: str | None = None  # "APPROVED" | "REJECTED" | None
    answer: str | None = None
    answer_justification: str | None = None
    answer_causal_chain: str | None = None
    answer_reflection: str | None = None
    independent_findings_submitted: bool = False
    hypothesis_revealed: bool = False
    benchmark_block: str = ""


@dataclass
class SREDeps:
    namespace: str
    shared_file: SharedFile
    iteration: int
    stage: str  # "diagnosis" | "mitigation"
    state: SharedState = field(default_factory=SharedState)
    lt_summary_file: Path | None = None
    incidents_dir: Path | None = None
    ltm_model_id: str | None = None
    ltm_call_count: int = 0
    ltm_call_budget: int = 1
    trajectory_path: Path | None = None
    triage_report: TriageReport | None = None


@dataclass
class JudgeDeps:
    namespace: str
    shared_file: SharedFile
    iteration: int
    stage: str
    submit_mcp_url: str
    hypothesis_text: str = ""
    state: SharedState = field(default_factory=SharedState)


# ---------------------------------------------------------------------------
# Pipeline models (4-stage diagnosis pipeline)
# ---------------------------------------------------------------------------


class TriageAnomaly(BaseModel):
    resource_kind: str = Field(description="Kubernetes resource kind (e.g., Pod, Service, ConfigMap)")
    resource_name: str = Field(description="Name of the resource")
    namespace: str = Field(description="Namespace of the resource")
    observation: str = Field(description="Factual description of the anomaly — no interpretation")


class TriageReport(BaseModel):
    non_running_pods: list[TriageAnomaly] = Field(
        default_factory=list, description="Pods not in Running/Completed/Succeeded state"
    )
    services_without_endpoints: list[TriageAnomaly] = Field(
        default_factory=list, description="Services with 0 endpoints or selector mismatches"
    )
    configmap_anomalies: list[TriageAnomaly] = Field(
        default_factory=list, description="ConfigMaps with unusual content (feature flags, auth scripts, etc.)"
    )
    deployment_anomalies: list[TriageAnomaly] = Field(
        default_factory=list, description="Deployment/StatefulSet spec issues (images, resources, env, etc.)"
    )
    probe_anomalies: list[TriageAnomaly] = Field(
        default_factory=list, description="Liveness/readiness probe misconfigurations"
    )
    job_anomalies: list[TriageAnomaly] = Field(
        default_factory=list, description="Running Jobs/CronJobs that may be fault injectors or load generators"
    )
    storage_anomalies: list[TriageAnomaly] = Field(
        default_factory=list, description="PV/PVC issues (pending, access mode conflicts, affinity violations)"
    )
    network_anomalies: list[TriageAnomaly] = Field(
        default_factory=list, description="NetworkPolicies, Ingress, DNS policy issues"
    )
    scheduling_anomalies: list[TriageAnomaly] = Field(
        default_factory=list, description="Taint/toleration, affinity, ResourceQuota issues"
    )
    crd_anomalies: list[TriageAnomaly] = Field(default_factory=list, description="CRD/operator-managed resource issues")
    rbac_anomalies: list[TriageAnomaly] = Field(default_factory=list, description="RBAC permission issues")
    recent_events: list[TriageAnomaly] = Field(
        default_factory=list, description="Warning/error events from kubectl get events"
    )
    other_anomalies: list[TriageAnomaly] = Field(
        default_factory=list, description="Anomalies that don't fit other categories"
    )
    raw_cluster_snapshot: str = Field(
        default="", description="Condensed kubectl output for downstream agents to reference"
    )


def format_triage_report(report: TriageReport) -> str:
    """Convert a TriageReport to readable markdown."""
    sections = [
        ("Non-Running Pods", report.non_running_pods),
        ("Services Without Endpoints", report.services_without_endpoints),
        ("ConfigMap Anomalies", report.configmap_anomalies),
        ("Deployment Anomalies", report.deployment_anomalies),
        ("Probe Anomalies", report.probe_anomalies),
        ("Job Anomalies", report.job_anomalies),
        ("Storage Anomalies", report.storage_anomalies),
        ("Network Anomalies", report.network_anomalies),
        ("Scheduling Anomalies", report.scheduling_anomalies),
        ("CRD Anomalies", report.crd_anomalies),
        ("RBAC Anomalies", report.rbac_anomalies),
        ("Recent Events", report.recent_events),
        ("Other Anomalies", report.other_anomalies),
    ]
    lines = ["### Triage Report"]
    for title, anomalies in sections:
        if anomalies:
            lines.append(f"\n**{title}**")
            lines.extend(f"- `{a.resource_kind}/{a.resource_name}` ({a.namespace}): {a.observation}" for a in anomalies)
    if not any(anomalies for _, anomalies in sections):
        lines.append("\nNo anomalies detected.")
    return "\n".join(lines) + "\n"


@dataclass
class TriageDeps:
    namespace: str
    shared_file: Path


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _agent_cwd() -> Path:
    """Return the working directory for agent tools (from ``SREGYM_EXP_ENV`` or ``'.'``)."""
    return Path(os.getenv("SREGYM_EXP_ENV", "."))


def _run_async(coro):
    """Run *coro* safely even when a pydantic-ai event loop is already running."""
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


def _run_bash_sync(cmd: str) -> str:
    """Run *cmd* in a shell, capture stdout+stderr, truncate to MAX_OUTPUT_CHARS.

    The subprocess is started in its own session (``start_new_session=True``)
    so that on timeout we can kill the **entire process group** — not just the
    top-level shell — preventing orphaned child processes such as
    ``kubectl exec -it`` from lingering indefinitely.
    """
    cwd = str(_agent_cwd())
    process: subprocess.Popen | None = None
    try:
        process = subprocess.Popen(  # noqa: S602
            cmd,
            shell=True,
            executable="/bin/bash",
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        stdout, stderr = process.communicate(timeout=BASH_TIMEOUT)
        output = stdout
        if stderr:
            output += f"\nSTDERR:\n{stderr}"
        if process.returncode != 0:
            output = f"[Exit code {process.returncode}]\n{output}"
    except subprocess.TimeoutExpired:
        if process is not None:
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            except OSError:
                process.kill()
            process.wait()
        return f"Error: Command timed out after {BASH_TIMEOUT} seconds."
    except Exception as e:
        if process is not None:
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            except OSError:
                process.kill()
            process.wait()
        return f"Error executing command: {e}"

    if len(output) > MAX_OUTPUT_CHARS:
        tmp_path = f"/tmp/bash_out_{uuid.uuid4().hex}.txt"
        Path(tmp_path).write_text(output)
        return (
            f"Output truncated ({len(output)} chars). Written to {tmp_path}. "
            f"Use read_file to inspect it (e.g. read_file(path='{tmp_path}', start_line=0, end_line=100))."
        )
    return output or "(no output)"


def _check_mutating_kubectl(cmd: str) -> str | None:
    """Return an error message if *cmd* contains a mutating kubectl verb, else None."""
    try:
        tokens = shlex.split(cmd)
    except ValueError:
        return "Error: unable to parse command (malformed quoting). Fix the command syntax."

    for i, token in enumerate(tokens):
        if token == "kubectl":
            # Find the first non-flag token after "kubectl"
            for candidate in tokens[i + 1 :]:
                if not candidate.startswith("-"):
                    if candidate in MUTATING_KUBECTL_VERBS:
                        return (
                            f"Error: kubectl verb '{candidate}' is mutating and not allowed "
                            "for the judge agent. Use exec_bash_readonly only for read-only operations."
                        )
                    break
    return None


async def _submit_to_benchmark(
    submit_mcp_url: str,
    submission_ans: str,
    stage: str,
) -> tuple[bool, str, dict | None]:
    """Submit *submission_ans* to the benchmark MCP server.

    Returns (success, message, oracle_result_dict).
    """
    from mcp import ClientSession
    from mcp.client.sse import sse_client

    async with AsyncExitStack() as stack:
        transport = await stack.enter_async_context(sse_client(url=submit_mcp_url))
        session = await stack.enter_async_context(ClientSession(*transport))
        await session.initialize()
        result = await session.call_tool("submit", arguments={"ans": submission_ans})

    raw = result.content[0].text if result.content else "{}"
    try:
        parsed = ast.literal_eval(raw)
    except Exception:
        return False, f"Failed to parse benchmark response: {raw}", None

    if parsed.get("status") != "200":
        return False, f"Benchmark returned non-200 status: {parsed}", None

    try:
        oracle = json.loads(parsed.get("text", "{}"))
    except json.JSONDecodeError:
        return False, f"Benchmark text is not valid JSON: {parsed.get('text')}", None

    # Filter oracle to only include current stage results — the conductor
    # returns a cumulative dict (e.g. Diagnosis + Mitigation), but each
    # caller only needs its own stage.
    STAGE_TIMING_KEYS = {"Diagnosis": "TTL", "Mitigation": "TTM"}
    stage_key = stage.capitalize()
    timing_key = STAGE_TIMING_KEYS.get(stage_key)
    filtered_oracle = {}
    if stage_key in oracle:
        filtered_oracle[stage_key] = oracle[stage_key]
    if timing_key and timing_key in oracle:
        filtered_oracle[timing_key] = oracle[timing_key]

    stage_result = filtered_oracle.get(stage_key, {})
    if not stage_result.get("success"):
        return False, f"Benchmark rejected submission for stage '{stage_key}'.", filtered_oracle

    return True, f"Benchmark accepted submission for stage '{stage_key}'.", filtered_oracle


# ---------------------------------------------------------------------------
# SRE agent tools
# ---------------------------------------------------------------------------


def exec_bash(ctx: RunContext[SREDeps], cmd: str) -> str:
    """Execute a shell command and return its output.

    Args:
        cmd: The shell command to run.
    """
    return _run_bash_sync(cmd)


def read_file(
    ctx: RunContext[Any],
    path: str,
    start_line: int = 0,
    end_line: int | None = None,
) -> str:
    """Read lines from a file and return them in cat -n format.

    Args:
        path: Absolute or relative path to the file.
        start_line: First line to read (0-indexed, inclusive). Negative values
            count from the end of the file (e.g. -50 starts at the 50th-last line).
        end_line: Last line to read (0-indexed, exclusive). Defaults to 200
            when start_line >= 0, or end-of-file when start_line < 0.
            Use -1 to explicitly read through the end of the file.
    """
    try:
        lines = Path(path).read_text().splitlines()
        n = len(lines)
        start = max(0, start_line if start_line >= 0 else n + start_line)
        if end_line is None:
            end = n if start_line < 0 else min(200, n)
        elif end_line < 0:
            end = n
        else:
            end = min(end_line, n)
        numbered = "\n".join(f"{start + i + 1:6}\t{line}" for i, line in enumerate(lines[start:end]))
        if not numbered:
            return "(empty range)"
        if APPLY_READ_CHAR_LIMIT and len(numbered) > MAX_READ_CHARS:
            return (
                f"Error: requested range too large ({len(numbered)} chars, max {MAX_READ_CHARS}). "
                f"The file has {len(lines)} lines. Either proportionally decrease the line range "
                f"based on {len(numbered)}/{MAX_READ_CHARS} chars, or read just a few lines first "
                f"to find keywords, then use `cat {path} | grep <pattern>` via exec_bash to "
                f"extract only the lines you need."
            )
        return numbered
    except FileNotFoundError:
        return f"Error: File not found: {path}"
    except Exception as e:
        return f"Error reading file: {e}"


MAX_GREP_RESULTS = 200


def grep(
    ctx: RunContext[Any],
    pattern: str,
    path: str = ".",
    include: str = "",
) -> str:
    """Search for a regex pattern in files, returning matching lines with file paths and line numbers.

    Args:
        pattern: Regex pattern to search for.
        path: File or directory to search in (default: current working directory).
        include: Optional glob pattern to filter files (e.g. "*.yaml", "*.py").
    """
    target = _agent_cwd() / path
    if len(pattern) > 1000:
        return "Error: regex pattern too long (max 1000 chars)."
    try:
        regex = re.compile(pattern)
    except re.error as e:
        return f"Error: invalid regex: {e}"

    if not target.exists():
        return f"Error: path not found: {path}"

    matches: list[str] = []
    total_chars = 0
    truncated = False

    files: list[Path]
    if target.is_file():
        files = [target]
    else:
        glob_pattern = include or "*"
        files = sorted(target.rglob(glob_pattern))

    for file_path in files:
        if not file_path.is_file():
            continue
        try:
            content = file_path.read_text()
        except (UnicodeDecodeError, OSError):
            continue
        for idx, line in enumerate(content.splitlines(), start=1):
            if regex.search(line):
                try:
                    relative = file_path.relative_to(target)
                except ValueError:
                    relative = file_path
                entry = f"{relative}:{idx}:{line.rstrip()}"
                total_chars += len(entry) + 1
                if total_chars > MAX_OUTPUT_CHARS or len(matches) >= MAX_GREP_RESULTS:
                    truncated = True
                    break
                matches.append(entry)
        if truncated:
            break

    if not matches:
        return "(no matches)"

    result = "\n".join(matches)
    if truncated:
        tmp_path = f"/tmp/grep_out_{uuid.uuid4().hex}.txt"
        # Write all collected matches so far
        Path(tmp_path).write_text(result)
        result += (
            f"\n\n... truncated ({len(matches)} matches shown). "
            f"Full output written to {tmp_path}. "
            f"Use read_file to inspect it."
        )
    return result


def write_file(ctx: RunContext[Any], path: str, content: str) -> str:
    """Write content to a file, creating parent directories as needed.

    Args:
        path: Destination file path.
        content: Text content to write.
    """
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        return f"Written {len(content)} bytes to {path}"
    except Exception as e:
        return f"Error writing file: {e}"


def str_replace_file(
    ctx: RunContext[Any],
    path: str,
    old_str: str,
    new_str: str,
) -> str:
    """Replace the first occurrence of old_str with new_str in a file.

    Args:
        path: Path to the file to edit.
        old_str: Exact string to find (must appear at least once).
        new_str: Replacement string.
    """
    try:
        if not old_str:
            return "Error: old_str must not be empty."
        p = Path(path)
        original = p.read_text()
        if old_str not in original:
            return f"Error: old_str not found in {path}"
        updated = original.replace(old_str, new_str, 1)
        p.write_text(updated)
        return f"Replaced in {path}"
    except FileNotFoundError:
        return f"Error: File not found: {path}"
    except Exception as e:
        return f"Error replacing in file: {e}"


# ---------------------------------------------------------------------------
# LTM retrieval subagent models
# ---------------------------------------------------------------------------


class CandidateRootCause(BaseModel):
    root_cause_class: str = Field(
        description=(
            "Abstract class of root cause (e.g., 'missing Kubernetes Service',"
            " 'targetPort mismatch', 'DNS policy override')"
        )
    )
    root_cause: str = Field(
        description=(
            "Natural-language description of the failure pattern to look for."
            " Do NOT name specific services, ports, or field values."
        )
    )
    distinguishing_check: str = Field(
        description=(
            "Natural-language investigation strategy describing what to check"
            " across ALL resources of the relevant class. Not a specific kubectl"
            " command targeting a single resource."
        )
    )
    mitigation_hint: str = Field(description="Generic mitigation approach for this root cause class")


class DifferentialDiagnosis(BaseModel):
    candidate_root_causes: list[CandidateRootCause] = Field(
        description="Top 1-3 candidate root causes for the observed symptoms, ordered by relevance"
    )
    novel_cause_signals: str = Field(
        description="What to look for if none of the candidates match — signals indicating a novel root cause"
    )
    caveats: str = Field(description="What doesn't match; what to verify before assuming patterns apply")


class CandidateVerification(BaseModel):
    """Result of verifying whether a candidate root cause applies to the current incident."""

    candidate_index: int = Field(description="Index of the candidate in the differential diagnosis list")
    root_cause_class: str = Field(description="The candidate's root_cause_class (echoed for context)")
    root_cause: str = Field(description="The candidate's root_cause (echoed for context)")
    applies: bool = Field(description="True if evidence confirms this candidate applies; False if ruled out")
    causal_chain: str = Field(
        default="",
        description=(
            "If applies: full chain from misconfigured field → mechanism → observed symptom. Empty if ruled out."
        ),
    )
    evidence: list[str] = Field(
        default_factory=list,
        description="Specific evidence items (command outputs, log lines, field values) supporting the conclusion",
    )
    reasoning: str = Field(description="Explanation of why this candidate was confirmed or ruled out")


class VerifiedDifferentialDiagnosis(BaseModel):
    """Differential diagnosis with each candidate verified by a subagent."""

    verified_candidates: list[CandidateVerification] = Field(
        description="Verification results for each candidate, ordered by original rank"
    )
    confirmed_candidates: list[CandidateVerification] = Field(
        default_factory=list,
        description="Subset of verified_candidates where applies=True, for convenience",
    )
    novel_cause_signals: str = Field(description="What to look for if none of the candidates match")
    caveats: str = Field(description="Original caveats from the retrieval agent")


class HypothesisCoverageVerdict(BaseModel):
    """Result of checking whether a hypothesis explains all triage anomalies."""

    verdict: str = Field(description="'accept' if the hypothesis explains all anomalies, 'reject' otherwise")
    explained_anomalies: list[str] = Field(
        default_factory=list,
        description="Triage anomalies that the hypothesis explains (including pre-existing noise)",
    )
    unexplained_anomalies: list[str] = Field(
        default_factory=list,
        description="Triage anomalies that the hypothesis does NOT explain",
    )
    reasoning: str = Field(description="Explanation of coverage assessment")


class MitigationStrategy(BaseModel):
    """A mitigation strategy retrieved from the knowledge base."""

    root_cause_class: str = Field(description="The root cause class this mitigation addresses")
    mitigation_approach: str = Field(description="Generic mitigation approach from the knowledge base")
    detailed_steps: str = Field(
        default="",
        description=(
            "Detailed mitigation steps extracted from referenced incident files, "
            "if available. Includes specific commands or procedures used in past incidents."
        ),
    )
    incident_refs: list[str] = Field(
        default_factory=list,
        description="Incident file references where this mitigation was applied",
    )
    caveats: str = Field(
        default="",
        description="Warnings or conditions under which this mitigation may not apply",
    )


class MitigationSearchResult(BaseModel):
    """Result of searching the knowledge base for mitigation strategies."""

    strategies: list[MitigationStrategy] = Field(
        description=(
            "Mitigation strategies matching the confirmed root cause, "
            "ordered by relevance. Empty if no matching root cause class found in KB."
        ),
    )
    novel_cause: bool = Field(
        default=False,
        description="True if the root cause does not match any known class in the KB",
    )
    general_guidance: str = Field(
        default="",
        description=("General mitigation guidance when no exact match is found, or additional context from the KB"),
    )


VERIFICATION_THINKING_BUDGET = 2048


def _exec_bash_readonly_impl(cmd: str) -> str:
    """Core read-only bash execution: check for mutating kubectl, then run."""
    error = _check_mutating_kubectl(cmd)
    if error is not None:
        return error
    return _run_bash_sync(cmd)


def exec_bash_any(ctx: RunContext[Any], cmd: str) -> str:
    """Execute a shell command and return its output.

    Args:
        cmd: The shell command to run.
    """
    return _run_bash_sync(cmd)


def exec_bash_readonly_any(ctx: RunContext[Any], cmd: str) -> str:
    """Execute a read-only shell command. Mutating kubectl verbs are blocked.

    Args:
        cmd: The shell command to run (must not mutate cluster state).
    """
    return _exec_bash_readonly_impl(cmd)


async def _ltm_stream_handler(ctx: Any, events: Any) -> None:
    from pydantic_ai.messages import (
        FunctionToolCallEvent,
        FunctionToolResultEvent,
        PartEndEvent,
        RetryPromptPart,
        ThinkingPart,
        ToolReturnPart,
    )

    from libs.agent_mw._turn_logger import _fmt_args, _tool_failed

    async for event in events:
        if isinstance(event, FunctionToolCallEvent):
            logger.info("[ltm-search] → %s(%s)", event.part.tool_name, _fmt_args(event.part.args))
        elif isinstance(event, FunctionToolResultEvent):
            result = event.result
            if isinstance(result, RetryPromptPart):
                logger.warning(
                    "[ltm-search] ✗ %s() failed: %s",
                    result.tool_name or "unknown",
                    result.model_response(),
                )
            elif isinstance(result, ToolReturnPart) and _tool_failed(result.content):
                logger.warning(
                    "[ltm-search] ✗ %s() exited with code %s: %s",
                    result.tool_name,
                    result.content.get("exit_code", "?"),
                    result.content.get("stderr", ""),
                )
        elif isinstance(event, PartEndEvent) and isinstance(event.part, ThinkingPart) and event.part.has_content():
            logger.info("[ltm-search] <thinking> %s", event.part.content)


def _make_verify_stream_handler(idx: int):
    """Create a stream handler that logs with [ltm-verify-{idx}] prefix."""
    prefix = f"[ltm-verify-{idx}]"

    async def _handler(ctx: Any, events: Any) -> None:
        from pydantic_ai.messages import (
            FunctionToolCallEvent,
            FunctionToolResultEvent,
            PartEndEvent,
            RetryPromptPart,
            ThinkingPart,
            ToolReturnPart,
        )

        from libs.agent_mw._turn_logger import _fmt_args, _tool_failed

        async for event in events:
            if isinstance(event, FunctionToolCallEvent):
                logger.info("%s → %s(%s)", prefix, event.part.tool_name, _fmt_args(event.part.args))
            elif isinstance(event, FunctionToolResultEvent):
                result = event.result
                if isinstance(result, RetryPromptPart):
                    logger.warning(
                        "%s ✗ %s() failed: %s",
                        prefix,
                        result.tool_name or "unknown",
                        result.model_response(),
                    )
                elif isinstance(result, ToolReturnPart) and _tool_failed(result.content):
                    logger.warning(
                        "%s ✗ %s() exited with code %s: %s",
                        prefix,
                        result.tool_name,
                        result.content.get("exit_code", "?"),
                        result.content.get("stderr", ""),
                    )
            elif isinstance(event, PartEndEvent) and isinstance(event.part, ThinkingPart) and event.part.has_content():
                logger.info("%s <thinking> %s", prefix, event.part.content)

    return _handler


def _write_trajectory_record(
    trajectory_path: Path,
    agent_name: str,
    result: Any,
    run_ctx: dict[str, Any] | None = None,
) -> None:
    """Write a single trajectory record for an inline agent run (same format as TrajectoryMiddleware)."""
    from datetime import datetime

    from pydantic_ai.messages import ModelMessagesTypeAdapter

    u = result.usage()
    record = {
        "agent_name": agent_name,
        "timestamp": datetime.now().isoformat(),
        "run_ctx": run_ctx,
        "messages": ModelMessagesTypeAdapter.dump_python(result.all_messages(), mode="json"),
        "usage": {
            "input_tokens": u.input_tokens or 0,
            "output_tokens": u.output_tokens or 0,
        },
    }
    trajectory_path.parent.mkdir(parents=True, exist_ok=True)
    with open(trajectory_path, "a") as f:
        f.write(json.dumps(record) + "\n")


async def _run_verification_phase(
    candidates: list[CandidateRootCause],
    diagnosis: DifferentialDiagnosis,
    observed_symptoms: str,
    namespace: str,
    stage: str,
    model_id: str,
    trajectory_path: Path | None = None,
    triage_report: TriageReport | None = None,
) -> VerifiedDifferentialDiagnosis:
    """Spawn one verification subagent per candidate in parallel and return aggregated results."""
    from pydantic_ai import Agent

    from libs.pydantic_agent import thinking_settings
    from sregym_agents.crucible._prompts import _render

    triage_context = ""
    if triage_report is not None:
        triage_context = format_triage_report(triage_report)

    async def _verify_one(idx: int, candidate: CandidateRootCause) -> CandidateVerification:
        prompt = _render(
            "ltm_verify_candidate",
            namespace=namespace,
            stage=stage,
            observed_symptoms=observed_symptoms,
            triage_context=triage_context,
            candidate_index=idx,
            root_cause_class=candidate.root_cause_class,
            root_cause=candidate.root_cause,
            distinguishing_check=candidate.distinguishing_check,
            mitigation_hint=candidate.mitigation_hint,
        )

        verify_agent: Agent[None, CandidateVerification] = Agent(
            model_id,
            output_type=CandidateVerification,
            tools=[read_file, exec_bash_any, grep, write_file, str_replace_file],
            model_settings=thinking_settings(model_id, VERIFICATION_THINKING_BUDGET),
        )
        try:
            result = await verify_agent.run(
                prompt,
                event_stream_handler=_make_verify_stream_handler(idx),
            )
            output = result.output
            # Ensure echoed fields match the candidate
            output.candidate_index = idx
            output.root_cause_class = candidate.root_cause_class
            output.root_cause = candidate.root_cause

            if trajectory_path is not None:
                _write_trajectory_record(
                    trajectory_path,
                    f"ltm-verify-{idx}",
                    result,
                    run_ctx={"stage": stage, "role": "ltm-verify", "candidate_index": idx},
                )

            logger.info(
                "[ltm-verify-%d] done: applies=%s, reasoning=%s",
                idx,
                output.applies,
                output.reasoning,
            )
            return output
        except Exception as e:
            logger.warning("[ltm-verify-%d] failed: %s", idx, e)
            return CandidateVerification(
                candidate_index=idx,
                root_cause_class=candidate.root_cause_class,
                root_cause=candidate.root_cause,
                applies=False,
                reasoning=f"Verification failed with error: {e}",
            )

    verifications = await asyncio.gather(
        *[_verify_one(i, c) for i, c in enumerate(candidates)],
    )

    confirmed = [v for v in verifications if v.applies]

    return VerifiedDifferentialDiagnosis(
        verified_candidates=list(verifications),
        confirmed_candidates=confirmed,
        novel_cause_signals=diagnosis.novel_cause_signals,
        caveats=diagnosis.caveats,
    )


async def _triage_stream_handler(ctx: Any, events: Any) -> None:
    """Stream handler that logs triage subagent events with [triage] prefix."""
    from pydantic_ai.messages import (
        FunctionToolCallEvent,
        FunctionToolResultEvent,
        PartEndEvent,
        RetryPromptPart,
        ThinkingPart,
        ToolReturnPart,
    )

    from libs.agent_mw._turn_logger import _fmt_args, _tool_failed

    async for event in events:
        if isinstance(event, FunctionToolCallEvent):
            logger.info("[triage] → %s(%s)", event.part.tool_name, _fmt_args(event.part.args))
        elif isinstance(event, FunctionToolResultEvent):
            result = event.result
            if isinstance(result, RetryPromptPart):
                logger.warning(
                    "[triage] ✗ %s() failed: %s",
                    result.tool_name or "unknown",
                    result.model_response(),
                )
            elif isinstance(result, ToolReturnPart) and _tool_failed(result.content):
                logger.warning(
                    "[triage] ✗ %s() exited with code %s: %s",
                    result.tool_name,
                    result.content.get("exit_code", "?"),
                    result.content.get("stderr", ""),
                )
        elif isinstance(event, PartEndEvent) and isinstance(event.part, ThinkingPart) and event.part.has_content():
            logger.info("[triage] <thinking> %s", event.part.content)


async def triage_cluster(
    ctx: RunContext[SREDeps],
) -> str:
    """Systematically audit the Kubernetes namespace for unhealthy components.

    Call this FIRST, before search_prior_incidents. Returns a structured triage
    report listing all anomalous resources (non-running pods, services without
    endpoints, misconfigurations, etc.). Pass the output to search_prior_incidents
    as part of your observed_symptoms.
    """
    from pydantic_ai import Agent

    from libs.pydantic_agent import thinking_settings
    from sregym_agents.crucible._prompts import _render

    model_id = ctx.deps.ltm_model_id
    if not model_id:
        return "Error: triage_cluster requires a model ID (ltm_model_id not set)."

    prompt = _render("triage_cluster", namespace=ctx.deps.namespace)

    triage_agent: Agent[None, TriageReport] = Agent(
        model_id,
        output_type=TriageReport,
        tools=[read_file, exec_bash_any, grep],
        model_settings=thinking_settings(model_id, THINKING_BUDGET),
    )

    @triage_agent.output_validator
    def _require_tool_calls(ctx: RunContext[None], report: TriageReport) -> TriageReport:
        from pydantic_ai.messages import ToolCallPart

        has_calls = any(isinstance(part, ToolCallPart) for msg in ctx.messages for part in msg.parts)
        if not has_calls:
            raise ModelRetry(
                "You MUST use exec_bash_any to run kubectl commands before "
                "producing the triage report. You have not called any tools yet. "
                "Run the recommended kubectl commands now."
            )
        return report

    try:
        result = await triage_agent.run(
            prompt,
            event_stream_handler=_triage_stream_handler,
        )
        report = result.output
        ctx.deps.triage_report = report

        if ctx.deps.trajectory_path is not None:
            _write_trajectory_record(
                ctx.deps.trajectory_path,
                "triage",
                result,
                run_ctx={"stage": ctx.deps.stage, "role": "triage"},
            )

        formatted = format_triage_report(report)
        logger.info("[triage] done: %s", formatted)
        return formatted
    except Exception as e:
        logger.warning("[triage] failed: %s", e)
        return f"Triage failed with error: {e}. Proceed with manual investigation."


COVERAGE_THINKING_BUDGET = 2048


async def check_hypothesis_coverage(
    ctx: RunContext[SREDeps],
    hypothesis: str,
) -> str:
    """Check whether your hypothesis explains ALL anomalies in the triage report.

    Call this BEFORE submitting your diagnosis. Pass your proposed root cause
    (including the specific resource, misconfigured field, and causal chain).
    Returns accept/reject with reasoning about which triage anomalies are
    unexplained. If rejected, revise your hypothesis to account for the
    unexplained anomalies before submitting.
    """
    from pydantic_ai import Agent

    from libs.pydantic_agent import thinking_settings
    from sregym_agents.crucible._prompts import _render

    triage_report = ctx.deps.triage_report
    if triage_report is None:
        return "Error: no triage report available. Call triage_cluster first."

    model_id = ctx.deps.ltm_model_id
    if not model_id:
        return "Error: check_hypothesis_coverage requires a model ID (ltm_model_id not set)."

    triage_context = format_triage_report(triage_report)
    prompt = _render(
        "check_hypothesis_coverage",
        triage_context=triage_context,
        hypothesis=hypothesis,
    )

    coverage_agent: Agent[None, HypothesisCoverageVerdict] = Agent(
        model_id,
        output_type=HypothesisCoverageVerdict,
        model_settings=thinking_settings(model_id, COVERAGE_THINKING_BUDGET),
    )

    try:
        result = await coverage_agent.run(prompt)
        output = result.output

        if ctx.deps.trajectory_path is not None:
            _write_trajectory_record(
                ctx.deps.trajectory_path,
                "hypothesis-coverage",
                result,
                run_ctx={"stage": ctx.deps.stage, "role": "hypothesis-coverage"},
            )

        output_json = output.model_dump_json(indent=2)
        logger.info(
            "[hypothesis-coverage] done: verdict=%s, unexplained=%s, reasoning=%s",
            output.verdict,
            output.unexplained_anomalies,
            output.reasoning,
        )
        return output_json
    except Exception as e:
        logger.warning("[hypothesis-coverage] failed: %s", e)
        return f"Coverage check failed with error: {e}. Submit your best hypothesis."


async def search_prior_incidents(
    ctx: RunContext[SREDeps],
    observed_symptoms: str,
) -> str:
    """Search past incidents and return verified candidate root causes.

    Call EARLY with observed symptoms from quick triage — a full hypothesis is
    NOT required. Factual symptom descriptions work well (e.g., "pod X
    CrashLoopBackOff, OOMKilled exit code 137, service Y no endpoints"). Can
    also be called later with a refined hypothesis. Returns candidates that have
    been verified by parallel subagents — each candidate includes whether it
    applies, a causal chain if confirmed, and reasoning. Budget: 1 call per stage.

    Args:
        observed_symptoms: Factual description of current observations or hypothesis.
    """
    empty_result = (
        '{"verified_candidates": [], "confirmed_candidates": [],'
        ' "novel_cause_signals": "", "caveats": "No incident history available."}'
    )
    if not ctx.deps.lt_summary_file:
        logger.info("[ltm-search] skipped (no summary file): %s", empty_result)
        return empty_result

    if not observed_symptoms.strip():
        result = "Error: observed_symptoms must not be empty."
        logger.info("[ltm-search] skipped (empty symptoms): %s", result)
        return result

    if ctx.deps.ltm_call_count >= ctx.deps.ltm_call_budget:
        result = (
            '{"verified_candidates": [], "confirmed_candidates": [],'
            ' "novel_cause_signals": "",'
            ' "caveats": "Search budget exhausted. Proceed with independent investigation."}'
        )
        logger.info("[ltm-search] skipped (budget exhausted): %s", result)
        return result
    ctx.deps.ltm_call_count += 1

    from pydantic_ai import Agent

    from sregym_agents.crucible._prompts import _render

    triage_context = ""
    if ctx.deps.triage_report is not None:
        triage_context = format_triage_report(ctx.deps.triage_report)

    prompt = _render(
        "search_prior_incidents",
        stage=ctx.deps.stage,
        observed_symptoms=observed_symptoms,
        triage_context=triage_context,
        lt_summary_file=str(ctx.deps.lt_summary_file),
        incidents_dir=str(ctx.deps.incidents_dir) if ctx.deps.incidents_dir else "",
    )

    from libs.pydantic_agent import thinking_settings

    retrieval_agent: Agent[None, DifferentialDiagnosis] = Agent(
        ctx.deps.ltm_model_id,
        output_type=DifferentialDiagnosis,
        tools=[read_file, exec_bash_any, grep, write_file, str_replace_file],
        model_settings=thinking_settings(ctx.deps.ltm_model_id, THINKING_BUDGET),
    )
    retrieval_result = await retrieval_agent.run(prompt, event_stream_handler=_ltm_stream_handler)
    diagnosis = retrieval_result.output
    logger.info("[ltm-search] retrieval output: %s", diagnosis.model_dump_json(indent=2))

    if not diagnosis.candidate_root_causes:
        verified = VerifiedDifferentialDiagnosis(
            verified_candidates=[],
            confirmed_candidates=[],
            novel_cause_signals=diagnosis.novel_cause_signals,
            caveats=diagnosis.caveats,
        )
        output_json = verified.model_dump_json(indent=2)
        logger.info("[ltm-search] no candidates to verify: %s", output_json)
        return output_json

    verified = await _run_verification_phase(
        candidates=diagnosis.candidate_root_causes,
        diagnosis=diagnosis,
        observed_symptoms=observed_symptoms,
        namespace=ctx.deps.namespace,
        stage=ctx.deps.stage,
        model_id=ctx.deps.ltm_model_id,
        trajectory_path=ctx.deps.trajectory_path,
        triage_report=ctx.deps.triage_report,
    )
    output_json = verified.model_dump_json(indent=2)
    logger.info("[ltm-search] verified output: %s", output_json)
    return output_json


async def search_prior_incidents_any(
    ctx: RunContext[Any],
    observed_symptoms: str,
) -> str:
    """Search past incidents and return verified candidate root causes.

    Call EARLY with observed symptoms from quick triage — a full hypothesis is
    NOT required. Factual symptom descriptions work well (e.g., "pod X
    CrashLoopBackOff, OOMKilled exit code 137, service Y no endpoints"). Can
    also be called later with a refined hypothesis. Returns candidates that have
    been verified by parallel subagents — each candidate includes whether it
    applies, a causal chain if confirmed, and reasoning. Budget: 1 call per stage.

    Args:
        observed_symptoms: Factual description of current observations or hypothesis.
    """
    empty_result = (
        '{"verified_candidates": [], "confirmed_candidates": [],'
        ' "novel_cause_signals": "", "caveats": "No incident history available."}'
    )
    lt_summary_file = getattr(ctx.deps, "lt_summary_file", None)
    if not lt_summary_file:
        logger.info("[ltm-search] skipped (no summary file): %s", empty_result)
        return empty_result

    if not observed_symptoms.strip():
        result = "Error: observed_symptoms must not be empty."
        logger.info("[ltm-search] skipped (empty symptoms): %s", result)
        return result

    ltm_call_count = getattr(ctx.deps, "ltm_call_count", 0)
    ltm_call_budget = getattr(ctx.deps, "ltm_call_budget", 1)
    if ltm_call_count >= ltm_call_budget:
        result = (
            '{"verified_candidates": [], "confirmed_candidates": [],'
            ' "novel_cause_signals": "",'
            ' "caveats": "Search budget exhausted. Proceed with independent investigation."}'
        )
        logger.info("[ltm-search] skipped (budget exhausted): %s", result)
        return result
    ctx.deps.ltm_call_count = ltm_call_count + 1

    from pydantic_ai import Agent

    from sregym_agents.crucible._prompts import _render

    stage = getattr(ctx.deps, "stage", "diagnosis")
    incidents_dir = getattr(ctx.deps, "incidents_dir", None)
    ltm_model_id = getattr(ctx.deps, "ltm_model_id", None)
    namespace = getattr(ctx.deps, "namespace", "default")
    trajectory_path = getattr(ctx.deps, "trajectory_path", None)
    triage_rpt = getattr(ctx.deps, "triage_report", None)

    triage_context = ""
    if triage_rpt is not None:
        triage_context = format_triage_report(triage_rpt)

    prompt = _render(
        "search_prior_incidents",
        stage=stage,
        observed_symptoms=observed_symptoms,
        triage_context=triage_context,
        lt_summary_file=str(lt_summary_file),
        incidents_dir=str(incidents_dir) if incidents_dir else "",
    )

    from libs.pydantic_agent import thinking_settings

    retrieval_agent: Agent[None, DifferentialDiagnosis] = Agent(
        ltm_model_id,
        output_type=DifferentialDiagnosis,
        tools=[read_file, exec_bash_any, grep, write_file, str_replace_file],
        model_settings=thinking_settings(ltm_model_id, THINKING_BUDGET),
    )
    retrieval_result = await retrieval_agent.run(prompt, event_stream_handler=_ltm_stream_handler)
    diagnosis = retrieval_result.output
    logger.info("[ltm-search] retrieval output: %s", diagnosis.model_dump_json(indent=2))

    if not diagnosis.candidate_root_causes:
        verified = VerifiedDifferentialDiagnosis(
            verified_candidates=[],
            confirmed_candidates=[],
            novel_cause_signals=diagnosis.novel_cause_signals,
            caveats=diagnosis.caveats,
        )
        output_json = verified.model_dump_json(indent=2)
        logger.info("[ltm-search] no candidates to verify: %s", output_json)
        return output_json

    verified = await _run_verification_phase(
        candidates=diagnosis.candidate_root_causes,
        diagnosis=diagnosis,
        observed_symptoms=observed_symptoms,
        namespace=namespace,
        stage=stage,
        model_id=ltm_model_id,
        trajectory_path=trajectory_path,
        triage_report=triage_rpt,
    )
    output_json = verified.model_dump_json(indent=2)
    logger.info("[ltm-search] verified output: %s", output_json)
    return output_json


async def search_prior_mitigations(
    ctx: RunContext[SREDeps],
    root_cause: str,
    failed_attempts: str = "",
) -> str:
    """Search past incidents for mitigation strategies matching a confirmed root cause.

    Call with the confirmed diagnosis to retrieve proven mitigation approaches
    from past incidents. Optionally include failed_attempts to exclude strategies
    that were already tried unsuccessfully. Budget: 1 call per stage (shared with
    search_prior_incidents).

    Args:
        root_cause: The confirmed root cause diagnosis to find mitigations for.
        failed_attempts: Description of mitigation attempts that already failed (optional).
    """
    empty_result = '{"strategies": [], "novel_cause": false, "general_guidance": "No incident history available."}'
    if not ctx.deps.lt_summary_file:
        logger.info("[ltm-mitigation] skipped (no summary file): %s", empty_result)
        return empty_result

    if not root_cause.strip():
        result = "Error: root_cause must not be empty."
        logger.info("[ltm-mitigation] skipped (empty root_cause): %s", result)
        return result

    if ctx.deps.ltm_call_count >= ctx.deps.ltm_call_budget:
        result = (
            '{"strategies": [], "novel_cause": false,'
            ' "general_guidance": "Search budget exhausted. Proceed with independent mitigation."}'
        )
        logger.info("[ltm-mitigation] skipped (budget exhausted): %s", result)
        return result
    ctx.deps.ltm_call_count += 1

    from pydantic_ai import Agent

    from sregym_agents.crucible._prompts import _render

    prompt = _render(
        "search_prior_mitigations",
        root_cause=root_cause,
        failed_attempts=failed_attempts,
        lt_summary_file=str(ctx.deps.lt_summary_file),
        incidents_dir=str(ctx.deps.incidents_dir) if ctx.deps.incidents_dir else "",
    )

    from libs.pydantic_agent import thinking_settings

    retrieval_agent: Agent[None, MitigationSearchResult] = Agent(
        ctx.deps.ltm_model_id,
        output_type=MitigationSearchResult,
        tools=[read_file, exec_bash_any, grep],
        model_settings=thinking_settings(ctx.deps.ltm_model_id, THINKING_BUDGET),
    )
    retrieval_result = await retrieval_agent.run(prompt, event_stream_handler=_ltm_stream_handler)
    output = retrieval_result.output
    output_json = output.model_dump_json(indent=2)
    logger.info("[ltm-mitigation] output: %s", output_json)
    return output_json


# ---------------------------------------------------------------------------
# Judge agent tools
# ---------------------------------------------------------------------------


def exec_bash_readonly(ctx: RunContext[JudgeDeps], cmd: str) -> str:
    """Execute a read-only shell command. Mutating kubectl verbs are blocked.

    Args:
        cmd: The shell command to run (must not mutate cluster state).
    """
    return _exec_bash_readonly_impl(cmd)


def submit_independent_findings(
    ctx: RunContext[JudgeDeps],
    findings: str,
) -> str:
    """Record the judge's independent investigation findings before seeing the agent's hypothesis.

    Args:
        findings: The judge's independent assessment of the cluster state and likely root cause.
    """
    if not findings.strip():
        return "Error: findings must not be empty."

    if ctx.deps.state.independent_findings_submitted:
        return "Error: independent findings already submitted."

    iteration = ctx.deps.iteration
    entry = f"\n### Iteration {iteration} — Judge Independent Findings\n{findings}\n"
    try:
        with ctx.deps.shared_file.open("a") as fh:
            fh.write(entry)
    except Exception as e:
        return f"Error writing to shared file: {e}"

    ctx.deps.state.independent_findings_submitted = True
    return f"Independent findings recorded for iteration {iteration}."


def reveal_agent_hypothesis(
    ctx: RunContext[JudgeDeps],
) -> str:
    """Reveal the agent's hypothesis after the judge has submitted independent findings.

    Returns the agent's hypothesis text for comparison with the judge's own findings.
    """
    if not ctx.deps.state.independent_findings_submitted:
        return "Error: you must call submit_independent_findings before revealing the agent's hypothesis."

    if ctx.deps.state.hypothesis_revealed:
        return "Error: agent hypothesis already revealed."

    ctx.deps.state.hypothesis_revealed = True
    return ctx.deps.hypothesis_text


def submit_verdict(
    ctx: RunContext[JudgeDeps],
    verdict: bool,
    reasoning: str,
    submission_ans: str,
) -> str:
    """Record the judge's verdict and, if approved, submit to the benchmark.

    Args:
        verdict: True to APPROVE the agent's answer, False to REJECT it.
        reasoning: Explanation for the verdict.
        submission_ans: The agent's answer string to forward to the benchmark on approval.
    """
    if ctx.deps.state.submitted:
        return "Verdict already submitted. Your task is complete."

    if not ctx.deps.state.hypothesis_revealed:
        return "Error: you must call reveal_agent_hypothesis before submitting a verdict."

    iteration = ctx.deps.iteration
    stage = ctx.deps.stage
    status_str = "APPROVED" if verdict else "REJECTED"

    entry = f"\n### Iteration {iteration} — Judge Verdict ({stage})\n- Status: {status_str}\n- Reasoning: {reasoning}\n"

    ctx.deps.state.submitted = True
    ctx.deps.state.verdict = status_str

    benchmark_block = ""
    if verdict:
        try:
            success, message, oracle = _run_async(_submit_to_benchmark(ctx.deps.submit_mcp_url, submission_ans, stage))
            oracle_text = f"<oracle>\n{json.dumps(oracle, indent=2)}\n</oracle>" if oracle is not None else ""
            benchmark_block = (
                f"\n<benchmark_result>\nsuccess: {success}\nmessage: {message}\n{oracle_text}\n</benchmark_result>\n"
            )
        except Exception as e:
            benchmark_block = f"\n<benchmark_result>\nError submitting to benchmark: {e}\n</benchmark_result>\n"

    ctx.deps.state.benchmark_block = benchmark_block
    full_entry = entry + benchmark_block
    try:
        ctx.deps.shared_file.append(full_entry)
    except Exception as e:
        return f"Error writing verdict to shared file: {e}"

    return f"Verdict submitted: {status_str}."
