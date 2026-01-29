import asyncio
from typing import List, Callable
from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, Input, Log
from textual import work
from textual.binding import Binding

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
            self.app.write_log(f"{Colors.BOLD}{Colors.YELLOW}Question {i}:{Colors.ENDC} {q}")
            self.app.write_log(f"{Colors.CYAN}Answer: {Colors.ENDC}")
            answer = await self.app.input_queue.get()
            answers.append(answer)
        return answers

    async def prompt_int(self, label: str) -> int:
        while True:
            self.app.write_log(f"{Colors.BOLD}{label}: {Colors.ENDC}")
            val = await self.app.input_queue.get()
            try:
                n = int(val)
                if n > 0:
                    return n
                self.app.write_log(f"{Colors.RED}Please enter a positive integer.{Colors.ENDC}")
            except ValueError:
                self.app.write_log(f"{Colors.RED}Invalid number. Please try again.{Colors.ENDC}")

    def info(self, message: str) -> None:
        self.app.write_log(message)

    def print_stream(self, text: str) -> None:
        self.app.print_stream(text)


class AgentflowTUI(App):
    CSS = """
    Log {
        height: 1fr;
        border: solid green;
    }
    Input {
        dock: bottom;
    }
    """
    
    BINDINGS = [
        ("ctrl+c", "quit", "Quit"),
    ]

    def __init__(self, engine_factory: Callable[[UserIO], "AgentflowEngine"], initial_prompt: str = None, **kwargs):
        super().__init__(**kwargs)
        self.engine_factory = engine_factory
        self.initial_prompt = initial_prompt
        self.input_queue = asyncio.Queue()
        self.processing = False

    def compose(self) -> ComposeResult:
        yield Header()
        yield Log(id="log")
        yield Input(placeholder="Enter your prompt here...", id="input")
        yield Footer()

    async def on_mount(self) -> None:
        self.log_widget = self.query_one(Log)
        self.input_widget = self.query_one(Input)
        
        self.write_log(f"{Colors.BOLD}{Colors.BLUE}Welcome to Agentflow TUI!{Colors.ENDC}")
        
        if self.initial_prompt:
            self.write_log(f"{Colors.CYAN}> {self.initial_prompt}{Colors.ENDC}")
            self.processing = True
            self.run_agentflow(self.initial_prompt)
        else:
            self.write_log("Please enter your prompt below.")
            self.input_widget.focus()

    def write_log(self, message: str) -> None:
        self.log_widget.write(message + "\n")

    def print_stream(self, text: str) -> None:
        self.log_widget.write(text)

    async def on_input_submitted(self, message: Input.Submitted) -> None:
        value = message.value
        self.input_widget.value = ""
        
        if not self.processing:
            # First input is the prompt
            self.write_log(f"{Colors.CYAN}> {value}{Colors.ENDC}")
            self.processing = True
            self.run_agentflow(value)
        else:
            # Input is answer to question
            self.write_log(f"{Colors.CYAN}> {value}{Colors.ENDC}")
            await self.input_queue.put(value)

    @work
    async def run_agentflow(self, user_prompt: str):
        io = TextualIO(self)
        engine = self.engine_factory(io)
        
        try:
            result = await engine.run_async(user_prompt)
            self.write_log(f"\n{Colors.GREEN}Success! Script written to: {result.script_path}{Colors.ENDC}")
            
            # Optionally execute?
            # For now just finish.
            self.write_log("\nExecution finished. You can exit with Ctrl+C or enter a new prompt to restart (if implemented).")
        except Exception as e:
            self.write_log(f"\n{Colors.RED}Error: {e}{Colors.ENDC}")
            import traceback
            traceback.print_exc()
        finally:
             pass