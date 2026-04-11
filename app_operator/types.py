"""Shared type definitions for the app_operator package."""

from dataclasses import dataclass
from typing import Any, TypedDict

from libs.agent_cli.trajectory import FaultInjectionMetadata, TokenUsage

__all__ = [
    "CommandResult",
    "HealthVerdict",
    "TokenUsage",
    "FaultInjectionMetadata",
    "TrajectoryCallRecord",
    "ConversationEntry",
]


class CommandResult(TypedDict):
    """Result of running a shell command (deploy script or health check)."""

    success: bool
    exit_code: int
    stdout: str
    stderr: str


@dataclass
class HealthVerdict:
    """Normalized health assessment returned by the health judge."""

    healthy: bool
    assessment: str
    diagnosis: str
    script_was_fixed: bool
    raw_response: str
    false_negative_suspected: bool = False

    @classmethod
    def from_dict(cls, d: dict) -> "HealthVerdict":
        return cls(
            healthy=d.get("healthy", False),
            assessment=d.get("assessment", ""),
            diagnosis=d.get("diagnosis", ""),
            script_was_fixed=d.get("script_was_fixed", False),
            raw_response=d.get("raw_response", ""),
        )


class _TrajectoryCallRecordRequired(TypedDict):
    call_id: int
    phase: str
    start_time: str
    end_time: str | None
    context: dict


class TrajectoryCallRecord(_TrajectoryCallRecordRequired, total=False):
    """A single call-record entry in the trajectory calls list."""

    prompt_version: str
    fallback_occurred: bool
    prompt_artifact: dict[str, Any]


class _ConversationEntryRequired(TypedDict):
    call_id: int
    messages: list


class ConversationEntry(_ConversationEntryRequired, total=False):
    """A conversation entry in a trajectory phase list."""

    prompt_version: str
    fallback_occurred: bool
    prompt_kwargs: dict
    rendered_prompt: str
    prompt_artifact: dict[str, Any]
