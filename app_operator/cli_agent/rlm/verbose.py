"""Rich-based verbose printer for the RLM loop."""

from __future__ import annotations

from pathlib import Path
from typing import IO, Any

from rich.console import Console, Group
from rich.panel import Panel
from rich.rule import Rule
from rich.text import Text

_MAX_PREVIEW = 400


def _truncate(value: Any, limit: int = _MAX_PREVIEW) -> str:
    text = str(value)
    return text if len(text) <= limit else text[:limit] + "..."


class VerbosePrinter:
    """Print RLM loop events to the terminal and, optionally, a plain log file."""

    def __init__(self, enabled: bool = True, log_file: Path | str | None = None):
        self.enabled = enabled
        self.console = Console() if enabled else Console(quiet=True)
        self._file_handle: IO[str] | None = None
        self._file_console: Console | None = None
        if log_file and enabled:
            path = Path(log_file)
            path.parent.mkdir(parents=True, exist_ok=True)
            self._file_handle = path.open("a", encoding="utf-8")
            self._file_console = Console(
                file=self._file_handle,
                force_terminal=True,
                no_color=True,
                width=160,
                highlight=False,
            )

    def _print(self, *args: Any, **kwargs: Any) -> None:
        self.console.print(*args, **kwargs)
        if self._file_console is not None:
            self._file_console.print(*args, **kwargs)

    def close(self) -> None:
        """Flush and close the log file handle, if present."""
        if self._file_handle is None:
            return
        try:
            self._file_handle.flush()
            self._file_handle.close()
        except OSError:
            pass
        self._file_handle = None
        self._file_console = None

    def __del__(self) -> None:
        self.close()

    def header(self, model: str, max_iterations: int, depth: int) -> None:
        if not self.enabled:
            return
        body = Text()
        body.append("Model: ")
        body.append(model, style="cyan")
        body.append(f"   Max iterations: {max_iterations}   Depth: {depth}")
        self._print()
        self._print(Panel(body, title="RLM", title_align="left"))
        self._print()

    def iteration(self, n: int, total: int) -> None:
        if self.enabled:
            self._print(Rule(f" Iteration {n}/{total} "))

    def llm_call(self, model: str, prompt_len: int) -> None:
        if self.enabled:
            self._print(f"LLM -> {model}  prompt {prompt_len} chars")

    def code_execution(self, description: str, code: str, result: Any, error: bool = False) -> None:
        if not self.enabled:
            return
        parts: list[Any] = [Text("Code:\n" + _truncate(code, 300))]
        result_text = str(result) if result is not None else ""
        if result_text.strip():
            parts.append(Text("\nResult:\n" + _truncate(result_text)))
        title = "execute_code"
        if description:
            title += f"  {description}"
        self._print(Panel(Group(*parts), title=title, title_align="left", border_style="red" if error else "green"))

    def specialist_call(self, specialist: str, task: str, result: str, cached: bool = False) -> None:
        if not self.enabled:
            return
        lines: list[str] = []
        if task:
            lines.append(f"Task: {_truncate(task, 120)}")
        lines.append(f"Result: {_truncate(result)}")
        suffix = " (cached)" if cached else ""
        self._print(Panel("\n".join(lines), title=f"specialist: {specialist}{suffix}", title_align="left"))

    def subagent_call(self, role: str, task: str, result: str) -> None:
        if not self.enabled:
            return
        self._print(Panel(f"Task: {_truncate(task, 120)}\nResult: {_truncate(result)}", title=f"subagent_call {role}"))

    def recursive_call(self, subtask: str, result: str) -> None:
        if not self.enabled:
            return
        self._print(Panel(f"Subtask: {_truncate(subtask, 120)}\nResult: {_truncate(result)}", title="recursive_call"))

    def final_answer(self, answer: str) -> None:
        if self.enabled:
            self._print(Panel(Text(answer), title="Final Answer", title_align="left"))

    def summary(self, iterations: int, token_usage: dict[str, Any]) -> None:
        if not self.enabled:
            return
        prompt = token_usage.get("prompt_tokens", 0)
        completion = token_usage.get("completion_tokens", 0)
        self._print(Rule())
        self._print(f"Iterations: {iterations}   Prompt tokens: {prompt:,}   Completion tokens: {completion:,}")
        self._print(Rule())
