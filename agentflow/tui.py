import asyncio
import os
from typing import List, Callable, Union
from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, Input, RichLog, Label
from textual import work
from textual.binding import Binding
from rich.text import Text

from agentflow.io import UserIO, Colors

class TextualIO:
    """UserIO implementation for Textual TUI."""
    
    def __init__(self, app: "AgentflowTUI"):
        self.app = app

    def read_prompt(self) -> str:
        # Not used by engine in this flow
        return ""

    async def ask_questions(self, questions: List[str]) -> List[str]:
        answers = []
        for i, q in enumerate(questions, 1):
            self.app.write_log(Text.from_markup(f"[bold yellow]Question {i}:[/] {q}"))
            self.app.write_log(Text.from_markup("[cyan]Answer: [/]"))
            answer = await self.app.input_queue.get()
            answers.append(answer)
        return answers

    async def prompt_int(self, label: str) -> int:
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
        self.app.write_log(message)

    def print_stream(self, text: str) -> None:
        self.app.print_stream(text)

    def render_thinking_chunk(self, text: str) -> None:
        self.app.print_stream(text)

    def render_tool_start(self, name: str, inputs: str) -> None:
        self.app.write_log(Text.from_markup(f"\n[bold blue][Tool Use] {name}({inputs})[/]"))

    def render_tool_end(self, name: str, output: str, status: str) -> None:
        symbol = ""
        if status == "success":
            symbol = "[green]✓[/] "
        elif status == "error":
            symbol = "[red]✗[/] "
        
        self.app.write_log(Text.from_markup(f"\n[bold blue][Tool Result] {name}: {symbol}[/]\n{output}"))

    def render_error(self, message: str) -> None:
        self.app.write_log(Text.from_markup(f"[bold red]{message}[/]"))

    def render_success(self, message: str) -> None:
        self.app.write_log(Text.from_markup(f"[bold green]{message}[/]"))

    def render_info(self, message: str) -> None:
        self.app.write_log(message)


class AgentflowTUI(App):
    CSS = """
    RichLog {
        height: 1fr;
    }
    """
    
    BINDINGS = [
        ("ctrl+c", "quit", "Quit"),
    ]

    def __init__(self, engine_factory: Callable[[UserIO], "AgentflowEngine"], initial_prompt: str = None, work_dir: str = ".", **kwargs):
        super().__init__(**kwargs)
        self.theme = "flexoki"
        self.engine_factory = engine_factory
        self.initial_prompt = initial_prompt
        self.work_dir = work_dir
        self.input_queue = asyncio.Queue()
        self.processing = False

    def compose(self) -> ComposeResult:
        yield Header()
        yield Label(f"Workdir: {os.path.abspath(self.work_dir)}", classes="workdir")
        yield RichLog(id="log", wrap=True)
        yield Input(placeholder="Enter your prompt here...", id="input")
        yield Footer()

    async def on_mount(self) -> None:
        self.log_widget = self.query_one(RichLog)
        self.input_widget = self.query_one(Input)
        
        self.write_log(Text.from_markup("[bold blue]Welcome to Agentflow TUI![/]"))
        
        if self.initial_prompt:
            self.write_log(Text.from_markup(f"[cyan]> {self.initial_prompt}[/]"))
            self.processing = True
            self.run_agentflow(self.initial_prompt)
        else:
            self.write_log("Please enter your prompt below.")
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
            self.write_log(Text.from_markup(f"\n[green]Success! Script written to: {result.script_path}[/]"))
            
            # Optionally execute?
            # For now just finish.
            self.write_log("\nExecution finished. You can exit with Ctrl+C or enter a new prompt to restart (if implemented).")
        except Exception as e:
            self.write_log(Text.from_markup(f"\n[red]Error: {e}[/]"))
            import traceback
            traceback.print_exc()
        finally:
             pass