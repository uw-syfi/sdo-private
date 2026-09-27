"""Structured coding-agent turns for SDO, on top of agentshim.

SDO's deployer, health judge, responder and reflection each run one turn that
must return JSON matching a schema, under one of three access levels. This
module maps those levels onto each provider's own confinement and returns a
provider-neutral result, so callers do not branch on the provider.

Access levels:

- ``read-only``: the agent may read the repository but not write it.
- ``workspace-write``: it may write the repository only; SDO's detector
  gateway (``sdo detector check``) is the one sanctioned way to compile, so
  direct ``go`` is refused where the provider can express that.
- ``danger-full-access``: no confinement, for sessions that must operate the
  cluster.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from agentshim import (
    AgentShimError,
    ClaudeHook,
    ClaudeProvider,
    CliAgent,
    CliExitError,
    CliTimeoutError,
    CodexProvider,
    CodexSandboxConfig,
    OutputSchema,
    SandboxConfig,
    ToolCall,
    TurnRequest,
)

if TYPE_CHECKING:
    from agentshim import AgentEvent, CommandExecutor, Provider, ProviderUsage

AgentProvider = Literal["codex", "claude"]
AccessMode = Literal["read-only", "workspace-write", "danger-full-access"]

AGENT_PROVIDERS: tuple[AgentProvider, ...] = ("codex", "claude")
ACCESS_MODES: tuple[AccessMode, ...] = ("read-only", "workspace-write", "danger-full-access")

#: The command that stays outside Claude's shell sandbox in workspace-write:
#: it starts the no-network validator container and needs the container runtime.
DETECTOR_GATEWAY_COMMAND = "sdo detector check"

#: Refuses direct execution of the executables named on its argv.
DENY_EXECUTABLES_HOOK = str(Path(__file__).absolute().parent / "hooks" / "deny_bash_executables.py")

#: A loaded host can take longer than agentshim's default to answer ``--help``.
_CLI_CHECK_TIMEOUT_S = 60.0

#: Tool names whose ``command`` argument is a shell command: Codex's
#: ``command_execution`` items and Claude's Bash tool.
_SHELL_TOOLS = frozenset({"execute", "Bash"})


class StructuredTurnError(RuntimeError):
    """A structured turn failed or returned something unusable."""


class StructuredTurnTimeout(StructuredTurnError):
    """A structured turn did not finish within its timeout."""

    def __init__(self, timeout_seconds: float) -> None:
        self.timeout_seconds = timeout_seconds
        super().__init__(f"agent turn timed out after {timeout_seconds:g}s")


@dataclass(frozen=True)
class StructuredTurn:
    """What one structured turn produced.

    Attributes:
        output_json: The schema-conformant output, serialized.
        session_id: The provider session, for resuming this conversation.
        usage: Token and cost accounting reported by the provider.
        shell_commands: Every shell command the agent started, in order, for
            confinement audits.
    """

    output_json: str
    session_id: str
    usage: ProviderUsage
    shell_commands: tuple[str, ...] = field(default=())


@dataclass
class _ShellCommandRecorder:
    commands: list[str] = field(default_factory=list)

    def on_event(self, event: AgentEvent) -> None:
        if not isinstance(event, ToolCall) or event.tool not in _SHELL_TOOLS:
            return
        if isinstance(event.args, Mapping):
            command = event.args.get("command")
            if isinstance(command, str):
                self.commands.append(command)


def run_structured_turn(
    provider: AgentProvider,
    prompt: str,
    *,
    output_schema: Mapping[str, object],
    cwd: str | Path,
    access: AccessMode,
    model: str | None = None,
    reasoning_effort: str | None = None,
    timeout_seconds: float | None = None,
    resume_session_id: str | None = None,
    executor: CommandExecutor | None = None,
) -> StructuredTurn:
    """Run one structured turn, fresh or resumed, and return its output.

    Raises:
        StructuredTurnTimeout: The turn exceeded ``timeout_seconds``.
        StructuredTurnError: The CLI failed, or reported no session id or no
            structured output.
        ValueError: An argument is invalid before any process starts.
    """
    if provider not in AGENT_PROVIDERS:
        raise ValueError(f"unsupported agent provider {provider!r}")
    if access not in ACCESS_MODES:
        raise ValueError(f"unsupported access mode {access!r}")
    if timeout_seconds is not None and timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    if reasoning_effort is not None and not reasoning_effort.strip():
        raise ValueError("reasoning_effort must not be empty")
    if resume_session_id is not None and not resume_session_id.strip():
        raise ValueError("resume_session_id must not be empty")

    workspace = Path(cwd).resolve()
    selected, extra_args, extra_env = _provider_for(provider, access, workspace)
    recorder = _ShellCommandRecorder()
    try:
        agent = CliAgent(
            selected,
            model=model,
            executor=executor,
            env={**os.environ, **extra_env},
            event_handler=recorder,
            check_timeout=_CLI_CHECK_TIMEOUT_S,
        )
        session = agent.start_session(cwd=str(workspace), timeout=timeout_seconds)
        if resume_session_id is not None and not session.adopt(resume_session_id):
            raise StructuredTurnError(f"{provider} cannot resume session {resume_session_id!r}")
        with tempfile.TemporaryDirectory(prefix="sdo-structured-turn-") as schema_dir:
            result = session.turn(
                TurnRequest(
                    prompt=prompt,
                    output_schema=OutputSchema(schema=dict(output_schema), host_dir=Path(schema_dir)),
                    reasoning_effort=reasoning_effort,
                    extra_args=extra_args,
                )
            )
    except CliTimeoutError as exc:
        raise StructuredTurnTimeout(exc.timeout) from exc
    except CliExitError as exc:
        # Both streams, stderr first: a provider may report the real cause on
        # either, and callers hand this text back to the next attempt.
        details = "\n".join(part.strip() for part in (exc.stderr, exc.stdout) if part.strip())
        raise StructuredTurnError(f"{provider} exited with code {exc.returncode}: {details or 'no output'}") from exc
    except AgentShimError as exc:
        raise StructuredTurnError(f"{provider} turn failed: {exc}") from exc

    if not isinstance(result.structured_output, dict):
        raise StructuredTurnError(f"{provider} turn returned no structured output")
    session_id = result.session_id or resume_session_id
    if not session_id:
        raise StructuredTurnError(f"{provider} turn did not report a session id")
    return StructuredTurn(
        output_json=json.dumps(result.structured_output),
        session_id=session_id,
        usage=result.usage,
        shell_commands=tuple(recorder.commands),
    )


def _provider_for(
    provider: AgentProvider, access: AccessMode, workspace: Path
) -> tuple[Provider, Sequence[str], dict[str, str]]:
    """Build the agentshim provider, extra CLI arguments and env for *access*."""
    if provider == "codex":
        return CodexProvider(sandbox=CodexSandboxConfig(mode=access)), (), {}
    if access == "danger-full-access":
        return ClaudeProvider(), (), {}
    root = str(workspace)
    if access == "read-only":
        claude = ClaudeProvider(sandbox=SandboxConfig(deny_write=[root], confine_native_reads_to=[root]))
        return claude, ("--disallowedTools", "Edit,Write,NotebookEdit"), claude.sandbox_env
    claude = ClaudeProvider(
        sandbox=SandboxConfig(
            confine_native_reads_to=[root],
            excluded_commands=[DETECTOR_GATEWAY_COMMAND],
        ),
        hooks=[
            ClaudeHook(
                event="PreToolUse",
                matcher="Bash",
                command=[sys.executable, DENY_EXECUTABLES_HOOK, "go"],
            )
        ],
    )
    return claude, (), claude.sandbox_env
