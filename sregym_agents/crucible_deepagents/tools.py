"""Data models, helper functions, and custom tools for the Crucible DeepAgents agent."""

from __future__ import annotations

import ast
import asyncio
import fcntl
import json
import logging
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.tools import tool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


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
            "diagnosis or mitigation was wrong — what investigative steps were "
            "missed or what evidence was misinterpreted, and what lesson follows. "
            "Leave empty for normal diagnosis and mitigation stages."
        ),
    )


class SharedFile:
    """File-lock-protected append-write helper for inter-agent communication."""

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
# Triage / LTM pipeline models
# ---------------------------------------------------------------------------


class TriageAnomaly(BaseModel):
    category: str = Field(
        description="Anomaly category — use a short descriptive label "
        "(e.g., 'Non-Running Pods', 'Port Mismatch', 'Services Without Endpoints', "
        "'ConfigMap Anomalies', 'Recent Events'). "
        "Use standard categories when they fit; create new ones for novel anomaly types."
    )
    resource_kind: str = Field(description="Kubernetes resource kind (e.g., Pod, Service, ConfigMap)")
    resource_name: str = Field(description="Name of the resource")
    namespace: str = Field(description="Namespace of the resource")
    observation: str = Field(description="Factual description of the anomaly — no interpretation")


class TriageReport(BaseModel):
    anomalies: list[TriageAnomaly] = Field(
        default_factory=list, description="All observed anomalies, each tagged with a category"
    )
    raw_cluster_snapshot: str = Field(
        default="", description="Condensed kubectl output for downstream agents to reference"
    )


def format_triage_report(report: TriageReport) -> str:
    """Convert a TriageReport to readable markdown."""
    lines = ["### Triage Report"]
    if not report.anomalies:
        lines.append("\nNo anomalies detected.")
        return "\n".join(lines) + "\n"
    grouped: dict[str, list[TriageAnomaly]] = {}
    for a in report.anomalies:
        grouped.setdefault(a.category, []).append(a)
    for category, anomalies in grouped.items():
        lines.append(f"\n**{category}**")
        lines.extend(f"- `{a.resource_kind}/{a.resource_name}` ({a.namespace}): {a.observation}" for a in anomalies)
    return "\n".join(lines) + "\n"


class CandidateRootCause(BaseModel):
    root_cause_class: str = Field(description="Abstract class of root cause (e.g., 'missing Kubernetes Service')")
    root_cause: str = Field(description="Natural-language description of the failure pattern")
    distinguishing_check: str = Field(
        description="Investigation strategy describing what to check across ALL resources"
    )
    mitigation_hint: str = Field(description="Generic mitigation approach for this root cause class")


class DifferentialDiagnosis(BaseModel):
    candidate_root_causes: list[CandidateRootCause] = Field(
        description="Top 1-3 candidate root causes, ordered by relevance"
    )
    novel_cause_signals: str = Field(description="What to look for if none of the candidates match")
    caveats: str = Field(description="What doesn't match; what to verify before assuming patterns apply")


class CandidateVerification(BaseModel):
    candidate_index: int = Field(description="Index of the candidate in the differential diagnosis list")
    root_cause_class: str = Field(description="The candidate's root_cause_class")
    root_cause: str = Field(description="The candidate's root_cause")
    applies: bool = Field(description="True if evidence confirms this candidate applies")
    causal_chain: str = Field(
        default="",
        description="If applies: full chain from misconfigured field → mechanism → symptom",
    )
    evidence: list[str] = Field(
        default_factory=list,
        description="Specific evidence items supporting the conclusion",
    )
    reasoning: str = Field(description="Explanation of why this candidate was confirmed or ruled out")


class VerifiedDifferentialDiagnosis(BaseModel):
    verified_candidates: list[CandidateVerification] = Field(description="Verification results for each candidate")
    confirmed_candidates: list[CandidateVerification] = Field(
        default_factory=list,
        description="Subset where applies=True",
    )
    novel_cause_signals: str = Field(description="What to look for if none of the candidates match")
    caveats: str = Field(description="Original caveats from the retrieval agent")


class HypothesisCoverageVerdict(BaseModel):
    verdict: str = Field(description="'accept' if the hypothesis explains all anomalies, 'reject' otherwise")
    explained_anomalies: list[str] = Field(
        default_factory=list,
        description="Triage anomalies that the hypothesis explains",
    )
    unexplained_anomalies: list[str] = Field(
        default_factory=list,
        description="Triage anomalies that the hypothesis does NOT explain",
    )
    reasoning: str = Field(description="Explanation of coverage assessment")


class MitigationStrategy(BaseModel):
    root_cause_class: str = Field(description="The root cause class this mitigation addresses")
    mitigation_approach: str = Field(description="Generic mitigation approach from the knowledge base")
    detailed_steps: str = Field(default="", description="Detailed mitigation steps from past incidents")
    incident_refs: list[str] = Field(default_factory=list, description="Incident file references")
    caveats: str = Field(default="", description="Warnings or conditions under which this may not apply")


