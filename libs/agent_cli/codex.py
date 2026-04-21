import json
import subprocess
import time
from collections.abc import Callable
from typing import Any

from libs.agent_cli.trajectory import TrajectoryRecorderProtocol

from .base import register_provider
from .cli_agent import CLICodingAgent, CLIGenerationSession
from .codex_events import (
    CodexEvent,
    ErrorEvent,
    TextEvent,
    ToolResultEvent,
    ToolUseEvent,
    TurnCompletedEvent,
)
from .events import AgentEventHandler
from .mcp_config import HttpMcpServer, McpServerConfig
from .sandbox import SandboxConfig
from .usage import ProviderUsage, TokenUsage


class CodexGenerationSession(CLIGenerationSession):
    def __init__(self, **kwargs: Any):
        super().__init__(**kwargs)
        self.tool_map: dict[str, str] = {}
        self.tool_start_times: dict[str, float] = {}
        self.tool_args: dict[str, Any] = {}
        # Codex has no single "final message" frame; track the most recent
        # agent_message text so run() can return it as the final result.
        self.final_result: str | None = None
        # Accumulator for per-turn usage; finalized into self.usage at end.
        self._accumulated_tokens = TokenUsage()

    def _process_stdout(self, line: str) -> None:
        if not line:
            return
        try:
            data = json.loads(line)
            event = CodexEvent.from_dict(data)
            if event:
                self._handle_event(event)
        except json.JSONDecodeError:
            # Fallback for non-JSON lines (e.g. codex warnings written
            # before the JSON stream begins).
            self.stdout_lines.append(line.rstrip())
            if not self.silent:
                if self._at_line_start:
                    self._log_raw(f"{self.log_prefix} ")
                self._log_raw(line.rstrip() + "\n")
                self._at_line_start = True

    def _handle_event(self, event: CodexEvent):
        self._update_state(event)
        if not self.silent:
            self._render_event(event)

    def _update_state(self, event: CodexEvent):
        if isinstance(event, TextEvent):
            self.stdout_lines.append(event.text)
            self.final_result = event.text
            if self.event_handler:
                self.event_handler.on_thinking(event.text)

        elif isinstance(event, ToolUseEvent):
            if event.tool_id:
                self.tool_map[event.tool_id] = event.tool_name
                self.tool_start_times[event.tool_id] = time.time()
                self.tool_args[event.tool_id] = event.parameters
                if self.event_handler:
                    self.event_handler.on_tool_call(event.tool_name, event.parameters)

        elif isinstance(event, TurnCompletedEvent):
            self._accumulated_tokens = self._accumulated_tokens + TokenUsage(
                input_tokens=event.input_tokens,
                output_tokens=event.output_tokens,
                cached_input_tokens=event.cached_input_tokens,
                turns=1,
            )
            self.usage = ProviderUsage(
                tokens=self._accumulated_tokens,
                total_cost_usd=None,
                provider="codex",
            )

        elif isinstance(event, ToolResultEvent):
            if event.tool_id:
                event.tool_name_resolved = self.tool_map.get(event.tool_id, "Tool")

                start_time = self.tool_start_times.get(event.tool_id)
                duration = time.time() - start_time if start_time else None
                args = self.tool_args.get(event.tool_id, {})

                self.recorder.add_tool_call(
                    tool=event.tool_name_resolved,
                    args=args,
                    stdout=event.output,
                    exit_code=event.exit_code,
                    duration=duration,
                )
                if self.event_handler:
                    self.event_handler.on_tool_result(
                        tool=event.tool_name_resolved,
                        stdout=event.output,
                        exit_code=event.exit_code,
                        duration=duration,
                    )

    def _render_event(self, event: CodexEvent):
        if isinstance(event, TextEvent):
            self._print_stream_content(event.text)
            return

        if not self._at_line_start:
            self._log_raw("\n")
            self._at_line_start = True

        output = event.render(self.log_prefix)
        if output:
            self._log_raw(output + "\n")

        if isinstance(event, ErrorEvent):
            # Surface error text to the accumulated transcript so callers
            # that read stdout_lines still see it.
            self.stdout_lines.append(event.message)

    def run(self, prompt: str) -> str:
        super().run(prompt)
        if self.final_result:
            return self.final_result
        return "\n".join(self.stdout_lines)


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

    def _get_command(self, prompt: str) -> list[str]:
        cmd = [
            self.binary_path,
            "exec",
            "--dangerously-bypass-approvals-and-sandbox",
            "--json",
        ]
        if self.model:
            cmd.extend(["--model", self.model])
        if self.mcp_servers:
            cmd.extend(self._build_mcp_args())
        # Tell Codex to read the prompt from stdin instead of expecting an
        # inline positional prompt argument.
        cmd.append("-")
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
