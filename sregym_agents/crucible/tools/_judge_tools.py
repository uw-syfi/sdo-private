"""Judge agent tools and MCP benchmark submission for the Crucible judge loop."""

from __future__ import annotations

import ast
import asyncio
import json
import logging
import random
from contextlib import AsyncExitStack
from typing import TYPE_CHECKING, Any

from pydantic_ai import RunContext  # noqa: TC002 — needed at runtime for pydantic-ai tool introspection

if TYPE_CHECKING:
    from sregym_agents.crucible.tools._deps import JudgeDeps

logger = logging.getLogger(__name__)

_MCP_MAX_RETRIES = 5
_MCP_INITIAL_DELAY = 1.0
_MCP_BACKOFF_FACTOR = 2.0
_MCP_MAX_DELAY = 60.0

# Maximum number of candidate diagnoses the judge may submit at once. Mirrors
# the cap in bench/sregym/sregym/conductor/constants.py — kept in sync by
# value rather than import so the agent doesn't depend on the benchmark
# package layout.
MAX_DIAGNOSIS_CANDIDATES = 5


async def submit_to_benchmark(
    submit_mcp_url: str,
    submission_ans: str | list[str],
    stage: str,
) -> tuple[bool, str, dict[str, Any] | None]:
    """Submit *submission_ans* to the benchmark MCP server.

    ``submission_ans`` may be a single string (a single diagnosis answer) or
    a list of candidate diagnosis strings (the multi-diagnosis path —
    benchmark grades success if any candidate matches the ground truth).

    Returns (success, message, oracle_result_dict).
    """
    from mcp import ClientSession
    from mcp.client.sse import sse_client

    result: Any = None
    for attempt in range(_MCP_MAX_RETRIES + 1):
        try:
            async with AsyncExitStack() as stack:
                transport = await stack.enter_async_context(sse_client(url=submit_mcp_url))
                session = await stack.enter_async_context(ClientSession(*transport))
                await session.initialize()
                result = await session.call_tool("submit", arguments={"ans": submission_ans})
            break
        except Exception as exc:
            if attempt == _MCP_MAX_RETRIES:
                raise
            delay = min(_MCP_INITIAL_DELAY * (_MCP_BACKOFF_FACTOR**attempt), _MCP_MAX_DELAY)
            delay += random.uniform(0, delay)
            logger.warning(
                "MCP submit failed (attempt %d/%d): %s — retrying in %.1fs.",
                attempt + 1,
                _MCP_MAX_RETRIES,
                exc,
                delay,
            )
            await asyncio.sleep(delay)

    assert result is not None
    first_content = result.content[0] if result.content else None
    raw: str = getattr(first_content, "text", "{}") if first_content is not None else "{}"
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
    filtered_oracle: dict[str, Any] = {}
    if stage_key in oracle:
        filtered_oracle[stage_key] = oracle[stage_key]
    if timing_key and timing_key in oracle:
        filtered_oracle[timing_key] = oracle[timing_key]

    stage_result: dict[str, Any] = filtered_oracle.get(stage_key, {})
    if not stage_result.get("success"):
        return False, f"Benchmark rejected submission for stage '{stage_key}'.", filtered_oracle

    return True, f"Benchmark accepted submission for stage '{stage_key}'.", filtered_oracle


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
        ctx.deps.shared_file.append(entry)
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


async def submit_verdict(
    ctx: RunContext[JudgeDeps],
    verdict: bool,
    reasoning: str,
    submission_ans: str | list[str],
) -> str:
    """Record the judge's verdict and, if approved, submit to the benchmark.

    Args:
        verdict: True to APPROVE the agent's answer, False to REJECT it.
        reasoning: Explanation for the verdict.
        submission_ans: The agent's answer to forward to the benchmark on
            approval. Either a single diagnosis string, or a list of up to
            ``MAX_DIAGNOSIS_CANDIDATES`` (currently 5) candidate diagnosis
            strings. Submit a list when the cluster exhibits multiple
            plausible faults at once — the benchmark grades the submission
            as a success if its tracked ground-truth root cause matches any
            of the candidates, so extra candidates do not penalize the
            agent. Prefer a single string when confidence is high.
    """
    if ctx.deps.state.submitted:
        return "Verdict already submitted. Your task is complete."

    if not ctx.deps.state.hypothesis_revealed:
        return "Error: you must call reveal_agent_hypothesis before submitting a verdict."

    if verdict and isinstance(submission_ans, list):
        if len(submission_ans) == 0:
            return "Error: submission_ans list must contain at least one candidate diagnosis."
        if len(submission_ans) > MAX_DIAGNOSIS_CANDIDATES:
            return (
                f"Error: submission_ans list has {len(submission_ans)} candidates; "
                f"maximum allowed is {MAX_DIAGNOSIS_CANDIDATES}."
            )

    iteration = ctx.deps.iteration
    stage = ctx.deps.stage
    status_str = "APPROVED" if verdict else "REJECTED"

    entry = f"\n### Iteration {iteration} — Judge Verdict ({stage})\n- Status: {status_str}\n- Reasoning: {reasoning}\n"

    ctx.deps.state.submitted = True
    ctx.deps.state.verdict = status_str

    benchmark_block = ""
    if verdict:
        try:
            success, message, oracle = await submit_to_benchmark(ctx.deps.submit_mcp_url, submission_ans, stage)
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
