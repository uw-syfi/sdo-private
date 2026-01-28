import json
from typing import Optional, List

from .cli_agent import CLICodingAgent, CLIGenerationSession
from .opencode_events import OpencodeEvent, TextEvent, ToolUseEvent
from app_operator.trajectory import record_tool_call


class OpencodeGenerationSession(CLIGenerationSession):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._at_line_start = True

    def _process_stdout(self, line: str) -> None:
        """Process a line from stdout."""
        if not line:
            return
        try:
            data = json.loads(line)
            event = OpencodeEvent.from_dict(data)
            if event:
                self._handle_event(event)
        except json.JSONDecodeError:
            # Fallback for non-JSON lines
            if not self.silent:
                if self._at_line_start:
                    self._log_raw(f"{self.log_prefix} ")
                self._log_raw(line.rstrip() + "\n")
                self._at_line_start = True

    def _handle_event(self, event: OpencodeEvent):
        """Handle a single parsed Opencode event."""
        # 1. Update State
        if isinstance(event, TextEvent):
            self.stdout_lines.append(event.text)
        elif isinstance(event, ToolUseEvent):
            # Record tool call if it has a completion status
            if event.status in ("success", "error"):
                args = (
                    event.input_data
                    if isinstance(event.input_data, dict)
                    else {"input": event.input_data}
                )
                stdout = str(event.output_data) if event.output_data is not None else ""

                record_tool_call(
                    tool=event.tool_name,
                    args=args,
                    stdout=stdout,
                    # duration is not easily available from event stream
                )

        # 2. Render Output
        if not self.silent:
            self._render_event(event)

    def _render_event(self, event: OpencodeEvent):
        """Render the event to stdout."""
        # Handle streaming text differently from block events
        if isinstance(event, TextEvent):
            self._print_stream_content(event.text)
            return

        # Ensure we start block events on a new line
        if not self._at_line_start:
            self._log_raw("\n")
            self._at_line_start = True

        # Render and print
        output = event.render(self.log_prefix)
        if output:
            self._log_raw(output + "\n")

    def _print_stream_content(self, content: str):
        """Print streaming content with prefix handling."""
        if not content:
            return

        lines = content.split("\n")

        for i, line in enumerate(lines):
            is_last = i == len(lines) - 1

            if is_last:
                if line:
                    if self._at_line_start:
                        self._log_raw(f"{self.log_prefix} ")
                        self._at_line_start = False
                    self._log_raw(line)
            else:
                if self._at_line_start:
                    self._log_raw(f"{self.log_prefix} ")
                self._log_raw(line)
                self._log_raw("\n")
                self._at_line_start = True


from .base import register_provider


@register_provider("opencode")
class OpencodeCodingAgent(CLICodingAgent):
    """Coding agent implementation using the Opencode CLI tool."""

    def __init__(self, model: Optional[str] = None):
        """Initialize the Opencode coding agent.

        Args:
            model: Optional model name to use.
        """
        if not model:
            model = "google-vertex/gemini-3-pro-preview"
        super().__init__("opencode", model)

    @property
    def _log_prefix(self) -> str:
        """Return the log prefix for this agent."""
        return "[Opencode]"

    def _get_command(self, prompt: str) -> List[str]:
        cmd = [self.binary_path, "run", f'"{prompt}"']

        if self.model:
            cmd.extend(["--model", self.model])

        # Output in json format
        cmd.extend(["--format=json"])

        return cmd

    def _create_session(
        self,
        cmd: List[str],
        cwd: Optional[str] = None,
        timeout: int = 300,
        silent: bool = False,
    ) -> OpencodeGenerationSession:
        return OpencodeGenerationSession(
            binary_name=self.binary_name,
            env=self.env,
            log_prefix=self._log_prefix,
            cmd=cmd,
            logger=self.logger,
            cwd=cwd,
            timeout=timeout,
            silent=silent,
        )
