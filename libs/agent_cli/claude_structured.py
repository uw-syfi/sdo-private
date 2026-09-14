from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from agentshim import ParsedTurn, ProviderUsage, get_provider

from libs.agent_cli.sandbox import SandboxConfig, build_claude_sandbox_settings

ClaudeSandbox = Literal["read-only", "workspace-write", "danger-full-access"]
CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class ClaudeStructuredResult:
    output_json: str
    session_id: str
    stdout: str
    stderr: str
    usage: ProviderUsage


class ClaudeStructuredExecutionError(RuntimeError):
    pass


def run_claude_structured(
    prompt: str,
    *,
    output_schema: Mapping[str, object],
    cwd: str | Path,
    executable: str = "claude",
    model: str | None = None,
    effort: str = "medium",
    timeout_seconds: float | None = 900,
    sandbox: ClaudeSandbox = "read-only",
    runner: CommandRunner = subprocess.run,
) -> ClaudeStructuredResult:
    return _execute(
        prompt,
        output_schema=output_schema,
        cwd=Path(cwd),
        executable=executable,
        model=model,
        effort=effort,
        timeout_seconds=timeout_seconds,
        sandbox=sandbox,
        runner=runner,
    )


def resume_claude_structured(
    session_id: str,
    prompt: str,
    *,
    output_schema: Mapping[str, object],
    cwd: str | Path,
    executable: str = "claude",
    model: str | None = None,
    effort: str = "medium",
    timeout_seconds: float | None = 900,
    sandbox: ClaudeSandbox = "danger-full-access",
    runner: CommandRunner = subprocess.run,
) -> ClaudeStructuredResult:
    if not session_id.strip():
        raise ValueError("session_id must not be empty")
    return _execute(
        prompt,
        output_schema=output_schema,
        cwd=Path(cwd),
        executable=executable,
        model=model,
        effort=effort,
        timeout_seconds=timeout_seconds,
        sandbox=sandbox,
        runner=runner,
        session_id=session_id,
    )


def _execute(
    prompt: str,
    *,
    output_schema: Mapping[str, object],
    cwd: Path,
    executable: str,
    model: str | None,
    effort: str,
    timeout_seconds: float | None,
    sandbox: ClaudeSandbox,
    runner: CommandRunner,
    session_id: str | None = None,
) -> ClaudeStructuredResult:
    if sandbox not in ("read-only", "workspace-write", "danger-full-access"):
        raise ValueError(f"unsupported Claude sandbox {sandbox!r}")
    command = [
        executable,
        "-p",
        "--dangerously-skip-permissions",
        "--output-format",
        "stream-json",
        "--verbose",
        "--json-schema",
        json.dumps(dict(output_schema)),
        "--effort",
        effort,
    ]
    if model:
        command.extend(["--model", model])
    if session_id:
        command.extend(["--resume", session_id])
    if sandbox in ("read-only", "workspace-write"):
        settings = build_claude_sandbox_settings(
            SandboxConfig(
                deny_write=[str(cwd.resolve())] if sandbox == "read-only" else [],
                confine_native_reads_to=[str(cwd.resolve())],
                denied_bash_executables=["go"] if sandbox == "workspace-write" else [],
                excluded_commands=["sdo detector check"] if sandbox == "workspace-write" else [],
            )
        )
        command.extend(["--settings", json.dumps(settings)])
        if sandbox == "read-only":
            command.extend(["--disallowedTools", "Edit,Write,NotebookEdit"])
    completed = runner(
        command,
        input=prompt,
        cwd=cwd,
        text=True,
        capture_output=True,
        timeout=timeout_seconds,
        check=False,
    )
    if completed.returncode != 0:
        details = completed.stderr.strip() or completed.stdout.strip()
        raise ClaudeStructuredExecutionError(details or f"Claude exited with code {completed.returncode}")
    payload = _result_payload(completed.stdout)
    parsed = _parse_stream(completed.stdout, completed.stderr)
    if parsed.error:
        raise ClaudeStructuredExecutionError(parsed.error)
    structured = parsed.structured_output
    reported_session = parsed.session_id or payload.get("session_id")
    if not isinstance(structured, dict):
        raise ClaudeStructuredExecutionError("Claude did not return structured_output")
    if not isinstance(reported_session, str) or not reported_session:
        raise ClaudeStructuredExecutionError("Claude did not report a session_id")
    return ClaudeStructuredResult(
        output_json=json.dumps(structured),
        session_id=reported_session,
        stdout=completed.stdout,
        stderr=completed.stderr,
        usage=parsed.usage,
    )


def _parse_stream(stdout: str, stderr: str) -> ParsedTurn:
    parser = get_provider("claude").new_parser(lambda _event: None, expect_structured=True)
    for line in stdout.splitlines(keepends=True):
        parser.feed_stdout(line)
    for line in stderr.splitlines(keepends=True):
        parser.feed_stderr(line)
    return parser.finish()


def _result_payload(stdout: str) -> dict[str, object]:
    for line in reversed(stdout.splitlines()):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and payload.get("type") == "result":
            return payload
    raise ClaudeStructuredExecutionError("Claude output did not contain a result event")
