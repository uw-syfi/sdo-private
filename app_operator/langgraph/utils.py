import subprocess
import time
from pathlib import Path
from typing import Any

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from app_operator.filesystem import FileSystemInterface
from app_operator.langgraph.message_utils import extract_text
from app_operator.langgraph.state import OperatorState
from app_operator.langgraph.trajectory_handler import LangGraphTrajectoryHandler
from app_operator.trajectory import TrajectoryRecorderProtocol

BLUE = "\033[34m"
GREEN = "\033[32m"
RESET = "\033[0m"

MAX_DISPLAY_CONTENT = 100


def _extract_token_usage(message: BaseMessage) -> dict[str, int]:
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


def _update_usage(state: OperatorState, new_usage: dict[str, int]) -> None:
    current = state.get("token_usage") or {"input": 0, "output": 0, "total": 0}
    state["token_usage"] = {
        "input": current.get("input", 0) + new_usage.get("input", 0),
        "output": current.get("output", 0) + new_usage.get("output", 0),
        "total": current.get("total", 0) + new_usage.get("total", 0),
    }


def _last_assistant_text(messages: list[BaseMessage]) -> str:
    for message in reversed(messages):
        content = getattr(message, "content", None)
        if content:
            return extract_text(content)
    return ""


def invoke_agent(
    state: OperatorState,
    agent: Any,
    system_prompt: str,
    user_prompt: str,
    agent_name: str = "Agent",
    context_limit: int = 128000,
    recorder: TrajectoryRecorderProtocol | None = None,
) -> tuple[str, list[BaseMessage]]:
    handler = LangGraphTrajectoryHandler(recorder)

    if system_prompt:
        messages: list[BaseMessage] = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt),
        ]
        # We record the user part of the prompt
        handler.on_user_message(user_prompt)
    else:
        messages: list[BaseMessage] = [HumanMessage(content=user_prompt)]
        handler.on_user_message(user_prompt)

    response_messages = list(messages)
    total_usage = {"input": 0, "output": 0, "total": 0}

    print("\n" + "=" * 50)
    print(f"Executing {agent_name}...")
    print("=" * 50 + "\n")

    for chunk in agent.stream({"messages": messages}, stream_mode="updates"):
        for _node_name, updates in chunk.items():
            new_messages = updates.get("messages", [])
            if not new_messages:
                continue

            response_messages.extend(new_messages)

            for msg in new_messages:
                handler.process_message(msg)

                if isinstance(msg, AIMessage):
                    usage = _extract_token_usage(msg)
                    total_usage["input"] += usage.get("input", 0)
                    total_usage["output"] += usage.get("output", 0)
                    total_usage["total"] += usage.get("total", 0)

                    if msg.tool_calls:
                        for tool_call in msg.tool_calls:
                            args_str = str(tool_call["args"])
                            if len(args_str) > MAX_DISPLAY_CONTENT:
                                args_str = f"{args_str[:MAX_DISPLAY_CONTENT]}... (truncated)"
                            print(f"{BLUE}[Tool Use] {tool_call['name']} {args_str}{RESET}")

                    content_text = extract_text(msg.content)
                    if content_text:
                        # Print thought/response in default color (usually white/gray)
                        # similar to CLI agent text stream
                        print(f"{content_text}")

                    if usage.get("total", 0) > 0:
                        pct = round((usage["total"] / context_limit) * 100, 1)
                        print(f"\nToken Usage: {pct}% ({usage['total']}/{context_limit})")

                elif isinstance(msg, ToolMessage):
                    content = extract_text(msg.content)
                    if len(content) > MAX_DISPLAY_CONTENT:
                        content = f"{content[:MAX_DISPLAY_CONTENT]}... (truncated)"

                    print(f"{GREEN}[Tool Result] {content}{RESET}")

    print("\n" + "=" * 50 + "\n")

    _update_usage(state, total_usage)

    assistant_text = _last_assistant_text(response_messages)
    return assistant_text, response_messages


def write_log_file(filesystem: FileSystemInterface, path: Path, content: str) -> None:
    if not filesystem.exists(path.parent):
        filesystem.mkdir(path.parent, parents=True, exist_ok=True)
    filesystem.write_text(path, content)


def run_script(
    repo_path: Path,
    filesystem: FileSystemInterface,
    command: str,
    log_file_path: Path | None = None,
    timeout: int = 900,
    recorder: TrajectoryRecorderProtocol | None = None,
) -> dict[str, Any]:
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
        success = result.returncode == 0
        stdout = result.stdout
        stderr = result.stderr
        exit_code = result.returncode

    except subprocess.TimeoutExpired:
        success = False
        stdout = ""
        stderr = f"Command timed out after {timeout} seconds"
        exit_code = -1

    except Exception as e:
        success = False
        stdout = ""
        stderr = f"Error: {e!s}"
        exit_code = -1

    duration = time.time() - start_time

    if log_file_path:
        log_content = (
            f"=== Command ===\n{command}\n\n"
            f"=== Exit Code ===\n{exit_code}\n\n"
            f"=== STDOUT ===\n{stdout}\n\n"
            f"=== STDERR ===\n{stderr}\n"
        )
        write_log_file(filesystem, log_file_path, log_content)

    if recorder:
        recorder.add_tool_call(
            tool="bash",
            args={"command": command},
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
