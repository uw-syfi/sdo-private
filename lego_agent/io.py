from typing import Any, Protocol

import click


class Colors:
    """ANSI color codes for console output."""

    HEADER = "\033[95m"
    BLUE = "\033[94m"
    CYAN = "\033[96m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    ENDC = "\033[0m"
    BOLD = "\033[1m"
    UNDERLINE = "\033[4m"
    GRAY = "\033[90m"
    LIGHT_GRAY = "\033[37m"


class UserIO(Protocol):
    """Protocol for user interaction."""

    def read_prompt(self) -> str:
        """Read the initial user prompt."""
        ...

    async def ask_questions(self, questions: list[str]) -> list[str]:
        """Ask clarifying questions and return answers."""
        ...

    async def prompt_int(self, label: str) -> int:
        """Prompt for an integer value."""
        ...

    def info(self, message: str) -> None:
        """Display information to the user."""
        ...

    def print_stream(self, text: str) -> None:
        """Print text to stdout without newline."""
        ...

    # Rendering methods
    def render_thinking_chunk(self, text: str) -> None:
        """Render a chunk of thinking text."""
        ...

    def render_tool_start(self, name: str, inputs: str) -> None:
        """Render the start of a tool execution."""
        ...

    def render_tool_end(self, name: str, output: str, status: str) -> None:
        """Render the result of a tool execution."""
        ...

    def render_error(self, message: str) -> None:
        """Render an error message."""
        ...

    def render_success(self, message: str) -> None:
        """Render a success message."""
        ...

    def render_info(self, message: str) -> None:
        """Render a general info message."""
        ...

    def render_graph(self, config: dict[str, Any]) -> None:
        """Render the dependency graph from the config."""
        ...


class ConsoleIO:
    """Console implementation of UserIO."""

    def read_prompt(self) -> str:
        try:
            click.echo(f"\n{Colors.BOLD}{Colors.BLUE}Please enter your prompt:{Colors.ENDC}")
            click.echo(f"{Colors.CYAN}> {Colors.ENDC}", nl=False)
            return input()
        except (KeyboardInterrupt, EOFError):
            return ""

    async def ask_questions(self, questions: list[str]) -> list[str]:
        answers: list[str] = []
        for i, q in enumerate(questions, 1):
            click.echo(f"\n{Colors.BOLD}{Colors.YELLOW}Question {i}:{Colors.ENDC} {q}")
            click.echo(f"{Colors.CYAN}Answer: {Colors.ENDC}", nl=False)
            answer = input().strip()
            while not answer:
                click.echo(f"{Colors.RED}Answer cannot be empty.{Colors.ENDC}")
                click.echo(f"{Colors.CYAN}Answer: {Colors.ENDC}", nl=False)
                answer = input().strip()
            answers.append(answer)
        return answers

    async def prompt_int(self, label: str) -> int:
        while True:
            try:
                click.echo(f"{Colors.BOLD}{label}: {Colors.ENDC}", nl=False)
                val = input().strip()
                n = int(val)
                if n > 0:
                    return n
                click.echo(f"{Colors.RED}Please enter a positive integer.{Colors.ENDC}")
            except ValueError:
                click.echo(f"{Colors.RED}Invalid number. Please try again.{Colors.ENDC}")

    def info(self, message: str) -> None:
        click.echo(message)

    def print_stream(self, text: str) -> None:
        click.echo(text, nl=False)

    def render_thinking_chunk(self, text: str) -> None:
        click.echo(f"{Colors.LIGHT_GRAY}{text}{Colors.ENDC}", nl=False)

    def render_tool_start(self, name: str, inputs: str) -> None:
        click.echo(f"\n{Colors.BLUE}[Tool Use] {name}({inputs}){Colors.ENDC}")

    def render_tool_end(self, name: str, output: str, status: str) -> None:
        symbol = ""
        if status == "success":
            symbol = f"{Colors.GREEN}✓{Colors.ENDC} "
        elif status == "error":
            symbol = f"{Colors.RED}✗{Colors.ENDC} "

        click.echo(
            f"\n{Colors.BLUE}[Tool Result] {name}: {symbol}{Colors.ENDC}\n{Colors.LIGHT_GRAY}{output}{Colors.ENDC}"
        )

    def render_error(self, message: str) -> None:
        click.echo(f"{Colors.RED}{message}{Colors.ENDC}")

    def render_success(self, message: str) -> None:
        click.echo(f"{Colors.GREEN}{message}{Colors.ENDC}")

    def render_info(self, message: str) -> None:
        click.echo(message)

    def render_graph(self, config: dict[str, Any]) -> None:
        # For console, we just print a simple text representation or info message
        click.echo(f"\n{Colors.BOLD}{Colors.BLUE}[Graph Generated]{Colors.ENDC}")
        # We could print a tree here, but for now just acknowledge it
