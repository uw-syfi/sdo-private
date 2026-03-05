import functools
from collections.abc import Callable
from pathlib import Path
from typing import Any

from langgraph.graph import END, StateGraph
from langgraph.prebuilt import create_react_agent

from app_operator.config import Config
from app_operator.filesystem import FileSystemInterface, RealFilesystem
from app_operator.langgraph.models import get_model_context_limit
from app_operator.langgraph.nodes.analyzer import analyze_code
from app_operator.langgraph.nodes.deployer import deploy_attempt, fix_errors
from app_operator.langgraph.nodes.generator import generate_scripts
from app_operator.langgraph.nodes.monitor import (
    health_check,
    monitor_analyze,
    monitor_health,
)
from app_operator.langgraph.state import OperatorState
from app_operator.langgraph.tools import build_tools
from app_operator.prompts import PromptLoader
from app_operator.trajectory import TrajectoryRecorderProtocol


def build_graph(
    llm: Any,
    repo_path: Path,
    config: Config,
    health_check_interval: int,
    filesystem: FileSystemInterface | None = None,
    check_shutdown: Callable[[], bool] | None = None,
    recorder: TrajectoryRecorderProtocol | None = None,
):
    if filesystem is None:
        filesystem = RealFilesystem()

    tools = build_tools(repo_path, filesystem, git_integration=config.operator.phase.git_integration)
    analyze_agent = create_react_agent(llm, tools=tools)
    script_agent = create_react_agent(llm, tools=tools)
    fix_agent = create_react_agent(llm, tools=tools)
    monitor_agent = create_react_agent(llm, tools=tools)

    # Create a loader instance with DSPy config (no global singleton needed)
    loader = PromptLoader(dspy_config=config.dspy)

    # Determine model context limit
    model_name = config.agent.model or "gpt-4o"
    context_limit = get_model_context_limit(model_name)

    # Bind dependencies to nodes
    analyze_node = functools.partial(
        analyze_code,
        repo_path=repo_path,
        filesystem=filesystem,
        loader=loader,
        agent=analyze_agent,
        context_limit=context_limit,
        recorder=recorder,
    )

    generate_node = functools.partial(
        generate_scripts,
        operator_config=config,
        repo_path=repo_path,
        loader=loader,
        agent=script_agent,
        context_limit=context_limit,
        recorder=recorder,
    )

    deploy_node = functools.partial(
        deploy_attempt,
        repo_path=repo_path,
        filesystem=filesystem,
        operator_config=config,
        check_shutdown=check_shutdown,
        recorder=recorder,
    )

    health_node = functools.partial(
        health_check,
        repo_path=repo_path,
        filesystem=filesystem,
        check_shutdown=check_shutdown,
        recorder=recorder,
    )

    fix_node = functools.partial(
        fix_errors,
        repo_path=repo_path,
        filesystem=filesystem,
        operator_config=config,
        loader=loader,
        agent=fix_agent,
        context_limit=context_limit,
        check_shutdown=check_shutdown,
        recorder=recorder,
    )

    monitor_health_node_bound = functools.partial(
        monitor_health,
        repo_path=repo_path,
        filesystem=filesystem,
        health_check_interval=health_check_interval,
        check_shutdown=check_shutdown,
        recorder=recorder,
    )

    monitor_analyze_node_bound = functools.partial(
        monitor_analyze,
        repo_path=repo_path,
        filesystem=filesystem,
        loader=loader,
        agent=monitor_agent,
        context_limit=context_limit,
        recorder=recorder,
    )

    def should_fix(state: OperatorState) -> str:
        if (state.get("health_result") or {}).get("success"):
            return "monitor"
        if state["attempt"] < state["max_attempts"]:
            return "fix"
        return "end"

    def should_monitor(state: OperatorState) -> str:
        if check_shutdown and check_shutdown():
            return "end"
        monitor_max = state.get("monitor_max")
        if monitor_max is None:
            return "monitor"
        if state["monitor_count"] < monitor_max:
            return "monitor"
        return "end"

    graph = StateGraph(OperatorState)
    graph.add_node("analyze_code", analyze_node)
    graph.add_node("generate_scripts", generate_node)
    graph.add_node("deploy_attempt", deploy_node)
    graph.add_node("health_check", health_node)
    graph.add_node("fix_errors", fix_node)
    graph.add_node("monitor_health", monitor_health_node_bound)
    graph.add_node("monitor_analyze", monitor_analyze_node_bound)

    graph.set_entry_point("analyze_code")
    graph.add_edge("analyze_code", "generate_scripts")
    graph.add_edge("generate_scripts", "deploy_attempt")
    graph.add_edge("deploy_attempt", "health_check")

    graph.add_conditional_edges(
        "health_check",
        should_fix,
        {
            "monitor": "monitor_health",
            "fix": "fix_errors",
            "end": END,
        },
    )

    graph.add_edge("fix_errors", "deploy_attempt")
    graph.add_edge("monitor_health", "monitor_analyze")
    graph.add_conditional_edges(
        "monitor_analyze",
        should_monitor,
        {
            "monitor": "monitor_health",
            "end": END,
        },
    )

    return graph.compile()
