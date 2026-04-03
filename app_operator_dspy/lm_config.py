"""LM configuration for DSPy operator.

Resolves model-specific kwargs (e.g. Vertex AI project, location)
for use with dspy.LM / configure_lm.
"""

import os
import sys

from app_operator_dspy.logger import get_logger

log = get_logger("lm_config")
DEFAULT_MODEL = "vertex_ai/gemini-2.5-pro"
# Timeout per LLM call in seconds. Prevents hung Vertex AI requests
# from blocking the pipeline indefinitely.
LM_TIMEOUT = 600  # 10 minutes


def resolve_vertex_project() -> str:
    """Resolve GCP project ID from VERTEX_PROJECT or GOOGLE_CLOUD_PROJECT."""
    project = os.environ.get("VERTEX_PROJECT") or os.environ.get("GOOGLE_CLOUD_PROJECT")
    if project:
        return project
    log.error("Set VERTEX_PROJECT or GOOGLE_CLOUD_PROJECT")
    sys.exit(1)


def get_lm_kwargs(model: str) -> dict:
    """Build extra kwargs for dspy.LM based on model identifier.

    Args:
        model: LiteLLM model identifier (e.g. vertex_ai/gemini-2.5-pro).

    Returns:
        Dict of kwargs to pass to configure_lm (e.g. vertex_project, vertex_location).
    """
    kwargs = {"timeout": LM_TIMEOUT}
    if model.startswith("vertex_ai/"):
        kwargs["vertex_project"] = resolve_vertex_project()
        kwargs["vertex_location"] = os.environ.get(
            "VERTEX_LOCATION", os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
        )
    return kwargs
