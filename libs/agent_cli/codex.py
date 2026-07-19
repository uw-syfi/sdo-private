from __future__ import annotations

import json
import subprocess
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast

from .base import register_provider
from .cli_agent import CLICodingAgent
from .mcp_config import HttpMcpServer, McpServerConfig

if TYPE_CHECKING:
    from .events import AgentEventHandler
    from .sandbox import SandboxConfig
    from .trajectory import TrajectoryRecorderProtocol

CodexSandbox = Literal["read-only", "danger-full-access"]
CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class CodexStructuredResult:
    """Raw structured output and event stream from one Codex CLI turn."""

    output_json: str
    session_id: str
    stdout: str
    stderr: str


class CodexStructuredExecutionError(RuntimeError):
    """Raised when a structured Codex CLI turn exits unsuccessfully."""

    def __init__(self, *, returncode: int, stdout: str, stderr: str) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        details = "\n".join(part.strip() for part in (stderr, stdout) if part.strip())
        super().__init__(details or f"Codex structured execution exited with code {returncode}")


class CodexSessionIdError(RuntimeError):
    """Raised when a fresh structured turn does not report its session ID."""


class CodexStructuredOutputError(OSError):
    """Raised when Codex does not materialize its structured output file."""


def run_codex_structured(
    prompt: str,
    *,
    output_schema: Mapping[str, object],
    cwd: str | Path,
    executable: str = "codex",
    model: str | None = None,
    reasoning_effort: str = "medium",
    timeout_seconds: float | None = 900,
    sandbox: CodexSandbox = "read-only",
    mcp_servers: Sequence[McpServerConfig] = (),
    runner: CommandRunner | None = None,
) -> CodexStructuredResult:
    """Run a fresh Codex turn and return its validated transport artifacts.

    The caller owns domain-level validation of ``output_json``. This boundary
    owns the temporary JSON Schema/output files and the JSONL session event.
    """

    _validate_execution_options(prompt, reasoning_effort, timeout_seconds, sandbox)
    repository = Path(cwd).resolve()
    with tempfile.TemporaryDirectory(prefix="sdo-codex-structured-") as temp_dir:
        root = Path(temp_dir)
        schema_path = root / "output.schema.json"
        output_path = root / "output.json"
        schema_path.write_text(json.dumps(dict(output_schema)), encoding="utf-8")
        command = [
            executable,
            "exec",
            "--sandbox",
            sandbox,
            "--cd",
            str(repository),
            "--output-schema",
            str(schema_path),
            "--output-last-message",
            str(output_path),
            "--json",
        ]
        _append_model_and_config(command, model, reasoning_effort, mcp_servers)
        command.append("-")
        completed = _run(command, prompt=prompt, timeout_seconds=timeout_seconds, runner=runner)
        _raise_for_failure(completed)
        session_id = _codex_session_id(completed.stdout)
        if session_id is None:
            raise CodexSessionIdError("Codex structured execution did not report a fresh thread id")
        output_json = _read_structured_output(output_path)
    return CodexStructuredResult(
        output_json=output_json,
        session_id=session_id,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def resume_codex_structured(
    session_id: str,
    prompt: str,
    *,
    output_schema: Mapping[str, object],
    cwd: str | Path,
    executable: str = "codex",
    model: str | None = None,
    reasoning_effort: str = "medium",
    timeout_seconds: float | None = 900,
    sandbox: CodexSandbox = "danger-full-access",
    mcp_servers: Sequence[McpServerConfig] = (),
    runner: CommandRunner | None = None,
) -> CodexStructuredResult:
    """Resume a Codex session with structured output in the supplied worktree."""

    _validate_execution_options(prompt, reasoning_effort, timeout_seconds, sandbox)
    if not session_id.strip():
        raise ValueError("session_id must not be empty")
    worktree = Path(cwd).resolve()
    with tempfile.TemporaryDirectory(prefix="sdo-codex-resume-") as temp_dir:
        root = Path(temp_dir)
        schema_path = root / "output.schema.json"
        output_path = root / "output.json"
        schema_path.write_text(json.dumps(dict(output_schema)), encoding="utf-8")
        command = [
            executable,
            "exec",
            "resume",
            "-c",
            f'sandbox_mode="{sandbox}"',
            "--output-schema",
            str(schema_path),
            "--output-last-message",
            str(output_path),
            "--json",
        ]
        _append_model_and_config(command, model, reasoning_effort, mcp_servers)
        command.extend([session_id, "-"])
        completed = _run(
            command,
            prompt=prompt,
            timeout_seconds=timeout_seconds,
            runner=runner,
            cwd=worktree,
        )
        _raise_for_failure(completed)
        reported_session_id = _codex_session_id(completed.stdout) or session_id
        output_json = _read_structured_output(output_path)
    return CodexStructuredResult(
        output_json=output_json,
        session_id=reported_session_id,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def _validate_execution_options(
    prompt: str,
    reasoning_effort: str,
    timeout_seconds: float | None,
    sandbox: str,
) -> None:
    if not reasoning_effort.strip():
        raise ValueError("reasoning_effort must not be empty")
    if timeout_seconds is not None and timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    if sandbox not in {"read-only", "danger-full-access"}:
        raise ValueError(f"unsupported Codex sandbox mode: {sandbox!r}")


def _append_model_and_config(
    command: list[str],
    model: str | None,
    reasoning_effort: str,
    mcp_servers: Sequence[McpServerConfig],
) -> None:
    if model:
        command.extend(["--model", model])
    command.extend(["-c", f'model_reasoning_effort="{reasoning_effort}"'])
    command.extend(_build_mcp_args(mcp_servers))


def _build_mcp_args(mcp_servers: Sequence[McpServerConfig]) -> list[str]:
    args: list[str] = []
    for server in mcp_servers:
        prefix = f"mcp_servers.{server.name}"
        if isinstance(server, HttpMcpServer):
            args.extend(["-c", f'{prefix}.url="{server.url}"'])
        else:
            args.extend(["-c", f'{prefix}.command="{server.command}"'])
            if server.args:
                toml_args = "[" + ", ".join(f'"{arg}"' for arg in server.args) + "]"
                args.extend(["-c", f"{prefix}.args={toml_args}"])
            for key, value in server.env.items():
                args.extend(["-c", f'{prefix}.env.{key}="{value}"'])
    return args


def _run(
    command: list[str],
    *,
    prompt: str,
    timeout_seconds: float | None,
    runner: CommandRunner | None,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    selected_runner = runner or subprocess.run
    return selected_runner(
        command,
        input=prompt,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        cwd=cwd,
    )


def _raise_for_failure(completed: subprocess.CompletedProcess[str]) -> None:
    if completed.returncode != 0:
        raise CodexStructuredExecutionError(
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )


def _read_structured_output(output_path: Path) -> str:
    try:
        return output_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CodexStructuredOutputError(str(exc)) from exc


def _codex_session_id(stdout: str) -> str | None:
    for line in stdout.splitlines():
        try:
            raw_event: object = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(raw_event, dict):
            continue
        event = cast("dict[str, object]", raw_event)
        if event.get("type") != "thread.started":
            continue
        thread_id = event.get("thread_id")
        if isinstance(thread_id, str) and thread_id:
            return thread_id
    return None


@register_provider("openai", "codex")
class CodexCodingAgent(CLICodingAgent):
    """Coding agent implementation using the Codex CLI tool."""

    def __init__(
        self,
        model: str | None = None,
        recorder: TrajectoryRecorderProtocol | None = None,
        event_handler: AgentEventHandler | None = None,
        mcp_servers: list[McpServerConfig] | None = None,
        sandbox: bool | SandboxConfig = False,
    ):
        """Initialize the Codex coding agent.

        Args:
            model: Optional model name to use with codex. If None, uses default.
            mcp_servers: Optional list of MCP server configurations.
            sandbox: Not supported for Codex; must be False.
        """
        if sandbox:
            raise NotImplementedError("sandbox is not supported for CodexCodingAgent")
        super().__init__("codex", model, recorder, event_handler, mcp_servers=mcp_servers)

    @property
    def codex_path(self) -> str:
        """Return path to codex binary (for backward compatibility)."""
        return self.binary_path

    @property
    def _log_prefix(self) -> str:
        """Return the log prefix for this agent."""
        return "[Codex]"

    def _build_mcp_args(self) -> list[str]:
        """Build -c flag arguments for MCP server configuration."""
        return _build_mcp_args(self.mcp_servers)

    def _get_command(self, prompt: str) -> list[str]:
        cmd = [self.binary_path, "exec", "--dangerously-bypass-approvals-and-sandbox"]
        if self.model:
            cmd.extend(["--model", self.model])
        if self.mcp_servers:
            cmd.extend(self._build_mcp_args())
        return cmd
