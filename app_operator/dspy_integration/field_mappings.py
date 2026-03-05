"""Field mappings for DSPy signatures.

Maps Jinja2 template kwargs to DSPy InputField names, handling edge cases
where field names differ between template context and signature definitions.
"""

from pathlib import Path
from typing import Any

# Explicit field mappings for prompts where Jinja2 kwargs don't match DSPy fields.
# Maps prompt_name to a dict of jinja2_kwarg -> dspy_field_name.
EXPLICIT_MAPPINGS: dict[str, dict[str, str]] = {
    "deployer_fix_error": {
        "previous_summary_note": "previous_summary",
    },
    "deployer_summarize": {
        "output_snippet": "deployment_log",
    },
}


def convert_type(value: Any) -> Any:
    """Convert values to DSPy-compatible types.

    Args:
        value: Input value

    Returns:
        Converted value (Path -> str, etc.)
    """
    if isinstance(value, Path):
        return str(value)
    return value


def map_kwargs_to_fields(prompt_name: str, kwargs: dict[str, Any]) -> dict[str, Any]:
    """Map Jinja2 template kwargs to DSPy signature fields.

    Applies explicit mappings first, then auto-maps remaining fields.
    Converts types as needed (Path -> str).

    Args:
        prompt_name: Name of the prompt (e.g., 'deployer_fix_error')
        kwargs: Jinja2 template context kwargs

    Returns:
        Mapped field names with converted types

    Examples:
        >>> map_kwargs_to_fields('deployer_fix_error', {
        ...     'repo_path': Path('/repo'),
        ...     'previous_summary_note': 'Previous attempt timed out',
        ...     'attempt': 1
        ... })
        {
            'repo_path': '/repo',
            'previous_summary': 'Previous attempt timed out',
            'attempt': 1
        }
    """
    # Get explicit mappings for this prompt
    explicit = EXPLICIT_MAPPINGS.get(prompt_name, {})

    # Apply mappings and type conversion
    mapped = {}
    for key, value in kwargs.items():
        # Apply explicit mapping if exists
        field_name = explicit.get(key, key)
        # Convert types
        mapped[field_name] = convert_type(value)

    return mapped


def get_output_field_name(prompt_name: str) -> str:
    """Get the primary output field name for a prompt.

    Most prompts have a single primary output field. This function returns
    the expected field name for extracting the final result.

    Args:
        prompt_name: Name of the prompt

    Returns:
        Name of the primary output field

    Raises:
        KeyError: If prompt not recognized
    """
    # Map prompt names to their primary output field
    OUTPUT_FIELDS = {
        "deployer_system": "system_prompt",
        "deployer_generate_deploy_script": "deployment_script",
        "deployer_generate_health_check": "health_check_script",
        "deployer_generate_script": "deployment_script",  # Note: also has health_check_script
        "deployer_fix_error": "rendered_prompt",
        "deployer_summarize": "rendered_prompt",
        "code_analyzer_system": "system_prompt",
        "code_analyzer_user": "code_analysis",  # Note: also has deployment_issues
        "monitor_analyze_health": "health_status",  # Note: also has is_healthy
        "agentflow_system": "system_prompt",
        "agentflow_user": "script_code",  # Note: also has status, questions
        "agentflow_repair": "repaired_response",
        "subagent_trajectory_analyst": "system_prompt",
        "subagent_error_log_analyst": "system_prompt",
        "subagent_script_analyst": "system_prompt",
        "subagent_repo_analyst": "system_prompt",
        "subagent_root_synthesis": "system_prompt",
        "rlm_deployer_fix_error": "rendered_prompt",
    }

    if prompt_name not in OUTPUT_FIELDS:
        raise KeyError(
            f"No output field mapping for '{prompt_name}'. Available prompts: {', '.join(sorted(OUTPUT_FIELDS.keys()))}"
        )

    return OUTPUT_FIELDS[prompt_name]


def get_all_output_fields(prompt_name: str) -> dict[str, str]:
    """Get all output fields for prompts with multiple outputs.

    Some prompts (like code_analyzer_user) have multiple output fields.
    This returns a dict mapping logical names to field names.

    Args:
        prompt_name: Name of the prompt

    Returns:
        Dict mapping output names to field names

    Examples:
        >>> get_all_output_fields('code_analyzer_user')
        {'code_analysis': 'code_analysis', 'deployment_issues': 'deployment_issues'}
    """
    # Prompts with multiple outputs
    MULTI_OUTPUT = {
        "deployer_generate_script": {
            "deployment_script": "deployment_script",
            "health_check_script": "health_check_script",
        },
        "code_analyzer_user": {
            "code_analysis": "code_analysis",
            "deployment_issues": "deployment_issues",
        },
        "monitor_analyze_health": {
            "health_status": "health_status",
            "is_healthy": "is_healthy",
        },
        "agentflow_user": {
            "script_code": "script_code",
            "status": "status",
            "questions": "questions",
        },
    }

    if prompt_name in MULTI_OUTPUT:
        return MULTI_OUTPUT[prompt_name]

    # Single output prompts - return primary field only
    primary = get_output_field_name(prompt_name)
    return {primary: primary}
