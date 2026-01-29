from typing import Any, Callable, List, Optional

from google.adk.agents import LlmAgent, LoopAgent
from google.adk.tools.function_tool import FunctionTool


def build_adk_agent(
    name: str, instruction: str, model: Any, tools: List[Callable], **kwargs: Any
) -> LlmAgent:
    """Build a specialized ADK LLM agent.

    Args:
        name: Name of the agent.
        instruction: System instruction/prompt for the agent.
        model: Model handle/name.
        tools: List of tool functions.
        **kwargs: Additional arguments passed to LlmAgent (e.g. output_schema).

    Returns:
        Configured LlmAgent instance.
    """
    # Wrap callables in FunctionTool
    wrapped_tools = []
    for tool in tools:
        if isinstance(tool, FunctionTool):
            wrapped_tools.append(tool)
        else:
            wrapped_tools.append(FunctionTool(tool))

    return LlmAgent(
        name=name,
        description=f"SDS Agent: {name}",
        model=model,
        tools=wrapped_tools,
        instruction=instruction,
        generate_content_config={"thinking_config": {"include_thoughts": True}},
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
    )
