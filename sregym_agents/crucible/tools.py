"""Tools for the Crucible dual-agent judge loop (pydantic-ai style)."""

from __future__ import annotations

import ast
import asyncio
import json
import logging
import shlex
import subprocess
import uuid
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pydantic_ai import RunContext

logger = logging.getLogger(__name__)

MAX_OUTPUT_CHARS = 4000
BASH_TIMEOUT = 60
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


@dataclass
class SharedState:
    submitted: bool = False
    verdict: str | None = None  # "APPROVED" | "REJECTED" | None


@dataclass
class SREDeps:
    namespace: str
    shared_file: Path
    iteration: int
    stage: str  # "diagnosis" | "mitigation"
    state: SharedState = field(default_factory=SharedState)


@dataclass
class JudgeDeps:
    namespace: str
    shared_file: Path
    iteration: int
    stage: str
    submit_mcp_url: str
    state: SharedState = field(default_factory=SharedState)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _run_bash_sync(cmd: str) -> str:
    """Run *cmd* in a shell, capture stdout+stderr, truncate to MAX_OUTPUT_CHARS."""
    try:
        result = subprocess.run(  # noqa: S602
            cmd,
            shell=True,
            timeout=BASH_TIMEOUT,
            capture_output=True,
            text=True,
        )
        output = result.stdout
        if result.returncode != 0 and result.stderr:
            output += f"\nSTDERR: {result.stderr}"
    except subprocess.TimeoutExpired:
        return f"Error: Command timed out after {BASH_TIMEOUT} seconds."
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


def _check_mutating_kubectl(cmd: str) -> str | None:
    """Return an error message if *cmd* contains a mutating kubectl verb, else None."""
    try:
        tokens = shlex.split(cmd)
    except ValueError:
        return None

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

    stage_key = stage.capitalize()
    stage_result = oracle.get(stage_key, {})
    if not stage_result.get("success"):
        return False, f"Benchmark rejected submission for stage '{stage_key}': {oracle}", oracle

    return True, f"Benchmark accepted submission for stage '{stage_key}'.", oracle


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
    end_line: int = 200,
) -> str:
    """Read lines from a file and return them in cat -n format (0-indexed).

    Args:
        path: Absolute or relative path to the file.
        start_line: First line to read (0-indexed, inclusive).
        end_line: Last line to read (0-indexed, exclusive). Use -1 for end of file.
    """
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


def write_file(ctx: RunContext[SREDeps], path: str, content: str) -> str:
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
    ctx: RunContext[SREDeps],
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


def mark_hypothesis_complete(
    ctx: RunContext[SREDeps],
    diagnosis: str,
    justification: str,
) -> str:
    """Record the SRE agent's fault diagnosis hypothesis and signal completion.

    Args:
        diagnosis: A concise description of the diagnosed fault.
        justification: Evidence and reasoning supporting the diagnosis.
    """
    if not diagnosis.strip():
        return "Error: diagnosis must not be empty."
    if not justification.strip():
        return "Error: justification must not be empty."

    iteration = ctx.deps.iteration
    entry = (
        f"\n### Iteration {iteration} — Agent Hypothesis\n"
        f"**Diagnosis**: {diagnosis}\n"
        f"**Justification**: {justification}\n"
    )
    try:
        with ctx.deps.shared_file.open("a") as fh:
            fh.write(entry)
    except Exception as e:
        return f"Error writing to shared file: {e}"

    ctx.deps.state.submitted = True
    return f"Hypothesis recorded for iteration {iteration}."


def mark_mitigation_complete(
    ctx: RunContext[SREDeps],
    mitigation: str,
    justification: str,
) -> str:
    """Record the SRE agent's mitigation strategy and signal completion.

    Args:
        mitigation: A concise description of the applied or proposed mitigation.
        justification: Evidence and reasoning supporting the mitigation choice.
    """
    if not mitigation.strip():
        return "Error: mitigation must not be empty."
    if not justification.strip():
        return "Error: justification must not be empty."

    iteration = ctx.deps.iteration
    entry = (
        f"\n### Iteration {iteration} — Agent Strategy\n"
        f"**Mitigation**: {mitigation}\n"
        f"**Justification**: {justification}\n"
    )
    try:
        with ctx.deps.shared_file.open("a") as fh:
            fh.write(entry)
    except Exception as e:
        return f"Error writing to shared file: {e}"

    ctx.deps.state.submitted = True
    return f"Mitigation recorded for iteration {iteration}."


# ---------------------------------------------------------------------------
# Judge agent tools
# ---------------------------------------------------------------------------


def exec_bash_readonly(ctx: RunContext[JudgeDeps], cmd: str) -> str:
    """Execute a read-only shell command. Mutating kubectl verbs are blocked.

    Args:
        cmd: The shell command to run (must not mutate cluster state).
    """
    error = _check_mutating_kubectl(cmd)
    if error is not None:
        return error
    return _run_bash_sync(cmd)


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
    iteration = ctx.deps.iteration
    stage = ctx.deps.stage
    status_str = "APPROVED" if verdict else "REJECTED"

    entry = f"\n### Iteration {iteration} — Judge Verdict ({stage})\n- Status: {status_str}\n- Reasoning: {reasoning}\n"

    ctx.deps.state.submitted = True
    ctx.deps.state.verdict = status_str

    benchmark_block = ""
    if verdict:
        try:
            success, message, oracle = asyncio.run(_submit_to_benchmark(ctx.deps.submit_mcp_url, submission_ans, stage))
            oracle_text = json.dumps(oracle, indent=2) if oracle is not None else ""
            benchmark_block = (
                f"\n<benchmark_result>\nsuccess: {success}\nmessage: {message}\n{oracle_text}\n</benchmark_result>\n"
            )
        except Exception as e:
            benchmark_block = f"\n<benchmark_result>\nError submitting to benchmark: {e}\n</benchmark_result>\n"

    full_entry = entry + benchmark_block
    try:
        with ctx.deps.shared_file.open("a") as fh:
            fh.write(full_entry)
    except Exception as e:
        return f"Error writing verdict to shared file: {e}"

    return full_entry
