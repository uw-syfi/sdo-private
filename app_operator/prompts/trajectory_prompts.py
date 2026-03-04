"""
System prompt generation for trajectory recording.
"""

from typing import Any


def get_system_prompt(
    phase: str,
    context: dict[str, Any] | None = None,
    agent_name: str = "AI Agent",
) -> str:
    """Generate system prompt for a phase.

    Args:
        phase: The phase name (e.g. "exploration", "script_generation").
        context: Additional context for the phase.
        agent_name: Name of the agent.

    Returns:
        The formatted system prompt.
    """
    context = context or {}

    if phase == "exploration":
        return (
            f"You are an AI operator ({agent_name}) responsible for exploring "
            f"the codebase and identifying potential deployment issues."
        )
    elif phase == "script_generation":
        return (
            f"You are an AI operator ({agent_name}) responsible for analyzing "
            f"the repository and generating deployment scripts (deploy.sh) and "
            f"health check scripts (health_check.sh)."
        )
    elif phase == "deployment":
        attempt = context.get("attempt", 1)
        max_attempts = context.get("max_attempts", 5)
        return (
            f"You are an AI operator ({agent_name}) responsible for deploying "
            f"the application and fixing any deployment errors. "
            f"Deployment attempt {attempt} of {max_attempts}."
        )
    elif phase == "monitoring":
        cycle = context.get("cycle", 1)
        return (
            f"You are an AI operator ({agent_name}) responsible for analyzing "
            f"application health check results and providing recommendations. "
            f"Monitoring cycle #{cycle}."
        )
    return f"You are an AI operator ({agent_name})."
