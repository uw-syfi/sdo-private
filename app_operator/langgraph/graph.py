from collections.abc import Callable
from pathlib import Path
from typing import Any

from langgraph.graph import END, StateGraph
from langgraph.prebuilt import create_react_agent

from app_operator.config import Config
from app_operator.filesystem import FileSystemInterface, RealFilesystem
from app_operator.langgraph.compaction import make_compaction_hook
from app_operator.langgraph.context import NodeContext
from app_operator.langgraph.models import get_model_context_limit
from app_operator.langgraph.nodes.analyzer import analyze_code
from app_operator.langgraph.nodes.deployer import (
    FixSummaryResponse,
    deploy_attempt,
    fix_errors,
)
from app_operator.langgraph.nodes.generator import generate_scripts
from app_operator.langgraph.nodes.monitor import (
    HealthVerdictResponse,
    health_check,
    monitor_health,
)
from app_operator.langgraph.state import OperatorState
from app_operator.langgraph.tools import build_tools
from app_operator.prompts import PromptLoader
from app_operator.trajectory import NullTrajectoryRecorder, TrajectoryRecorderProtocol


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

    # Create a loader instance with DSPy config (no global singleton needed)
    loader = PromptLoader(dspy_config=config.dspy)

    # Determine model context limit and compaction hook before building tools
    model_name = config.agent.model or "gpt-4o"
    context_limit = get_model_context_limit(model_name)
    compaction_hook = make_compaction_hook(llm, context_limit)

    # NodeContext must exist before tools so the token sink can be shared
    ctx = NodeContext(
        repo_path=repo_path,
        filesystem=filesystem,
        loader=loader,
        config=config,
        context_limit=context_limit,
        recorder=recorder or NullTrajectoryRecorder(),
        check_shutdown=check_shutdown,
    )

    tools = build_tools(
        repo_path,
        filesystem,
        git_integration=config.features.git_integration,
        llm=llm,
        compaction_hook=compaction_hook,
        token_sink=ctx.subagent_token_sink,
    )

    analyze_agent = create_react_agent(llm, tools=tools, pre_model_hook=compaction_hook)
    script_agent = create_react_agent(llm, tools=tools, pre_model_hook=compaction_hook)
    fix_agent = create_react_agent(llm, tools=tools, response_format=FixSummaryResponse, pre_model_hook=compaction_hook)
    health_agent = create_react_agent(
        llm, tools=tools, response_format=HealthVerdictResponse, pre_model_hook=compaction_hook
    )

    def should_fix(state: OperatorState) -> str:
        health_verdict = state.get("health_verdict") or {}
        if health_verdict.get("healthy"):
            if not state.get("health_monitoring", True):
                return "end"
            return "monitor"
        if state["attempt"] < state["max_attempts"]:
            return "fix"
        return "end"

    def should_monitor(state: OperatorState) -> str:
        if ctx.should_shutdown():
            return "end"
        monitor_max = state.get("monitor_max")
        if monitor_max is None:
            return "monitor"
        if state["monitor_count"] < monitor_max:
            return "monitor"
        return "end"

    graph = StateGraph(OperatorState)
    graph.add_node("analyze_code", lambda s: analyze_code(s, ctx, analyze_agent))
    graph.add_node("generate_scripts", lambda s: generate_scripts(s, ctx, script_agent))
    graph.add_node("deploy_attempt", lambda s: deploy_attempt(s, ctx))
    graph.add_node("health_check", lambda s: health_check(s, ctx, health_agent))
    graph.add_node("fix_errors", lambda s: fix_errors(s, ctx, fix_agent))
    graph.add_node("monitor_health", lambda s: monitor_health(s, ctx, health_agent, health_check_interval))

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
    graph.add_conditional_edges(
        "monitor_health",
        should_monitor,
        {
            "monitor": "monitor_health",
            "end": END,
        },
    )

    return graph.compile()
