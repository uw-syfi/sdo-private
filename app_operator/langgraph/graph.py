import re
import time
import subprocess
from pathlib import Path
from typing import Callable, Dict, Any, Optional

from langchain_core.messages import (
    SystemMessage,
    HumanMessage,
    BaseMessage,
    AIMessage,
    ToolMessage,
)
from langgraph.graph import StateGraph, END
from langgraph.prebuilt import create_react_agent

from app_operator.config import Config
from app_operator.filesystem import FileSystemInterface, RealFilesystem
from app_operator.logger import logger
from app_operator.prompts import get_loader
from app_operator.agents.deployer import _analyze_repository, _create_system_prompt
from tools.trajectory import (
    Phase,
    record_phase_start,
    record_phase_end,
    record_user_message,
    record_assistant_message,
    record_tool_call,
)
from app_operator.langgraph.state import OperatorState
from app_operator.langgraph.tools import build_tools
from app_operator.langgraph.models import get_model_context_limit


BLUE = "\033[34m"
GREEN = "\033[32m"
RESET = "\033[0m"


def _extract_token_usage(message: BaseMessage) -> Dict[str, int]:
    if not isinstance(message, AIMessage):
        return {}

    usage = {"input": 0, "output": 0, "total": 0}
    metadata = message.response_metadata or {}

    if "token_usage" in metadata:  # OpenAI
        tu = metadata["token_usage"]
        usage["input"] = tu.get("prompt_tokens", 0)
        usage["output"] = tu.get("completion_tokens", 0)
        usage["total"] = tu.get("total_tokens", 0)
    elif "usage" in metadata:  # Anthropic
        tu = metadata["usage"]
        usage["input"] = tu.get("input_tokens", 0)
        usage["output"] = tu.get("output_tokens", 0)
        usage["total"] = usage["input"] + usage["output"]

    return usage


def _update_usage(state: OperatorState, new_usage: Dict[str, int]) -> None:
    current = state.get("token_usage") or {"input": 0, "output": 0, "total": 0}
    state["token_usage"] = {
        "input": current.get("input", 0) + new_usage.get("input", 0),
        "output": current.get("output", 0) + new_usage.get("output", 0),
        "total": current.get("total", 0) + new_usage.get("total", 0),
    }


