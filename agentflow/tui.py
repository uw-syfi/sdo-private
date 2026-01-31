import asyncio
import os
import sys
from pathlib import Path
from typing import List, Callable, Union, TYPE_CHECKING
from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, Input, RichLog, Label
from textual import work
from rich.text import Text
from rich.panel import Panel
from rich import box

from agentflow.io import UserIO

if TYPE_CHECKING:
    from agentflow.engine import AgentflowEngine


class TextualIO:
    """UserIO implementation for Textual TUI."""

    def __init__(self, app: "AgentflowTUI"):
        self.app = app
        self._thinking_buffer = ""

    def _flush_thinking(self) -> None:
        if self._thinking_buffer:
            self.app.write_log(Text(self._thinking_buffer, style="italic dim"))
            self._thinking_buffer = ""

    def read_prompt(self) -> str:
        # Not used by engine in this flow
        return ""

    async def ask_questions(self, questions: List[str]) -> List[str]:
        self._flush_thinking()
        answers = []
        for i, q in enumerate(questions, 1):
            self.app.write_log(Text.from_markup(f"[bold yellow]Question {i}:[/] {q}"))
            self.app.write_log(Text.from_markup("[cyan]Answer: [/]"))
            answer = await self.app.input_queue.get()
            answers.append(answer)
        return answers

    async def prompt_int(self, label: str) -> int:
        self._flush_thinking()
        while True:
            self.app.write_log(Text.from_markup(f"[bold]{label}: [/]"))
            val = await self.app.input_queue.get()
            try:
                n = int(val)
                if n > 0:
                    return n
                self.app.write_log(Text.from_markup("[red]Please enter a positive integer.[/]"))
            except ValueError:
                self.app.write_log(Text.from_markup("[red]Invalid number. Please try again.[/]"))

    def info(self, message: str) -> None:
        self._flush_thinking()
        self.app.write_log(message)

    def print_stream(self, text: str) -> None:
        self._flush_thinking()
        self.app.print_stream(text)

    def render_thinking_chunk(self, text: str) -> None:
        self._thinking_buffer += text
        if "\n" in self._thinking_buffer:
            lines = self._thinking_buffer.split("\n")
            # Write all complete lines
            for line in lines[:-1]:
                # Skip empty lines if they are just separators, but keep them if they are meaningful?
                # RichLog writes a new line for each call.
                if line:
                    self.app.write_log(Text(line, style="dim white"))
                else:
                    self.app.write_log("")
            # Keep the last partial line
            self._thinking_buffer = lines[-1]

    def render_tool_start(self, name: str, inputs: str) -> None:
        self._flush_thinking()
        self.app.write_log(
            Text.from_markup(
                f"\n[bold magenta]➜ Tool Use:[/] [cyan]{name}[/]([dim]{inputs}[/])"))

    def render_tool_end(self, name: str, output: str, status: str) -> None:
        self._flush_thinking()
        symbol = ""
        style = "blue"
        if status == "success":
            symbol = "✓"
            style = "green"
        elif status == "error":
            symbol = "✗"
            style = "red"

        panel = Panel(
            output,
            title=f"{name} [{style}]{symbol}[/]",
            border_style=style,
            title_align="left",
            box=box.ROUNDED,
            padding=(0, 1),
        )
        self.app.write_log(panel)

    def render_error(self, message: str) -> None:
        self._flush_thinking()
        self.app.write_log(Text.from_markup(f"[bold red]{message}[/]"))

    def render_success(self, message: str) -> None:
        self._flush_thinking()
        self.app.write_log(Text.from_markup(f"[bold green]{message}[/]"))

    def render_info(self, message: str) -> None:
        self._flush_thinking()
        self.app.write_log(message)


