"""Render functions for RLM agent prompts."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app_operator.prompts._core import get_loader

if TYPE_CHECKING:
    from app_operator.trajectory import TrajectoryRecorderProtocol


def render_fix_error_task_prompt(
    repo_path: str = "",
    available_variables: str = "",
    available_specialists: str = "",
    error_log_size: str = "",
    attempt: str = "",
    max_attempts: str = "",
    has_original_script: str = "",
    dspy_config: object | None = None,
    recorder: TrajectoryRecorderProtocol | None = None,
) -> str:
    """Render the RLM deployer fix-error task wrapper prompt."""
    return get_loader().render(
        "rlm/deployer_fix_error.jinja2",
        repo_path=repo_path,
        available_variables=available_variables,
        available_specialists=available_specialists,
        error_log_size=error_log_size,
        attempt=attempt,
        max_attempts=max_attempts,
        has_original_script=has_original_script,
        recorder=recorder,
    )