class MitigationSearchResult(BaseModel):
    strategies: list[MitigationStrategy] = Field(
        description="Mitigation strategies matching the confirmed root cause",
    )
    novel_cause: bool = Field(
        default=False,
        description="True if the root cause does not match any known class in the KB",
    )
    general_guidance: str = Field(
        default="",
        description="General mitigation guidance when no exact match is found",
    )


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


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

    from mcp.types import TextContent

    first_content = result.content[0] if result.content else None
    raw = first_content.text if isinstance(first_content, TextContent) else "{}"
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

    STAGE_TIMING_KEYS = {"Diagnosis": "TTL", "Mitigation": "TTM"}
    stage_key = stage.capitalize()
    timing_key = STAGE_TIMING_KEYS.get(stage_key)
    filtered_oracle: dict = {}
    if stage_key in oracle:
        filtered_oracle[stage_key] = oracle[stage_key]
    if timing_key and timing_key in oracle:
        filtered_oracle[timing_key] = oracle[timing_key]

    stage_result = filtered_oracle.get(stage_key, {})
    if not stage_result.get("success"):
        return False, f"Benchmark rejected submission for stage '{stage_key}'.", filtered_oracle

    return True, f"Benchmark accepted submission for stage '{stage_key}'.", filtered_oracle


def _run_async(coro: Any) -> Any:
    """Run *coro* safely even when an event loop is already running."""
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


# ---------------------------------------------------------------------------
# Trajectory callback handler
# ---------------------------------------------------------------------------


class TrajectoryCallbackHandler(BaseCallbackHandler):
    """Collects messages and usage from a DeepAgents run, writes a JSONL record on completion."""

    def __init__(
        self,
        trajectory_path: Path,
        agent_name: str,
        run_ctx: dict[str, Any] | None = None,
    ) -> None:
        super().__init__()
        self.trajectory_path = trajectory_path
        self.agent_name = agent_name
        self.run_ctx = run_ctx or {}
        self._messages: list[dict] = []
        self._usage: dict[str, int] = {"input_tokens": 0, "output_tokens": 0}
        self._turn_count: int = 0
        self._written = False

    def on_llm_start(self, serialized: dict, prompts: list[str], **kwargs: Any) -> None:
        self._turn_count += 1
        logger.info("[%s] turn %d — LLM call started", self.agent_name, self._turn_count)
        self._messages.append(
            {
                "type": "llm_start",
                "prompts": prompts[:3],  # truncate for size
            }
        )

    def on_llm_end(self, response: Any, **kwargs: Any) -> None:
        record: dict[str, Any] = {"type": "llm_end"}
        # Capture reasoning/thinking content
        if hasattr(response, "generations"):
            for gen_list in response.generations:
                for gen in gen_list:
                    if hasattr(gen, "text") and gen.text:
                        record["text"] = gen.text[:2000]
                        logger.info(
                            "[%s] turn %d — assistant response:\n%s",
                            self.agent_name,
                            self._turn_count,
                            gen.text[:2000],
                        )
                    if hasattr(gen, "message"):
                        msg = gen.message
                        # Capture thinking blocks from Claude responses
                        if hasattr(msg, "additional_kwargs"):
                            thinking = msg.additional_kwargs.get("thinking")
                            if thinking:
                                thinking_str = str(thinking)[:2000]
                                record["thinking"] = thinking_str
                                logger.info(
                                    "[%s] turn %d — model thinking:\n%s",
                                    self.agent_name,
                                    self._turn_count,
                                    thinking_str,
                                )
                        # Log tool_calls requested by the model
                        if hasattr(msg, "tool_calls") and msg.tool_calls:
                            for tc in msg.tool_calls:
                                tc_name = (
                                    tc.get("name", "unknown")
                                    if isinstance(tc, dict)
                                    else getattr(tc, "name", "unknown")
                                )
                                tc_args = tc.get("args", {}) if isinstance(tc, dict) else getattr(tc, "args", {})
                                logger.info(
                                    "[%s] turn %d — tool call requested: %s(%s)",
                                    self.agent_name,
                                    self._turn_count,
                                    tc_name,
                                    str(tc_args)[:500],
                                )
        # Capture token usage
        if hasattr(response, "llm_output") and response.llm_output:
            usage = response.llm_output.get("usage", {})
            self._usage["input_tokens"] += usage.get("input_tokens", 0)
            self._usage["output_tokens"] += usage.get("output_tokens", 0)
        self._messages.append(record)

    def on_tool_start(self, serialized: dict, input_str: str, **kwargs: Any) -> None:
        tool_name = serialized.get("name", "unknown")
        logger.info(
            "[%s] tool start: %s — input: %s",
            self.agent_name,
            tool_name,
            input_str[:500],
        )
        self._messages.append(
            {
                "type": "tool_start",
                "tool": tool_name,
                "input": input_str[:1000],
            }
        )

    def on_tool_end(self, output: Any, **kwargs: Any) -> None:
        text = output.content if hasattr(output, "content") else str(output)
        self._messages.append(
            {
                "type": "tool_end",
                "output": text[:2000],
            }
        )

    def on_tool_error(self, error: BaseException, **kwargs: Any) -> None:
        logger.error(
            "[%s] tool error: %s: %s",
            self.agent_name,
            type(error).__name__,
            error,
        )
        self._messages.append(
            {
                "type": "tool_error",
                "error": f"{type(error).__name__}: {error}"[:2000],
            }
        )

    def on_chain_end(self, outputs: dict, **kwargs: Any) -> None:
        # Write trajectory record only once (outermost chain)
        if self._written:
            return
        self._written = True
        record = {
            "agent_name": self.agent_name,
            "timestamp": datetime.now().isoformat(),
            "run_ctx": self.run_ctx,
            "messages": self._messages,
            "usage": self._usage,
        }
        self.trajectory_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.trajectory_path, "a") as f:
            f.write(json.dumps(record) + "\n")

    def flush(self) -> None:
        """Force-write the trajectory record if on_chain_end hasn't fired yet."""
        if not self._written:
            self._written = True
            record = {
                "agent_name": self.agent_name,
                "timestamp": datetime.now().isoformat(),
                "run_ctx": self.run_ctx,
                "messages": self._messages,
                "usage": self._usage,
            }
            self.trajectory_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.trajectory_path, "a") as f:
                f.write(json.dumps(record) + "\n")

    @property
    def usage(self) -> dict[str, int]:
        return dict(self._usage)


