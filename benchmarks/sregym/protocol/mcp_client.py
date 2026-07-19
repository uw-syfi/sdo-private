"""MCP client for the SREGym benchmark submit endpoint.

Single public helper: :func:`submit_to_benchmark`, which speaks to the
conductor's ``/submit`` MCP server over SSE, retries transient failures
with exponential backoff, and returns a typed :class:`BenchmarkResult`.

Kept agent-agnostic — crucible's judge agent is just one caller; any
future agent that wants to submit programmatically (rather than via the
CLI-level MCP tool wiring) can depend on this.
"""

from __future__ import annotations

import ast
import asyncio
import json
import logging
import random
from contextlib import AsyncExitStack
from typing import Any

from benchmarks.sregym.protocol.benchmark import BenchmarkResult, Oracle, Stage

logger = logging.getLogger(__name__)

_MCP_MAX_RETRIES = 5
_MCP_INITIAL_DELAY = 1.0
_MCP_BACKOFF_FACTOR = 2.0
_MCP_MAX_DELAY = 60.0

_STAGE_TIMING_KEYS = {"Diagnosis": "TTL", "Mitigation": "TTM"}


async def submit_to_benchmark(
    submit_mcp_url: str,
    submission_ans: str | list[str],
    stage: str,
) -> BenchmarkResult:
    """Submit *submission_ans* to the benchmark MCP server.

    ``submission_ans`` may be a single string (a single diagnosis answer) or
    a list of candidate diagnosis strings (the multi-diagnosis path —
    benchmark grades success if any candidate matches the ground truth).

    Returns a :class:`BenchmarkResult` describing the outcome. Callers that
    need the textual ``<benchmark_result>`` block should call ``.render()``.
    """
    from mcp import ClientSession
    from mcp.client.sse import sse_client

    stage_literal: Stage = stage if stage in ("diagnosis", "mitigation") else "diagnosis"

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
        return BenchmarkResult(
            stage=stage_literal,
            success=False,
            message=f"Failed to parse benchmark response: {raw}",
        )

    if parsed.get("status") != "200":
        return BenchmarkResult(
            stage=stage_literal,
            success=False,
            message=f"Benchmark returned non-200 status: {parsed}",
        )

    try:
        oracle_raw = json.loads(parsed.get("text", "{}"))
    except json.JSONDecodeError:
        return BenchmarkResult(
            stage=stage_literal,
            success=False,
            message=f"Benchmark text is not valid JSON: {parsed.get('text')}",
        )

    # Filter oracle to only include current stage results — the conductor
    # returns a cumulative dict (e.g. Diagnosis + Mitigation), but each
    # caller only needs its own stage.
    stage_key = stage.capitalize()
    timing_key = _STAGE_TIMING_KEYS.get(stage_key)
    filtered_oracle: dict[str, Any] = {}
    if stage_key in oracle_raw:
        filtered_oracle[stage_key] = oracle_raw[stage_key]
    if timing_key and timing_key in oracle_raw:
        filtered_oracle[timing_key] = oracle_raw[timing_key]

    oracle = Oracle(stage=stage_literal, data=filtered_oracle)
    stage_result: dict[str, Any] = filtered_oracle.get(stage_key, {})
    if not stage_result.get("success"):
        return BenchmarkResult(
            stage=stage_literal,
            success=False,
            message=f"Benchmark rejected submission for stage '{stage_key}'.",
            oracle=oracle,
        )

    return BenchmarkResult(
        stage=stage_literal,
        success=True,
        message=f"Benchmark accepted submission for stage '{stage_key}'.",
        oracle=oracle,
    )
