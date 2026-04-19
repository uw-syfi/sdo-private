"""Judge agent tools for the Crucible judge loop.

Benchmark MCP submission lives in :mod:`libs.sregym_lib.mcp_client` — this
module re-exports :func:`submit_to_benchmark` for call sites inside
``sregym_agents.crucible.tools``.
"""

from __future__ import annotations

import logging

from pydantic_ai import RunContext  # noqa: TC002 — needed at runtime for pydantic-ai tool introspection

from libs.sregym_lib.benchmark import BenchmarkResult, Stage
from libs.sregym_lib.mcp_client import submit_to_benchmark
from libs.sregym_lib.schema import MAX_DIAGNOSIS_CANDIDATES
from sregym_agents.crucible.tools._deps import JudgeDeps  # noqa: TC001 — needed at runtime for _impl functions

logger = logging.getLogger(__name__)


def submit_independent_findings_impl(
    deps: JudgeDeps,
    findings: str,
) -> str:
    """Record the judge's independent investigation findings before seeing the agent's hypothesis.

    Args:
        deps: Judge dependency context.
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


def submit_independent_findings(
    ctx: RunContext[JudgeDeps],
    findings: str,
) -> str:
    """Record the judge's independent investigation findings before seeing the agent's hypothesis.

    Args:
        findings: The judge's independent assessment of the cluster state and likely root cause.
    """
    return submit_independent_findings_impl(ctx.deps, findings)


def reveal_agent_hypothesis_impl(
    deps: JudgeDeps,
) -> str:
    """Reveal the agent's hypothesis after the judge has submitted independent findings.

    Args:
        deps: Judge dependency context.

    Returns the agent's hypothesis text for comparison with the judge's own findings.
    """
    if not deps.state.independent_findings_submitted:
        return "Error: you must call submit_independent_findings before revealing the agent's hypothesis."

    if deps.state.hypothesis_revealed:
        return "Error: agent hypothesis already revealed."

    deps.state.hypothesis_revealed = True
    return deps.hypothesis_text


def reveal_agent_hypothesis(
    ctx: RunContext[JudgeDeps],
) -> str:
    """Reveal the agent's hypothesis after the judge has submitted independent findings.

    Returns the agent's hypothesis text for comparison with the judge's own findings.
    """
    return reveal_agent_hypothesis_impl(ctx.deps)


async def submit_verdict_impl(
    deps: JudgeDeps,
    verdict: bool,
    reasoning: str,
    submission_ans: str | list[str],
) -> str:
    """Record the judge's verdict and, if approved, submit to the benchmark.

    Args:
        deps: Judge dependency context.
        verdict: True to APPROVE the agent's answer, False to REJECT it.
        reasoning: Explanation for the verdict.
        submission_ans: The agent's answer to forward to the benchmark on
            approval. Either a single diagnosis string, or a list of up to
            ``MAX_DIAGNOSIS_CANDIDATES`` (currently 5) candidate diagnosis
            strings.
    """
    if deps.state.submitted:
        return "Verdict already submitted. Your task is complete."

    if not deps.state.hypothesis_revealed:
        return "Error: you must call reveal_agent_hypothesis before submitting a verdict."

    if verdict and isinstance(submission_ans, list):
        if len(submission_ans) == 0:
            return "Error: submission_ans list must contain at least one candidate diagnosis."
        if len(submission_ans) > MAX_DIAGNOSIS_CANDIDATES:
            return (
                f"Error: submission_ans list has {len(submission_ans)} candidates; "
                f"maximum allowed is {MAX_DIAGNOSIS_CANDIDATES}."
            )

    iteration = deps.iteration
    stage = deps.stage
    status_str = "APPROVED" if verdict else "REJECTED"

    entry = f"\n### Iteration {iteration} — Judge Verdict ({stage})\n- Status: {status_str}\n- Reasoning: {reasoning}\n"

    deps.state.submitted = True
    deps.state.verdict = status_str

    benchmark_block = ""
    if verdict:
        stage_literal: Stage = stage if stage in ("diagnosis", "mitigation") else "diagnosis"
        try:
            bench_result = await submit_to_benchmark(deps.submit_mcp_url, submission_ans, stage)
        except Exception as e:
            bench_result = BenchmarkResult(stage=stage_literal, error=str(e))
        benchmark_block = bench_result.render()

    deps.state.benchmark_block = benchmark_block
    full_entry = entry + benchmark_block
    try:
        deps.shared_file.append(full_entry)
    except Exception as e:
        return f"Error writing verdict to shared file: {e}"

    return f"Verdict submitted: {status_str}."


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
    return await submit_verdict_impl(ctx.deps, verdict, reasoning, submission_ans)
