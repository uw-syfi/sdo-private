"""Render functions for subagent prompts.

Each function renders a Jinja2 system prompt for one of the subagent analyst
or root synthesis calls.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app_operator.prompts._core import get_loader

if TYPE_CHECKING:
    from app_operator.trajectory import TrajectoryRecorderProtocol


def render_trajectory_analyst_prompt(
    data_description: str = "",
    dspy_config: object | None = None,
    recorder: TrajectoryRecorderProtocol | None = None,
) -> str:
    """Render the trajectory analyst system prompt."""
    return get_loader().render(
        "subagent/trajectory_analyst.jinja2",
        data_description=data_description,
        recorder=recorder,
    )


def render_error_log_analyst_prompt(
    data_description: str = "",
    dspy_config: object | None = None,
    recorder: TrajectoryRecorderProtocol | None = None,
) -> str:
    """Render the error log analyst system prompt."""
    return get_loader().render(
        "subagent/error_log_analyst.jinja2",
        data_description=data_description,
        recorder=recorder,
    )


def render_script_analyst_prompt(
    has_original_script: str = "",
    dspy_config: object | None = None,
    recorder: TrajectoryRecorderProtocol | None = None,
) -> str:
    """Render the script analyst system prompt."""
    return get_loader().render(
        "subagent/script_analyst.jinja2",
        has_original_script=has_original_script,
        recorder=recorder,
    )


def render_repo_analyst_prompt(
    available_files: str = "",
    dspy_config: object | None = None,
    recorder: TrajectoryRecorderProtocol | None = None,
) -> str:
    """Render the repository analyst system prompt."""
    return get_loader().render(
        "subagent/repo_analyst.jinja2",
        available_files=available_files,
        recorder=recorder,
    )


def render_root_synthesis_prompt(
    num_analysts: str = "4",
    dspy_config: object | None = None,
    recorder: TrajectoryRecorderProtocol | None = None,
) -> str:
    """Render the root synthesis system prompt."""
    return get_loader().render(
        "subagent/root_synthesis.jinja2",
        num_analysts=num_analysts,
        recorder=recorder,
    )
