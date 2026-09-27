"""Test doubles for structured agent turns.

SDO runs agents through agentshim, so tests script turns with agentshim's
``FakeExecutor``: each run replays the provider's real stream format through
the real parser, and the test sees the exact argv, prompt and cwd.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agentshim.testing import FakeExecutor, FakeRun, scripted_turn

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from agentshim import CommandRequest


def turn_schema(request: CommandRequest) -> dict[str, Any]:
    """The output schema a scripted turn was asked to satisfy."""
    argv = list(request.argv)
    if "--output-schema" in argv:
        return json.loads(Path(argv[argv.index("--output-schema") + 1]).read_text(encoding="utf-8"))
    return json.loads(argv[argv.index("--json-schema") + 1])


def reply(
    provider: str,
    output: Mapping[str, Any],
    *,
    session_id: str | None,
    commands: Sequence[str] = (),
) -> FakeRun:
    """A successful turn that ran *commands* and returned *output*."""
    tool = "execute" if provider == "codex" else "Bash"
    return scripted_turn(
        provider,
        session_id=session_id,
        structured_output=dict(output),
        tool_calls=[(tool, {"command": command}, "") for command in commands],
    )


def failure(stdout: str = "", stderr: str = "", returncode: int = 1) -> FakeRun:
    """A turn whose CLI exited nonzero."""
    return FakeRun(stdout=[stdout] if stdout else [], stderr=[stderr] if stderr else [], returncode=returncode)


@dataclass
class ScriptedAgent:
    """A ``FakeExecutor`` driven by a function of each turn request."""

    respond: Callable[[CommandRequest], FakeRun]
    executor: FakeExecutor = field(init=False)
    schemas: list[dict[str, Any]] = field(init=False, default_factory=list)

    def __post_init__(self) -> None:
        self.executor = FakeExecutor(self._record_then_respond)

    def _record_then_respond(self, request: CommandRequest) -> FakeRun:
        # A schema file exists only while its turn runs, so read it now.
        self.schemas.append(turn_schema(request))
        return self.respond(request)

    @property
    def requests(self) -> list[CommandRequest]:
        return self.executor.requests

    @property
    def argvs(self) -> list[list[str]]:
        return [list(request.argv) for request in self.executor.requests]

    @property
    def prompts(self) -> list[str]:
        return [request.stdin or "" for request in self.executor.requests]
