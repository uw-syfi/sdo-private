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


class ConsoleIO:
    """Console implementation of UserIO."""

    def read_prompt(self) -> str:
        try:
            print("Please enter your prompt (Ctrl+D to finish):")
            lines = []
            while True:
                try:
                    line = input()
                    lines.append(line)
                except EOFError:
                    break
            return "\n".join(lines)
        except KeyboardInterrupt:
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