class AgentflowTUI(App):
    CSS = """
    Screen {
        background: #050505;
        color: #e0e0e0;
    }

    Header {
        background: #0a0a0a;
        color: #666666;
        dock: top;
        height: 1;
        content-align: center middle;
    }

    Footer {
        background: #0a0a0a;
        color: #666666;
        dock: bottom;
        height: 1;
    }

    /* Style the footer keys to look like little buttons or distinct tags */
    Footer > .footer-key {
        background: #1a1a1a;
        color: #aaaaaa;
        padding: 0 1;
    }

    RichLog {
        height: 1fr;
        background: #050505;
        padding: 1 2;
        border: none;
        scrollbar-background: #0a0a0a;
        scrollbar-color: #333333;
        color: #cccccc;
    }

    Input {
        dock: bottom;
        width: 100%;
        height: 5;
        background: #0f0f0f;
        border: wide #333333;
        color: #ffffff;
        padding: 1 2;
        margin: 0 0 0 0;
    }

    Input:focus {
        border: wide #555555;
        background: #141414;
    }

    .workdir {
        background: #0a0a0a;
        color: #444444;
        padding: 0 2;
        text-align: left;
        text-style: italic;
        dock: bottom;
        height: 1;
        width: 100%;
    }
    """

    BINDINGS = [
        ("ctrl+c", "quit", "Quit"),
    ]

    def __init__(self, engine_factory: Callable[[UserIO], "AgentflowEngine"],
                 initial_prompt: str = None, work_dir: str = ".", repo_root: str = None, **kwargs):
        super().__init__(**kwargs)
        self.theme = "flexoki"
        self.engine_factory = engine_factory
        self.initial_prompt = initial_prompt
        self.work_dir = work_dir
        self.repo_root = repo_root
        self.input_queue = asyncio.Queue()
        self.processing = False

    def compose(self) -> ComposeResult:
        yield Header()
        yield Footer()
        yield Input(placeholder="Enter your prompt here...", id="input")
        yield Label(f"Workdir: {os.path.abspath(self.work_dir)}", classes="workdir")
        yield RichLog(id="log", wrap=True)

    async def on_mount(self) -> None:
        self.log_widget = self.query_one(RichLog)
        self.input_widget = self.query_one(Input)

        self.write_log(Text.from_markup("[bold blue]Welcome to Agentflow TUI![/]"))

        if self.initial_prompt:
            self.write_log(Text.from_markup(f"[cyan]> {self.initial_prompt}[/]"))
            self.processing = True
            self.run_agentflow(self.initial_prompt)
        else:
            self.write_log("Type in a task that you want to delegate to agents")
            self.input_widget.focus()

    def write_log(self, message: Union[str, Text]) -> None:
        self.log_widget.write(message)

    def print_stream(self, text: str) -> None:
        self.log_widget.write(text)

    async def on_input_submitted(self, message: Input.Submitted) -> None:
        value = message.value
        self.input_widget.value = ""

        if not self.processing:
            # First input is the prompt
            self.write_log(Text.from_markup(f"[cyan]> {value}[/]"))
            self.processing = True
            self.run_agentflow(value)
        else:
            # Input is answer to question
            self.write_log(Text.from_markup(f"[cyan]> {value}[/]"))
            await self.input_queue.put(value)

    @work
    async def run_agentflow(self, user_prompt: str):
        io = TextualIO(self)
        engine = self.engine_factory(io)

        try:
            result = await engine.run_async(user_prompt)
            self.write_log(
                Text.from_markup(
                    f"\n[green]Success! Script written to: {
                        result.script_path}"
                    "[/]"))

            # Execute the generated script
            self.write_log(Text.from_markup("\n[bold blue]Executing generated script...[/]"))

            env = os.environ.copy()

            # Determine repo root if not provided
            repo_root_path = self.repo_root
            if not repo_root_path:
                current = Path.cwd().resolve()
                for parent in [current, *current.parents]:
                    if (parent / ".git").exists() or (parent / "sds.toml").exists():
                        repo_root_path = str(parent)
                        break
                if not repo_root_path:
                    repo_root_path = str(current)

            env["PYTHONPATH"] = f"{repo_root_path}:{env.get('PYTHONPATH', '')}"

            # Ensure work_dir exists
            work_dir_path = Path(self.work_dir)
            if not work_dir_path.exists():
                work_dir_path.mkdir(parents=True, exist_ok=True)

            self.write_log(Text.from_markup(
                "[bold blue]┌── Script Execution Output ──────────────────────────────────────────[/]"))

            process = await asyncio.create_subprocess_exec(
                sys.executable, str(result.script_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.work_dir,
                env=env
            )

            async def read_stream(stream, color_tag):
                while True:
                    line = await stream.readline()
                    if not line:
                        break
                    try:
                        decoded_line = line.decode().rstrip()
                        self.write_log(
                            Text.from_markup(
                                f"[bold blue]│[/] [{color_tag}]{decoded_line}[/]"))
                    except Exception:
                        # Fallback for decoding errors
                        self.write_log(Text.from_markup(
                            f"[bold blue]│[/] [{color_tag}]{str(line)}[/]"))

            await asyncio.gather(
                read_stream(process.stdout, "white"),
                read_stream(process.stderr, "red")
            )

            return_code = await process.wait()
            self.write_log(Text.from_markup(
                "[bold blue]└─────────────────────────────────────────────────────────────────────[/]"))

            if return_code == 0:
                self.write_log(
                    Text.from_markup(
                        f"\n[bold green]Execution finished successfully (Exit Code: {return_code})[/]"))
            else:
                self.write_log(
                    Text.from_markup(
                        f"\n[bold red]Execution failed (Exit Code: {return_code})[/]"))

            self.write_log(
                "\nExecution finished. You can exit with Ctrl+C or enter a new prompt to restart.")
            self.processing = False

        except Exception as e:
            self.write_log(Text.from_markup(f"\n[red]Error: {e}[/]"))
            import traceback
            traceback.print_exc()
        finally:
            pass
