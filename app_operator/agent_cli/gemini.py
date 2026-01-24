from typing import Optional, List
import json
import time

from .cli_agent import CLICodingAgent, CLIGenerationSession
from .gemini_events import GeminiEvent, MessageEvent, ToolUseEvent, ToolResultEvent
from tools.trajectory import record_tool_call


class GeminiGenerationSession(CLIGenerationSession):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # Initialize state required for stream processing
        self.tool_map = {}
        self.tool_start_times = {}
        self.tool_args = {}
        self._at_line_start = True

    def _process_stdout(self, line: str) -> None:
        """Process a line from stdout."""
        if not line:
            return
        try:
            data = json.loads(line)
            event = GeminiEvent.from_dict(data)
            if event:
                self._handle_event(event)
        except json.JSONDecodeError:
            # Fallback for non-JSON lines (e.g. YOLO warnings)
            if not self.silent:
                if self._at_line_start:
                    self._log_raw(f"{self.log_prefix} ")
                self._log_raw(line.rstrip() + "\n")
                self._at_line_start = True

    def _handle_event(self, event: GeminiEvent):
        """Handle a single parsed Gemini event."""
        # 1. Update State (Accumulator, Tool Map, Context Injection)
        self._update_state(event)

        # 2. Render Output
        if not self.silent:
            self._render_event(event)

    def _update_state(self, event: GeminiEvent):
        """Update internal state based on the event."""
        if isinstance(event, MessageEvent):
            if event.role == "assistant":
                self.stdout_lines.append(event.content)

        elif isinstance(event, ToolUseEvent):
            if event.tool_id:
                self.tool_map[event.tool_id] = event.tool_name
                self.tool_start_times[event.tool_id] = time.time()
                self.tool_args[event.tool_id] = event.parameters

        elif isinstance(event, ToolResultEvent):
            if event.tool_id:
                # Inject resolved name into the event for rendering
                event.tool_name_resolved = self.tool_map.get(event.tool_id, "Tool")

                start_time = self.tool_start_times.get(event.tool_id)
                duration = time.time() - start_time if start_time else None
                args = self.tool_args.get(event.tool_id, {})

                record_tool_call(
                    tool=event.tool_name_resolved,
                    args=args,
                    stdout=event.output,
                    duration=duration,
                )

    def _render_event(self, event: GeminiEvent):
        """Render the event to stdout."""
        # Handle streaming text differently from block events
        if isinstance(event, MessageEvent):
            if event.role == "assistant":
                self._print_stream_content(event.content)
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


class GeminiCodingAgent(CLICodingAgent):
    """Coding agent implementation using the Gemini CLI tool."""

    def __init__(self, model: Optional[str] = None):
        """Initialize the Gemini coding agent.

        Args:
            model: Optional model name to use.
        """
        super().__init__("gemini", model)

    @property
    def gemini_path(self) -> str:
        """Return path to gemini binary (for backward compatibility)."""
        return self.binary_path

    @property
    def _log_prefix(self) -> str:
        """Return the log prefix for this agent."""
        return "[Gemini]"

    def _get_command(self, prompt: str) -> List[str]:
        cmd = [self.binary_path]

        # Enable yolo mode
        cmd.extend(["-y"])

        if self.model:
            cmd.extend(["--model", self.model])

        # Output in stream-json format
        cmd.extend(["-o", "stream-json"])

        return cmd

    def _create_session(
        self,
        cmd: List[str],
        cwd: Optional[str] = None,
        timeout: int = 300,
        silent: bool = False,
    ) -> GeminiGenerationSession:
        return GeminiGenerationSession(
            binary_name=self.binary_name,
            env=self.env,
            log_prefix=self._log_prefix,
            cmd=cmd,
            logger=self.logger,
            cwd=cwd,
            timeout=timeout,
            silent=silent,
        )
