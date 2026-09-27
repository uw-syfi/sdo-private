"""Backend-neutral responder session lifecycle."""

from sdo.agent_runtime.responder.credentials import prepare_claude_home, prepare_codex_home
from sdo.agent_runtime.responder.reflection import (
    ClaudeSessionBackend,
    CodexSessionBackend,
    ReflectionTurn,
    SessionReflector,
)

__all__ = [
    "CodexSessionBackend",
    "ClaudeSessionBackend",
    "ReflectionTurn",
    "SessionReflector",
    "prepare_claude_home",
    "prepare_codex_home",
]
