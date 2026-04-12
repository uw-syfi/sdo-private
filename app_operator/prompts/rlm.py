"""Render functions for RLM agent prompts.

Renders the task wrapper prompt for the RLM deployer fix-error loop.
When DSPy optimisation is active, the prompt is produced by the optimised
module; otherwise the Jinja2 baseline template is used.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app_operator.prompts._core import DSPyConfigProtocol, get_loader

if TYPE_CHECKING:
    from app_operator.trajectory import TrajectoryRecorderProtocol


def render_fix_error_task_prompt(
    repo_path: str = "",
    available_variables: str = "",
    error_log_size: str = "",
    attempt: str = "",
    max_attempts: str = "",
    has_original_script: str = "",
    dspy_config: DSPyConfigProtocol | None = None,
    recorder: TrajectoryRecorderProtocol | None = None,
) -> str:
    """Render the RLM deployer fix-error task wrapper prompt."""
    return get_loader(dspy_config).render(
        "rlm/deployer_fix_error.jinja2",
        repo_path=repo_path,
        available_variables=available_variables,
        error_log_size=error_log_size,
        attempt=attempt,
        max_attempts=max_attempts,
        has_original_script=has_original_script,
        recorder=recorder,
    )
