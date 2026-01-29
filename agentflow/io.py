from typing import Protocol, List
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

    def ask_questions(self, questions: List[str]) -> List[str]:
        """Ask clarifying questions and return answers."""
        ...

    def prompt_int(self, label: str) -> int:
        """Prompt for an integer value."""
        ...

    def info(self, message: str) -> None:
        """Display information to the user."""
        ...

    def print_stream(self, text: str) -> None:
        """Print text to stdout without newline."""
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

    def ask_questions(self, questions: List[str]) -> List[str]:
        answers = []
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

    def prompt_int(self, label: str) -> int:
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
