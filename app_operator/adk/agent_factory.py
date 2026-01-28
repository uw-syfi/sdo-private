from typing import Any, Callable, List, Optional

# Assuming imports
try:
    from google.genai.agent import LlmAgent, LoopAgent
except ImportError:

    class LlmAgent:
        def __init__(self, model, tools, instructions, name=None, description=None):
            pass

    class LoopAgent(LlmAgent):
        def __init__(self, name, sub_agents, max_iterations=None, tools=None):
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


def build_loop_agent(
    name: str,
    sub_agents: List[LlmAgent],
    max_iterations: int = 5,
    tools: Optional[List[Callable]] = None,
) -> LoopAgent:
    """Build a LoopAgent.

    Args:
        name: Name of the agent.
        sub_agents: List of agents to loop through.
        max_iterations: Maximum number of iterations.
        tools: Optional list of tools for the loop agent itself.

    Returns:
        Configured LoopAgent instance.
    """
    return LoopAgent(
        name=name,
        sub_agents=sub_agents,
        max_iterations=max_iterations,
        tools=tools,
    )