# ---------------------------------------------------------------------------
# Stateful tool factories
# ---------------------------------------------------------------------------


def make_submit_independent_findings_tool(deps: JudgeDeps):
    """Create a submit_independent_findings tool for the judge agent."""

    @tool
    def submit_independent_findings(findings: str) -> str:
        """Record the judge's independent investigation findings before seeing the agent's hypothesis.

        Args:
            findings: The judge's independent assessment of the cluster state and likely root cause.
        """
        if not findings.strip():
            return "Error: findings must not be empty."
        if deps.state.independent_findings_submitted:
            return "Error: independent findings already submitted."

        iteration = deps.iteration
        entry = f"\n### Iteration {iteration} — Judge Independent Findings\n{findings}\n"
        try:
            deps.shared_file.append(entry)
        except Exception as e:
            return f"Error writing to shared file: {e}"

        deps.state.independent_findings_submitted = True
        return f"Independent findings recorded for iteration {iteration}."

    return submit_independent_findings


def make_reveal_agent_hypothesis_tool(deps: JudgeDeps):
    """Create a reveal_agent_hypothesis tool for the judge agent."""

    @tool
    def reveal_agent_hypothesis() -> str:
        """Reveal the agent's hypothesis after the judge has submitted independent findings.

        Returns the agent's hypothesis text for comparison with the judge's own findings.
        """
        if not deps.state.independent_findings_submitted:
            return "Error: you must call submit_independent_findings before revealing the agent's hypothesis."
        if deps.state.hypothesis_revealed:
            return "Error: agent hypothesis already revealed."

        deps.state.hypothesis_revealed = True
        return deps.hypothesis_text

    return reveal_agent_hypothesis


def make_submit_verdict_tool(deps: JudgeDeps):
    """Create a submit_verdict tool for the judge agent."""

    @tool
    def submit_verdict(verdict: bool, reasoning: str, submission_ans: str) -> str:
        """Record the judge's verdict and, if approved, submit to the benchmark.

        Args:
            verdict: True to APPROVE the agent's answer, False to REJECT it.
            reasoning: Explanation for the verdict.
            submission_ans: The agent's answer string to forward to the benchmark on approval.
        """
        if deps.state.submitted:
            return "Verdict already submitted. Your task is complete."
        if not deps.state.hypothesis_revealed:
            return "Error: you must call reveal_agent_hypothesis before submitting a verdict."

        iteration = deps.iteration
        stage = deps.stage
        status_str = "APPROVED" if verdict else "REJECTED"

        entry = (
            f"\n### Iteration {iteration} — Judge Verdict ({stage})\n- Status: {status_str}\n- Reasoning: {reasoning}\n"
        )

        deps.state.submitted = True
        deps.state.verdict = status_str

        benchmark_block = ""
        if verdict:
            try:
                success, message, oracle = _run_async(_submit_to_benchmark(deps.submit_mcp_url, submission_ans, stage))
                oracle_text = f"<oracle>\n{json.dumps(oracle, indent=2)}\n</oracle>" if oracle is not None else ""
                benchmark_block = (
                    f"\n<benchmark_result>\nsuccess: {success}\nmessage: {message}\n"
                    f"{oracle_text}\n</benchmark_result>\n"
                )
            except Exception as e:
                benchmark_block = f"\n<benchmark_result>\nError submitting to benchmark: {e}\n</benchmark_result>\n"

        deps.state.benchmark_block = benchmark_block
        full_entry = entry + benchmark_block
        try:
            deps.shared_file.append(full_entry)
        except Exception as e:
            return f"Error writing verdict to shared file: {e}"

        return f"Verdict submitted: {status_str}."

    return submit_verdict