def _extract_text(content: Any) -> str:
    """Extract text from message content, handling both string and list formats."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        text_parts = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                text_parts.append(part.get("text", ""))
            elif isinstance(part, str):
                text_parts.append(part)
        return "".join(text_parts)
    return str(content)


def _invoke_agent(
    state: OperatorState,
    agent: Any,
    system_prompt: str,
    user_prompt: str,
    agent_name: str = "Agent",
    context_limit: int = 128000,
) -> tuple[str, list[BaseMessage]]:
    if system_prompt:
        full_prompt = f"{system_prompt}\n\n{user_prompt}"
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt),
        ]
    else:
        full_prompt = user_prompt
        messages = [HumanMessage(content=user_prompt)]

    record_user_message(full_prompt)
    response_messages = list(messages)
    total_usage = {"input": 0, "output": 0, "total": 0}

    print("\n" + "=" * 50)
    print(f"Executing {agent_name}...")
    print("=" * 50 + "\n")

    for chunk in agent.stream({"messages": messages}, stream_mode="updates"):
        for node_name, updates in chunk.items():
            new_messages = updates.get("messages", [])
            if not new_messages:
                continue

            response_messages.extend(new_messages)

            for msg in new_messages:
                if isinstance(msg, AIMessage):
                    usage = _extract_token_usage(msg)
                    total_usage["input"] += usage.get("input", 0)
                    total_usage["output"] += usage.get("output", 0)
                    total_usage["total"] += usage.get("total", 0)

                    if msg.tool_calls:
                        for tool_call in msg.tool_calls:
                            args_str = str(tool_call["args"])
                            if len(args_str) > 500:
                                args_str = f"{args_str[:500]}... (truncated)"
                            print(
                                f"{BLUE}[Tool Use] {tool_call['name']} {args_str}{RESET}"
                            )

                    content_text = _extract_text(msg.content)
                    if content_text:
                        # Print thought/response in default color (usually white/gray)
                        # similar to CLI agent text stream
                        print(f"{content_text}")

                    if usage.get("total", 0) > 0:
                        pct = round((usage["total"] / context_limit) * 100, 1)
                        print(
                            f"\nToken Usage: {pct}% ({usage['total']}/{context_limit})"
                        )

                elif isinstance(msg, ToolMessage):
                    content = _extract_text(msg.content)
                    if len(content) > 500:
                        content = f"{content[:500]}... (truncated)"

                    print(f"{GREEN}[Tool Result] {content}{RESET}")

    print("\n" + "=" * 50 + "\n")

    _update_usage(state, total_usage)

    assistant_text = _last_assistant_text(response_messages)
    record_assistant_message(assistant_text)
    return assistant_text, response_messages


def _last_assistant_text(messages: list[BaseMessage]) -> str:
    for message in reversed(messages):
        content = getattr(message, "content", None)
        if content:
            return _extract_text(content)
    return ""


def _write_log_file(filesystem: FileSystemInterface, path: Path, content: str) -> None:
    if not filesystem.exists(path.parent):
        filesystem.mkdir(path.parent, parents=True, exist_ok=True)
    filesystem.write_text(path, content)


def _run_script(
    repo_path: Path,
    filesystem: FileSystemInterface,
    command: str,
    log_file_path: Optional[Path] = None,
    timeout: int = 900,
) -> Dict[str, Any]:
    start_time = time.time()
    try:
        result = subprocess.run(
            command,
            cwd=str(repo_path),
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        duration = time.time() - start_time
        success = result.returncode == 0
        stdout = result.stdout
        stderr = result.stderr
        exit_code = result.returncode

    except subprocess.TimeoutExpired:
        duration = time.time() - start_time
        success = False
        stdout = ""
        stderr = f"Command timed out after {timeout} seconds"
        exit_code = -1

    except Exception as e:
        duration = time.time() - start_time
        success = False
        stdout = ""
        stderr = f"Error: {str(e)}"
        exit_code = -1

    if log_file_path:
        log_content = (
            f"=== Command ===\n{command}\n\n"
            f"=== Exit Code ===\n{exit_code}\n\n"
            f"=== STDOUT ===\n{stdout}\n\n"
            f"=== STDERR ===\n{stderr}\n"
        )
        _write_log_file(filesystem, log_file_path, log_content)

    record_tool_call(
        tool="bash",
        args={"script": command},
        stdout=stdout,
        stderr=stderr,
        exit_code=exit_code,
        duration=duration,
    )

    return {
        "success": success,
        "exit_code": exit_code,
        "stdout": stdout,
        "stderr": stderr,
    }


def _prepare_error_context(
    deploy_result: Dict[str, Any],
    health_result: Optional[Dict[str, Any]],
    log_file_path: Optional[Path],
    health_check_log_path: Optional[Path],
) -> str:
    context_parts = []

    if log_file_path:
        context_parts.append(f"Full deployment logs available at: {log_file_path}")

    if health_check_log_path:
        context_parts.append(
            f"Health check outputs available at: {health_check_log_path}"
        )

    context_parts.append("## Deployment Script Result")
    context_parts.append(f"Exit Code: {deploy_result['exit_code']}")
    context_parts.append(
        f"Status: {'SUCCESS' if deploy_result['success'] else 'FAILED'}"
    )

    if health_result is not None:
        context_parts.append("\n## Health Check Result")
        context_parts.append(f"Exit Code: {health_result['exit_code']}")
        context_parts.append(
            f"Status: {'SUCCESS' if health_result['success'] else 'FAILED'}"
        )

    return "\n".join(context_parts)


def build_graph(
    llm: Any,
    repo_path: Path,
    config: Config,
    health_check_interval: int,
    filesystem: Optional[FileSystemInterface] = None,
    check_shutdown: Optional[Callable[[], bool]] = None,
):
    if filesystem is None:
        filesystem = RealFilesystem()

    tools = build_tools(repo_path, filesystem)
    analyze_agent = create_react_agent(llm, tools=tools)
    script_agent = create_react_agent(llm, tools=tools)
    fix_agent = create_react_agent(llm, tools=tools)
    monitor_agent = create_react_agent(llm, tools=tools)

    loader = get_loader()

    # Determine model context limit
    model_name = config.agent.model or "gpt-4o"
    context_limit = get_model_context_limit(model_name)

    def analyze_code(state: OperatorState) -> OperatorState:
        if state["analysis_done"]:
            return state

        # Check if analysis files already exist
        sds_dir = repo_path / ".sds"
        analysis_file = sds_dir / "code_analysis.md"
        issues_file = sds_dir / "deployment_issues.md"

        if filesystem.exists(analysis_file) and filesystem.exists(issues_file):
            logger.info("Code analysis files already exist. Skipping analysis.")
            state["analysis_done"] = True
            return state

        record_phase_start(Phase.EXPLORATION)
        system_prompt = loader.render("code_analyzer/system.jinja2")
        user_prompt = loader.render("code_analyzer/user.jinja2", repo_path=repo_path)

        _, messages = _invoke_agent(
            state,
            analyze_agent,
            system_prompt,
            user_prompt,
            agent_name="Code Analyzer",
            context_limit=context_limit,
        )

        record_phase_end("success")
        state["analysis_done"] = True
        state["messages"] = messages
        return state

    def generate_scripts(state: OperatorState) -> OperatorState:
        if state["scripts_done"]:
            return state

        record_phase_start(Phase.SCRIPT_GENERATION)
        system_prompt = _create_system_prompt(config.deployment.platform)
        repo_context = _analyze_repository(repo_path)

        deploy_prompt = loader.render(
            "deployer/generate_script.jinja2",
            system_prompt=system_prompt,
            script_name="deploy.sh",
            repo_context=repo_context,
            target_dir=str(repo_path),
            platform=config.deployment.platform,
        )
        health_prompt = loader.render(
            "deployer/generate_script.jinja2",
            system_prompt=system_prompt,
            script_name="health_check.sh",
            repo_context=repo_context,
            target_dir=str(repo_path),
            platform=config.deployment.platform,
        )

        _, _ = _invoke_agent(
            state,
            script_agent,
            "",
            deploy_prompt,
            agent_name="Script Generator",
            context_limit=context_limit,
        )
        _, _ = _invoke_agent(
            state,
            script_agent,
            "",
            health_prompt,
            agent_name="Script Generator",
            context_limit=context_limit,
        )

        record_phase_end("success")
        state["scripts_done"] = True
        return state

    def deploy_attempt(state: OperatorState) -> OperatorState:
        if check_shutdown and check_shutdown():
            return state

        record_phase_start(
            Phase.DEPLOYMENT,
            {"attempt": state["attempt"], "max_attempts": state["max_attempts"]},
        )

        log_file = (
            repo_path / ".sds" / "logs" / f"deploy_attempt_{state['attempt']}.log"
        )

        print(f"\n{BLUE}Running deployment script...{RESET}")
        print(f"Logging output to: {log_file}")

        result = _run_script(
            repo_path,
            filesystem,
            ".sds/deploy.sh start",
            log_file_path=log_file,
            timeout=config.operator.deploy_timeout,
        )
        state["deploy_result"] = result
        return state

    def health_check(state: OperatorState) -> OperatorState:
        if check_shutdown and check_shutdown():
            return state

        deploy_result = state.get("deploy_result") or {}
        if not deploy_result.get("success"):
            state["health_result"] = None
            record_phase_end("needs_retry")
            return state

        log_file = (
            repo_path / ".sds" / "logs" / f"health_check_attempt_{state['attempt']}.log"
        )

        print(f"\n{BLUE}Running health check script...{RESET}")
        print(f"Logging output to: {log_file}")

        result = _run_script(
            repo_path,
            filesystem,
            ".sds/health_check.sh",
            log_file_path=log_file,
            timeout=120,
        )
        state["health_result"] = result

        if result["success"]:
            record_phase_end("success")
        else:
            record_phase_end("needs_retry")

        return state

    def fix_errors(state: OperatorState) -> OperatorState:
        if check_shutdown and check_shutdown():
            return state

        deploy_result = state.get("deploy_result") or {}
        health_result = state.get("health_result")

        log_file_path = (
            repo_path / ".sds" / "logs" / f"deploy_attempt_{state['attempt']}.log"
        )
        health_check_log_path = None
        if health_result:
            health_check_log_path = (
                repo_path
                / ".sds"
                / "logs"
                / f"health_check_attempt_{state['attempt']}.log"
            )

        error_context = _prepare_error_context(
            deploy_result, health_result, log_file_path, health_check_log_path
        )

        system_prompt = _create_system_prompt(config.deployment.platform)
        previous_summary_note = ""
        if state["attempt"] > 1:
            prev_log_path = (
                repo_path / ".sds" / "logs" / f"fix_summary_{state['attempt'] - 1}.log"
            )
            previous_summary_note = (
                f"\n\nNote: This is attempt #{state['attempt']}. "
                "You can read the summary of the previous fix attempt at:\n"
                f"{prev_log_path}\n"
                "The log files follow the pattern .sds/logs/fix_summary_{attempt}.log. "
                "Please review the previous attempt to avoid repeating mistakes, and "
                "to check if the previous fix was successful."
                "Note that the application may still be failing, but the it's now "
                "failing for a different reason."
            )

        prompt = loader.render(
            "deployer/fix_error.jinja2",
            repo_path=repo_path,
            attempt=state["attempt"],
            max_attempts=state["max_attempts"],
            error_context=error_context,
            previous_summary_note=previous_summary_note,
            deploy_script=repo_path / ".sds" / "deploy.sh",
            health_check_script=repo_path / ".sds" / "health_check.sh",
        )

        response, messages = _invoke_agent(
            state,
            fix_agent,
            system_prompt,
            prompt,
            agent_name="Error Fixer",
            context_limit=context_limit,
        )
        state["messages"] = messages

        match = re.search(r"<summary>(.*?)</summary>", response, re.DOTALL)
        if match:
            summary_text = match.group(1)
            # Handle potential escaped characters
            summary_text = (
                summary_text.replace("\\n", "\n")
                .replace("\\t", "\t")
                .replace("\\r", "\r")
                .strip()
            )
            log_file = (
                repo_path / ".sds" / "logs" / f"fix_summary_{state['attempt']}.log"
            )
            _write_log_file(filesystem, log_file, summary_text)
            state["last_fix_summary"] = summary_text

        state["attempt"] += 1
        return state

    def monitor_health(state: OperatorState) -> OperatorState:
        if check_shutdown and check_shutdown():
            return state

        interval = health_check_interval
        if interval > 0:
            time.sleep(interval)

        state["monitor_count"] += 1
        record_phase_start(Phase.MONITORING, {"cycle": state["monitor_count"]})

        log_file = (
            repo_path
            / ".sds"
            / "logs"
            / "monitor"
            / f"check_{state['monitor_count']}_{time.strftime('%Y%m%d-%H%M%S')}.log"
        )

        result = _run_script(
            repo_path,
            filesystem,
            ".sds/health_check.sh",
            log_file_path=log_file,
            timeout=120,
        )
        state["health_result"] = result
        return state

    def monitor_analyze(state: OperatorState) -> OperatorState:
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

        response, messages = _invoke_agent(
            state,
            monitor_agent,
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
        _write_log_file(filesystem, log_file, response)

        record_phase_end("completed")
        return state

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
    graph.add_node("analyze_code", analyze_code)
    graph.add_node("generate_scripts", generate_scripts)
    graph.add_node("deploy_attempt", deploy_attempt)
    graph.add_node("health_check", health_check)
    graph.add_node("fix_errors", fix_errors)
    graph.add_node("monitor_health", monitor_health)
    graph.add_node("monitor_analyze", monitor_analyze)

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
