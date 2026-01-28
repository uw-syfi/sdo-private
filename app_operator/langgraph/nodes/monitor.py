import time
from pathlib import Path
from typing import Any, Callable, Optional

from app_operator.filesystem import FileSystemInterface
from app_operator.prompts import PromptLoader
from app_operator.trajectory import Phase, record_phase_start
from app_operator.langgraph.state import OperatorState
from app_operator.langgraph.utils import (
    BLUE,
    RESET,
    invoke_agent,
    run_script,
    write_log_file,
)


def health_check(
    state: OperatorState,
    repo_path: Path,
    filesystem: FileSystemInterface,
    check_shutdown: Optional[Callable[[], bool]],
) -> OperatorState:
    if check_shutdown and check_shutdown():
        return state

    deploy_result = state.get("deploy_result") or {}
    if not deploy_result.get("success"):
        state["health_result"] = None
        return state

    log_file = (
        repo_path / ".sds" / "logs" / f"health_check_attempt_{state['attempt']}.log"
    )

    print(f"\n{BLUE}Running health check script...{RESET}")
    print(f"Logging output to: {log_file}")

    result = run_script(
        repo_path,
        filesystem,
        ".sds/health_check.sh",
        log_file_path=log_file,
        timeout=120,
    )
    state["health_result"] = result

    return state


def monitor_health(
    state: OperatorState,
    repo_path: Path,
    filesystem: FileSystemInterface,
    health_check_interval: int,
    check_shutdown: Optional[Callable[[], bool]],
) -> OperatorState:
    if check_shutdown and check_shutdown():
        return state

    record_phase_start(Phase.MONITORING, {"cycle": state["monitor_count"]})

    interval = health_check_interval
    if interval > 0:
        time.sleep(interval)

    state["monitor_count"] += 1

    log_file = (
        repo_path
        / ".sds"
        / "logs"
        / "monitor"
        / f"check_{state['monitor_count']}_{time.strftime('%Y%m%d-%H%M%S')}.log"
    )

    result = run_script(
        repo_path,
        filesystem,
        ".sds/health_check.sh",
        log_file_path=log_file,
        timeout=120,
    )
    state["health_result"] = result
    return state


def monitor_analyze(
    state: OperatorState,
    repo_path: Path,
    filesystem: FileSystemInterface,
    loader: PromptLoader,
    agent: Any,
    context_limit: int,
) -> OperatorState:
    # We are still in MONITORING phase, so we don't start a new one,
    # but we are calling an agent, so logging will happen naturally.

    health_result = state.get("health_result") or {}
    context_parts = []
    context_parts.append(f"## Health Check #{state['monitor_count']}")
    context_parts.append(f"Exit Code: {health_result.get('exit_code')}")
    context_parts.append(
        f"Status: {'PASSED' if health_result.get('success') else 'FAILED'}"
    )
    context_parts.append(f"Timestamp: {time.strftime('%Y-%m-%d %H:%M:%S')}")

    if health_result.get("stdout"):
        context_parts.append("\n### Output:")
        context_parts.append(health_result.get("stdout"))

    if health_result.get("stderr"):
        context_parts.append("\n### Errors:")
        context_parts.append(health_result.get("stderr"))

    context = "\n".join(context_parts)
    prompt = loader.render(
        "monitor/analyze_health.jinja2", repo_path=repo_path, context=context
    )

    response, messages = invoke_agent(
        state,
        agent,
        "",
        prompt,
        agent_name="Health Monitor",
        context_limit=context_limit,
    )
    state["messages"] = messages

    log_file = (
        repo_path
        / ".sds"
        / "logs"
        / "monitor"
        / f"analysis_{state['monitor_count']}.log"
    )
    write_log_file(filesystem, log_file, response)

    return state
