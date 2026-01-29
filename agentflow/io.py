from typing import Protocol, List


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
            return input("Please enter your prompt: ")
        except (KeyboardInterrupt, EOFError):
            return ""

    def ask_questions(self, questions: List[str]) -> List[str]:
        answers = []
        for i, q in enumerate(questions, 1):
            print(f"\nQuestion {i}: {q}")
            answer = input("Answer: ").strip()
            while not answer:
                print("Answer cannot be empty.")
                answer = input("Answer: ").strip()
            answers.append(answer)
        return answers

    def prompt_int(self, label: str) -> int:
        while True:
            try:
                val = input(f"{label}: ").strip()
                n = int(val)
                if n > 0:
                    return n
                print("Please enter a positive integer.")
            except ValueError:
                print("Invalid number. Please try again.")

    def info(self, message: str) -> None:
        print(message)

    def print_stream(self, text: str) -> None:
        print(text, end="", flush=True)
