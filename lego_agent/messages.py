"""messages.py — Canonical WebSocket message schema for LegoAgent.

Mirrors lego_agent/ui/app/messages.ts exactly. Every type here has a 1:1
counterpart in TypeScript. When adding or changing a message type, update
both files together.

Use TaggedMsg to wrap any ServerMsg with an agent_id for multiplexed streams.
Wire format: {"agent_id": "worker_0", "type": "thinking", "text": "..."}
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass
from typing import Any, Literal, TypeAlias

# ── Server → Client ────────────────────────────────────────────────────────────


@dataclass
class InitMsg:
    type: Literal["init"]
    cwd: str
    model: str
    thinking_budget: int


@dataclass
class ThinkingMsg:
    type: Literal["thinking"]
    text: str


@dataclass
class ToolStartMsg:
    type: Literal["tool_start"]
    name: str
    input: str


@dataclass
class ToolEndMsg:
    type: Literal["tool_end"]
    name: str
    output: str
    status: Literal["success", "error"]


@dataclass
class LogMsg:
    type: Literal["log"]
    message: str
    level: Literal["info", "error", "success"]


@dataclass
class ScriptExecutionMsg:
    type: Literal["script_execution"]
    stream: Literal["stdout", "stderr"]
    data: str


@dataclass
class ExecutionResultMsg:
    type: Literal["execution_result"]
    exit_code: int


@dataclass
class QuestionMsg:
    type: Literal["question"]
    questions: list[str]


@dataclass
class DirOptionsMsg:
    type: Literal["dir_options"]
    options: list[str]


@dataclass
class PathValidationMsg:
    type: Literal["path_validation"]
    path: str
    valid: bool


@dataclass
class GraphMsg:
    type: Literal["graph"]
    config: dict[str, Any]


# The union — this is your ServerMsg "enum"
ServerMsg: TypeAlias = (
    InitMsg
    | ThinkingMsg
    | ToolStartMsg
    | ToolEndMsg
    | LogMsg
    | ScriptExecutionMsg
    | ExecutionResultMsg
    | QuestionMsg
    | DirOptionsMsg
    | PathValidationMsg
    | GraphMsg
)


# ── Client → Server ────────────────────────────────────────────────────────────


@dataclass
class StartMsg:
    type: Literal["start"]
    prompt: str
    work_dir: str


@dataclass
class StopMsg:
    type: Literal["stop"]


@dataclass
class AnswerMsg:
    type: Literal["answer"]
    answers: list[str]


@dataclass
class ListDirsMsg:
    type: Literal["list_dirs"]
    path: str


@dataclass
class ValidatePathMsg:
    type: Literal["validate_path"]
    path: str


ClientMsg: TypeAlias = StartMsg | StopMsg | AnswerMsg | ListDirsMsg | ValidatePathMsg


# ── Tagged Runtime Messages ───────────────────────────────────────────────────


@dataclass
class TaggedMsg:
    """A ServerMsg emitted by a runtime worker, tagged with its agent_id.

    Used to multiplex concurrent agent streams into one serialized stdout
    channel. Serializes to a flat JSON line so the parent process (server.py
    or a terminal reader) can parse and route each message independently.

    Wire format: {"agent_id": "worker_0", "type": "thinking", "text": "..."}
    """

    agent_id: str
    inner: ServerMsg

    def to_json_line(self) -> str:
        """Serialize to a newline-delimited JSON string."""
        d: dict[str, Any] = dataclasses.asdict(self.inner)  # type: ignore[arg-type]
        d["agent_id"] = self.agent_id
        return json.dumps(d)


# ── Parsing ───────────────────────────────────────────────────────────────────

_SERVER_MSG_DISPATCH: dict[str, type] = {
    "init": InitMsg,
    "thinking": ThinkingMsg,
    "tool_start": ToolStartMsg,
    "tool_end": ToolEndMsg,
    "log": LogMsg,
    "script_execution": ScriptExecutionMsg,
    "execution_result": ExecutionResultMsg,
    "question": QuestionMsg,
    "dir_options": DirOptionsMsg,
    "path_validation": PathValidationMsg,
    "graph": GraphMsg,
}


def parse_server_msg(data: dict[str, Any]) -> ServerMsg:
    """Parse a flat dict (e.g. from a JSON line) into the correct ServerMsg variant.

    Ignores unknown keys (like agent_id) so the same dict can be used
    for both tagged and untagged messages.
    """
    tag = data.get("type")
    cls = _SERVER_MSG_DISPATCH.get(str(tag) if tag is not None else "")
    if cls is None:
        raise ValueError(f"Unknown server message type: {tag!r}")
    valid_fields = {f.name for f in dataclasses.fields(cls)}
    return cls(**{k: v for k, v in data.items() if k in valid_fields})  # type: ignore[return-value]
