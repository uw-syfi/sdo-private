import json
import subprocess
from collections.abc import Callable
from typing import Any, cast

from libs.agent_cli.trajectory import TrajectoryRecorderProtocol

from .base import register_provider
from .cli_agent import CLICodingAgent, CLIGenerationSession
from .events import AgentEventHandler
from .mcp_config import HttpMcpServer, McpServerConfig
from .sandbox import SandboxConfig


class CodexGenerationSession(CLIGenerationSession):
    """Session that parses Codex ``--json`` event stream.

    Codex emits one JSON event per line (JSONL). We only need two for
    resume + correct return value:

    - ``thread.started`` carries ``thread_id`` (the resumable session id).
    - ``item.completed`` with ``item.type == "agent_message"`` carries the
      assistant's final text reply.

    Other event types are ignored for state purposes but still rendered as
    raw lines so terminal output isn't lost.
    """

    def _process_stdout(self, line: str) -> None:
        if not line:
            return
        try:
            data: dict[str, Any] = json.loads(line)
        except json.JSONDecodeError:
            # Non-JSON line (e.g. the trailing "Shell cwd was reset…" notice).
            self.stdout_lines.append(line.rstrip())
            if not self.silent:
                if self._at_line_start:
                    self._log_raw(f"{self.log_prefix} ")
                self._log_raw(line.rstrip() + "\n")
                self._at_line_start = True
            return

        ev_type = data.get("type")
        if ev_type == "thread.started":
            tid = data.get("thread_id")
            if isinstance(tid, str) and tid and self.session_id is None:
                self.session_id = tid
        elif ev_type == "item.completed":
            item_raw = data.get("item")
            if not isinstance(item_raw, dict):
                return
            item = cast("dict[str, Any]", item_raw)
            if item.get("type") != "agent_message":
                return
            text_raw = item.get("text", "")
            text = text_raw if isinstance(text_raw, str) else ""
            if text:
                self.stdout_lines.append(text)
                if self.event_handler:
                    self.event_handler.on_thinking(text)
                if not self.silent:
                    self._log_raw(f"{self.log_prefix} {text}\n")
                    self._at_line_start = True


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
            recorder: Trajectory recorder instance.
            event_handler: Optional event handler for UI updates.
            mcp_servers: Optional list of MCP server configurations.
            sandbox: Not supported for Codex; must be False.
        """
        if sandbox:
            raise NotImplementedError("sandbox is not supported for CodexCodingAgent")
        super().__init__("codex", model, recorder, event_handler, mcp_servers)

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
        args: list[str] = []
        for s in self.mcp_servers:
            prefix = f"mcp_servers.{s.name}"
            if isinstance(s, HttpMcpServer):
                args.extend(["-c", f'{prefix}.url="{s.url}"'])
            else:
                args.extend(["-c", f'{prefix}.command="{s.command}"'])
                if s.args:
                    toml_arr = "[" + ", ".join(f'"{a}"' for a in s.args) + "]"
                    args.extend(["-c", f"{prefix}.args={toml_arr}"])
                for k, v in s.env.items():
                    args.extend(["-c", f'{prefix}.env.{k}="{v}"'])
        return args

    def _get_command(self, prompt: str, resume_session_id: str | None = None) -> list[str]:
        cmd: list[str] = [self.binary_path, "exec"]
        if resume_session_id:
            cmd.extend(["resume", resume_session_id])
        cmd.extend(["--dangerously-bypass-approvals-and-sandbox", "--json"])
        if self.model:
            cmd.extend(["--model", self.model])
        if self.mcp_servers:
            cmd.extend(self._build_mcp_args())
        return cmd

    def _create_session(
        self,
        cmd: list[str],
        cwd: str | None = None,
        timeout: int = 300,
        silent: bool = False,
        recorder: TrajectoryRecorderProtocol | None = None,
        on_process_started: Callable[[subprocess.Popen[str]], None] | None = None,
    ) -> CodexGenerationSession:
        return CodexGenerationSession(
            binary_name=self.binary_name,
            env=self.env,
            log_prefix=self._log_prefix,
            cmd=cmd,
            logger=self.logger,
            cwd=cwd,
            timeout=timeout,
            silent=silent,
            recorder=recorder,
            event_handler=self.event_handler,
            on_process_started=on_process_started,
        )
