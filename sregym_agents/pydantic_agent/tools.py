"""Tools for the SREGym pydantic-ai agent."""

from __future__ import annotations

import asyncio
import concurrent.futures
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pydantic_ai import RunContext

MAX_OUTPUT_CHARS = 4000


@dataclass
class SREGymDeps:
    namespace: str
    submit_mcp_url: str  # e.g. http://localhost:9954/submit/sse


def exec_bash(ctx: RunContext[SREGymDeps], cmd: str) -> str:
    """Execute a shell command. Returns combined stdout+stderr, truncated at 4000 chars."""
    try:
        result = subprocess.run(  # noqa: S602
            cmd,
            shell=True,
            timeout=60,
            capture_output=True,
            text=True,
        )
        output = result.stdout
        if result.returncode != 0 and result.stderr:
            output += f"\nSTDERR: {result.stderr}"
    except subprocess.TimeoutExpired:
        return "Error: Command timed out after 60 seconds."
    except Exception as e:
        return f"Error executing command: {e}"

    if len(output) > MAX_OUTPUT_CHARS:
        tmp_path = f"/tmp/bash_out_{uuid.uuid4().hex}.txt"
        Path(tmp_path).write_text(output)
        return (
            f"Output truncated ({len(output)} chars). Written to {tmp_path}. "
            f"Use read_file to inspect it (e.g. read_file(path='{tmp_path}', start_line=0, end_line=100))."
        )
    return output or "(no output)"


def read_file(
    ctx: RunContext[SREGymDeps],
    path: str,
    start_line: int = 0,
    end_line: int = 200,
) -> str:
    """Read lines from a file (0-indexed). Returns content in cat -n format."""
    try:
        lines = Path(path).read_text().splitlines()
        start = max(0, start_line)
        end = len(lines) if end_line == -1 else min(end_line, len(lines))
        numbered = "\n".join(f"{start + i + 1:6}\t{line}" for i, line in enumerate(lines[start:end]))
        return numbered or "(empty range)"
    except FileNotFoundError:
        return f"Error: File not found: {path}"
    except Exception as e:
        return f"Error reading file: {e}"


def write_file(ctx: RunContext[SREGymDeps], path: str, content: str) -> str:
    """Write content to a file, creating parent directories as needed."""
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        return f"Written {len(content)} bytes to {path}"
    except Exception as e:
        return f"Error writing file: {e}"


def str_replace_file(
    ctx: RunContext[SREGymDeps],
    path: str,
    old_str: str,
    new_str: str,
) -> str:
    """Replace the first occurrence of old_str with new_str in a file."""
    try:
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


def _run_async(coro):
    """Run *coro* safely even when a pydantic-ai event loop is already running."""
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


def submit_solution(ctx: RunContext[SREGymDeps], ans: str) -> str:
    """Submit to the benchmark via the MCP submit server.

    For diagnosis: provide a natural language description of the fault.
    For mitigation: call with ans='' after applying the fix.
    """
    from contextlib import AsyncExitStack

    from mcp import ClientSession
    from mcp.client.sse import sse_client

    async def _submit() -> str:
        async with AsyncExitStack() as stack:
            transport = await stack.enter_async_context(sse_client(url=ctx.deps.submit_mcp_url))
            session = await stack.enter_async_context(ClientSession(*transport))
            await session.initialize()
            result = await session.call_tool("submit", arguments={"ans": ans})
            text = result.content[0].text if result.content else "(no response)"
            return text

    try:
        result = _run_async(_submit())
        return f"Submission result: {result}"
    except Exception as e:
        return f"Submission error: {e}"
