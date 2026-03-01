from typing import Callable
from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, Static, RichLog
from textual.containers import Container
from textual.binding import Binding
from textual import work
from rich.text import Text
from rich.panel import Panel

from app_operator.ui.base import OperatorUI

# We need to import AppOperator dynamically or via factory to avoid circular imports?
# The factory is passed to run(), so we just need the type.
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app_operator.cli_agent.operator import AppOperator


class TextualOperatorUI(OperatorUI):
    """Bridge between Operator events and Textual App."""

    def __init__(self, app: "OperatorTUI"):
        self.app = app

    def set_stage(
        self, stage: str, detail: str | None = None, status: str | None = None
    ) -> None:
        self.app.call_from_thread(self.app.update_stage, stage, detail)

    def log(self, message: str, level: str = "info") -> None:
        self.app.call_from_thread(self.app.add_log_message, message, level)

    def on_thinking(self, text: str) -> None:
        self.app.call_from_thread(self.app.add_thinking, text)

    def on_tool_call(self, tool: str, args: dict | str | None = None) -> None:
        self.app.call_from_thread(self.app.add_tool_call, tool, args)

    def on_tool_result(
        self,
        tool: str,
        stdout: str = "",
        stderr: str = "",
        exit_code: int | None = None,
        duration: float | None = None,
    ) -> None:
        self.app.call_from_thread(
            self.app.add_tool_result, tool, stdout, stderr, exit_code, duration
        )

    def close(
        self, status: str | None = None, exit_code: int | None = None
    ) -> None:
        # We don't exit the app immediately on close, we just log it.
        msg = f"Operator finished with status: {status} (Exit Code: {exit_code})"
        self.app.call_from_thread(
            self.app.add_log_message, msg, "success" if exit_code == 0 else "error"
        )


class OperatorTUI(App):
    """Textual TUI for the App Operator."""

    CSS = """
    Screen {
        layout: vertical;
    }

    #stage-container {
        dock: top;
        height: 3;
        content-align: center middle;
    }

    #stage-label {
        text-style: bold;
    }

    RichLog {
        width: 100%;
        height: 100%;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("ctrl+c", "quit", "Quit"),
    ]

    def __init__(self, operator_factory: Callable[[OperatorUI], "AppOperator"]):
        super().__init__()
        self.theme = "flexoki"
        self.operator_factory = operator_factory
        self.operator_thread = None
        self._exit_code = 0

    def compose(self) -> ComposeResult:
        yield Header()
        with Container(id="stage-container"):
            yield Static("Initializing...", id="stage-label")
        yield RichLog(highlight=True, markup=True)
        yield Footer()

    def on_mount(self) -> None:
        self.log_widget = self.query_one(RichLog)
        self.stage_label = self.query_one("#stage-label", Static)
        self.run_operator()

    @work(thread=True)
    def run_operator(self) -> None:
        """Run the operator in a background thread."""
        ui = TextualOperatorUI(self)

        # Configure logger to sink to this UI
        from app_operator.logger import attach_ui_sink

        attach_ui_sink(ui, replace=True)

        try:
            operator = self.operator_factory(ui)
            self._exit_code = operator.run()
        except Exception as e:
            self.call_from_thread(self.add_log_message, f"CRITICAL ERROR: {e}", "error")
            self._exit_code = 1

        self.call_from_thread(
            self.add_log_message,
            "Operator execution finished. Press 'q' to exit.",
            "info",
        )

    def update_stage(self, stage: str, detail: str | None = None) -> None:
        text = f"Stage: {stage}"
        if detail:
            text += f" — {detail}"
        self.stage_label.update(text)

    def add_log_message(self, message: str, level: str = "info") -> None:
        color = "white"
        if level == "error":
            color = "red"
        elif level == "warning":
            color = "yellow"
        elif level == "success":
            color = "green"
        elif level == "info":
            color = "blue"

        # If message comes from loguru it might already be formatted or plain text.
        # We assume clean message here.
        self.log_widget.write(Text(message, style=color))

    def add_thinking(self, text: str) -> None:
        self.log_widget.write(Text(text, style="dim italic"))

    def add_tool_call(self, tool: str, args: dict | str | None = None) -> None:
        args_str = str(args) if args else ""
        content = f"[bold]{tool}[/bold]\n{args_str}"
        panel = Panel(content, title="Tool Call", border_style="blue", expand=False)
        self.log_widget.write(panel)

    def add_tool_result(
        self,
        tool: str,
        stdout: str = "",
        stderr: str = "",
        exit_code: int | None = None,
        duration: float | None = None,
    ) -> None:
        title_parts = ["Tool Result"]
        if exit_code is not None:
            title_parts.append(f"Exit: {exit_code}")
        if duration is not None:
            title_parts.append(f"{duration:.2f}s")

        title = " | ".join(title_parts)
        border_style = "green" if (exit_code == 0 or exit_code is None) else "red"

        content = Text()
        if stdout:
            content.append("\n--- STDOUT ---\n", style="bold")
            content.append(stdout)
        if stderr:
            content.append("\n--- STDERR ---\n", style="bold red")
            content.append(stderr)

        if not stdout and not stderr:
            content.append("(no output)", style="italic")

        panel = Panel(content, title=title, border_style=border_style, expand=False)
        self.log_widget.write(panel)
