"""Shared type definitions for the app_operator package."""

from typing import Optional, TypedDict


class CommandResult(TypedDict):
    """Result of running a shell command (deploy script or health check)."""

    success: bool
    exit_code: int
    stdout: str
    stderr: str


class TokenUsage(TypedDict):
    """Token usage statistics from an LLM call."""

    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class _TrajectoryCallRecordRequired(TypedDict):
    call_id: int
    phase: str
    start_time: str
    end_time: Optional[str]
    context: dict


class TrajectoryCallRecord(_TrajectoryCallRecordRequired, total=False):
    """A single call-record entry in the trajectory calls list."""

    prompt_version: str
    fallback_occurred: bool
