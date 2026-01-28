from typing import Any, Callable, List

# Assuming imports
try:
    from google.genai.agent import LlmAgent
except ImportError:

    class LlmAgent:
        def __init__(self, model, tools, instructions, name=None, description=None):
            pass


def build_adk_agent(
    name: str, instruction: str, model: Any, tools: List[Callable]
) -> LlmAgent:
    """Build a specialized ADK LLM agent.

    Args:
        name: Name of the agent.
        instruction: System instruction/prompt for the agent.
        model: Model handle/name.
        tools: List of tool functions.

    Returns:
        Configured LlmAgent instance.
    """
    return LlmAgent(
        model=model,
        tools=tools,
        instructions=instruction,
        name=name,
        description=f"SDS Agent: {name}",
    )
