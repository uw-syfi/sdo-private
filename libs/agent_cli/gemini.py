from .base import register_provider
from typing import Optional, List
import json
import time
from pathlib import Path

from .cli_agent import CLICodingAgent, CLIGenerationSession
from .gemini_events import GeminiEvent, MessageEvent, ToolUseEvent, ToolResultEvent
from .events import AgentEventHandler
from app_operator.trajectory import (
    get_current_call_id,
    get_run_id,
    TrajectoryRecorderProtocol,
)


class GeminiGenerationSession(CLIGenerationSession):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # Initialize state required for stream processing
        self.tool_map = {}
        self.tool_start_times = {}
        self.tool_args = {}
        self._at_line_start = True

        # Capture call_id and run_id for correlation
        self.call_id = get_current_call_id()
        self.run_id = get_run_id()

    def _write_call_metadata(self):
        """Write metadata file to help correlate Gemini session with trajectory call."""
        if self.call_id is None or self.run_id is None:
            return

        try:
            # Write metadata to Gemini's tmp directory
            gemini_tmp_dir = Path.home() / ".gemini" / "tmp"
            if not gemini_tmp_dir.exists():
                return

            # Find the project directory (usually matches cwd)
            if self.cwd:
                project_name = Path(self.cwd).name
                project_dir = gemini_tmp_dir / project_name / "chats"
                if project_dir.exists():
                    metadata_file = project_dir / f"sds_call_{self.call_id:03d}.json"
                    metadata = {
                        "call_id": self.call_id,
                        "run_id": self.run_id,
                        "start_time": time.strftime("%Y-%m-%d %H:%M:%S"),
                        "cwd": self.cwd,
                    }
                    with open(metadata_file, "w") as f:
                        json.dump(metadata, f, indent=2)
        except Exception:
            # Silently fail - this is just metadata for convenience
            pass

    def run(self, prompt: str) -> str:
        """Execute the generation process, writing call metadata first."""
        # Write metadata file to correlate with trajectory
        self._write_call_metadata()

        # Call parent implementation
        return super().run(prompt)

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
                if self.event_handler:
                    self.event_handler.on_thinking(event.content)

        elif isinstance(event, ToolUseEvent):
            if event.tool_id:
                self.tool_map[event.tool_id] = event.tool_name
                self.tool_start_times[event.tool_id] = time.time()
                self.tool_args[event.tool_id] = event.parameters
                if self.event_handler:
                    self.event_handler.on_tool_call(event.tool_name, event.parameters)

        elif isinstance(event, ToolResultEvent):
            if event.tool_id:
                # Inject resolved name into the event for rendering
                event.tool_name_resolved = self.tool_map.get(event.tool_id, "Tool")

                start_time = self.tool_start_times.get(event.tool_id)
                duration = time.time() - start_time if start_time else None
                args = self.tool_args.get(event.tool_id, {})

                self.recorder.add_tool_call(
                    tool=event.tool_name_resolved,
                    args=args,
                    stdout=event.output,
                    duration=duration,
                )

                if self.event_handler:
                    self.event_handler.on_tool_result(
                        tool=event.tool_name_resolved,
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


@register_provider("gemini")
class GeminiCodingAgent(CLICodingAgent):
    """Coding agent implementation using the Gemini CLI tool."""

    def __init__(
        self,
        model: Optional[str] = None,
        recorder: Optional[TrajectoryRecorderProtocol] = None,
        event_handler: Optional[AgentEventHandler] = None,
    ):
        """Initialize the Gemini coding agent.

        Args:
            model: Optional model name to use.
            recorder: Trajectory recorder instance.
            event_handler: Optional event handler for UI updates.
        """
        super().__init__("gemini", model, recorder, event_handler)

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
        recorder: Optional[TrajectoryRecorderProtocol] = None,
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
            recorder=recorder,
            event_handler=self.event_handler,
        )
