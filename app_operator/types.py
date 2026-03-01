"""Shared type definitions for the app_operator package."""

from typing import TypedDict


class CommandResult(TypedDict):
    """Result of running a shell command (deploy script or health check)."""

    success: bool
    exit_code: int
    stdout: str
    stderr: str
