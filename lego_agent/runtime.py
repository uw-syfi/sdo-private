from __future__ import annotations

import asyncio
import io
import json
import re
import sys
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    Any,
    Protocol,
    runtime_checkable,
)

import yaml
from langchain_core.messages import HumanMessage
from langchain_core.tools import BaseTool, StructuredTool, tool  # pyright: ignore[reportUnknownVariableType]
from langgraph.prebuilt import create_react_agent  # pyright: ignore[reportUnknownVariableType, reportDeprecated]
from loguru import logger

if TYPE_CHECKING:
    from collections.abc import Callable

    from langchain_core.runnables import RunnableConfig

from lego_agent.config import load_config
from lego_agent.io import Colors
from lego_agent.llm import build_llm
from lego_agent.streaming import extract_tool_result, parse_chunk_content
from libs.sds_core.filesystem import RealFilesystem
from libs.sds_core.tools import build_tools

# Default timeout (seconds) for agent generation calls
DEFAULT_AGENT_TIMEOUT = 300
DEFAULT_FAN_OUT_MAX_WORKERS = 4  # maximum parallel workers for FanOut
DEFAULT_JUDGE_LOOP_MAX_ITERATIONS = 10  # default maximum iterations for JudgeLoop


@runtime_checkable
class Runnable(Protocol):
    """Protocol for any component that can execute a task."""

    def run(self, input_data: Any) -> Any: ...


@runtime_checkable
class AsyncRunnable(Protocol):
    """Protocol for components that support async execution."""

    def run(self, input_data: Any) -> Any: ...

    async def generate_async(self, prompt: str, timeout: int, output: io.StringIO | None = None) -> str: ...


class LangGraphAgent:
    """Agent wrapper around LangGraph prebuilt React agent."""

    def __init__(
        self,
        model_name: str,
        llm: Any,
        tools: list[Callable[..., Any]],
        instruction: str = "",
        agent_name: str = "LegoAgentWorker",
    ):
        self.model_name = model_name
        self.llm = llm
        self.tools = self._wrap_tools(tools)
        self.instruction = instruction
        self.agent_name = agent_name

        # Create the graph
        self.graph: Any = create_react_agent(  # pyright: ignore[reportDeprecated]
            model=self.llm, tools=self.tools, prompt=self.instruction
        )

    def _wrap_tools(self, tools: list[Callable[..., Any]]) -> list[BaseTool]:
        """Wrap ADK tools into LangChain StructuredTools."""
        wrapped_tools: list[BaseTool] = []
        for t in tools:
            if isinstance(t, StructuredTool):
                wrapped_tools.append(t)
            elif callable(t):
                wrapped_tools.append(tool(t))
            else:
                logger.warning(f"Unknown tool type: {type(t)}")
        return wrapped_tools

    def generate(
        self,
        prompt: str,
        cwd: str | None = None,
        timeout: int = DEFAULT_AGENT_TIMEOUT,
        silent: bool = False,
    ) -> str:
        """Generate response using LangGraph agent.

        This is a **sync-only** entry point.  It must be called from a
        context where no asyncio event loop is already running (e.g. a
        plain ``if __name__ == '__main__'`` script).  If you already have
        an event loop, call :meth:`generate_async` directly instead.
        """
        return asyncio.run(self.generate_async(prompt, timeout))

    async def generate_async(self, prompt: str, timeout: int, output: io.StringIO | None = None) -> str:
        """Async implementation of generate.

        Public so that callers such as :class:`FanOut` can await it
        directly without reaching into private internals.

        Args:
            output: Optional buffer to write printed output to instead of stdout.
                    Used by FanOut to capture per-worker output for clean routing.
        """
        out = output if output is not None else sys.stdout
        messages = [HumanMessage(content=prompt)]
        config: RunnableConfig = {"recursion_limit": 50}

        accumulated_text: list[str] = []

        async def run_stream() -> None:
            thinking_started = False
            async for event in self.graph.astream_events({"messages": messages}, version="v1", config=config):
                kind = event["event"]

                if kind == "on_chat_model_stream":
                    chunk = event["data"].get("chunk")
                    if not chunk:
                        continue
                    text_chunk = parse_chunk_content(chunk.content)

                    if text_chunk:
                        if not thinking_started:
                            print(
                                f"\n{Colors.LIGHT_GRAY}[Thinking]{Colors.ENDC}",
                                flush=True,
                                file=out,
                            )
                            thinking_started = True
                        print(
                            f"{Colors.LIGHT_GRAY}{text_chunk}{Colors.ENDC}",
                            end="",
                            flush=True,
                            file=out,
                        )
                        accumulated_text.append(text_chunk)

                elif kind == "on_tool_start":
                    name = event["name"]
                    inputs = event["data"].get("input")
                    if thinking_started:
                        print(flush=True, file=out)
                        thinking_started = False
                    print(
                        f"\n{Colors.BLUE}[Tool Use] {name}({inputs}){Colors.ENDC}",
                        flush=True,
                        file=out,
                    )

                elif kind == "on_tool_end":
                    name = event["name"]
                    tool_output = event["data"].get("output")
                    status, result_text = extract_tool_result(tool_output)

                    symbol = ""
                    if status == "success":
                        symbol = f"{Colors.GREEN}\u2713{Colors.ENDC} "
                    elif status == "error":
                        symbol = f"{Colors.RED}\u2717{Colors.ENDC} "

                    print(
                        "\n"
                        f"{Colors.BLUE}[Tool Result] {name}: {symbol}{Colors.ENDC}\n"
                        f"{Colors.LIGHT_GRAY}{result_text}{Colors.ENDC}",
                        flush=True,
                        file=out,
                    )

            if thinking_started:
                print(flush=True, file=out)

        try:
            await asyncio.wait_for(run_stream(), timeout=timeout)

            final_content = "".join(accumulated_text)
            if not final_content:
                return ""
            return final_content

        except asyncio.TimeoutError:
            return f"Error: Agent execution timed out after {timeout} seconds."
        except Exception as e:
            logger.error(f"Error in agent generation: {e}")
            return f"Error: {e!s}"

    def run(self, input_data: Any) -> Any:
        """Implement Runnable protocol."""
        if self.instruction and "{input}" in self.instruction:
            prompt = self.instruction.replace("{input}", str(input_data))
        else:
            prompt = str(input_data)
        return self.generate(prompt)


def create_agent(
    provider: str | None = None,
    model: str | None = None,
    config_path: str | None = None,
    repo_path: str | None = None,
    instruction: str | None = None,
    tools: list[str] | None = None,
) -> LangGraphAgent:
    """Create a coding agent instance using LangGraph."""
    target_dir = repo_path or "."
    config = load_config(target_dir, config_path)

    if provider:
        config.agent.backend = provider
    if model:
        config.agent.model = model

    llm = build_llm(config)

    repo_path_obj = Path(target_dir).resolve()
    filesystem = RealFilesystem()
    all_tools = build_tools(repo_path_obj, filesystem)

    # Filter tools if requested
    if tools:
        selected_tools: list[Callable[..., Any]] = []
        available_tools_map = {t.__name__: t for t in all_tools}

        for tool_name in tools:
            if tool_name in available_tools_map:
                selected_tools.append(available_tools_map[tool_name])
            else:
                logger.warning(f"Tool '{tool_name}' not found. Available: {list(available_tools_map.keys())}")

        agent_tools = selected_tools
    else:
        agent_tools = all_tools

    return LangGraphAgent(
        model_name=config.agent.model or "default-model",
        llm=llm,
        tools=agent_tools,
        instruction=instruction or "",
    )


class Chain(Runnable):
    """Executes a sequence of steps, passing output from one to the next."""

    def __init__(self, steps: list[Runnable]):
        self.steps = steps

    def run(self, input_data: Any) -> Any:
        current_data = input_data
        for i, step in enumerate(self.steps):
            print(f"__LEGO_STEP_START__ {i}", flush=True)
            print(f"\n{Colors.BOLD}--- Step {i + 1}/{len(self.steps)} ({type(step).__name__}) ---{Colors.ENDC}")
            current_data = step.run(current_data)
            print(f"__LEGO_STEP_END__ {i}", flush=True)
        return current_data


class FanOut(Runnable):
    """Executes multiple prompts/tasks in parallel using the same agent/runnable."""

    def __init__(
        self,
        agent: Runnable,
        items: list[str],
        max_workers: int = DEFAULT_FAN_OUT_MAX_WORKERS,
        timeout: int = DEFAULT_AGENT_TIMEOUT,
    ):
        self.agent = agent
        self.items = items
        self.max_workers = max_workers
        self.timeout = timeout

    def run(self, input_data: Any) -> list[Any]:
        # Resolve items: if empty or a non-list expression string (LLM-generated
        # template that we can't evaluate), fall back to splitting input_data by
        # newlines so the fan_out scatters over the previous step's output.
        items: list[str] = self.items
        if not items:
            if input_data:
                raw_lines = [line.strip() for line in str(input_data).strip().splitlines() if line.strip()]
                # Strip lines that are clearly prose/formatting rather than items:
                # numbered list prefixes ("1. foo"), bullet markers ("- foo", "* foo"),
                # and header lines ending with a colon ("Here are the files:").
                _prefix = re.compile(r"^(\d+[\.\)]\s+|[-*•]\s+)")
                items = [_prefix.sub("", line) for line in raw_lines if not line.endswith(":")]
            else:
                items = []

        instruction = getattr(self.agent, "instruction", None)

        prompts_to_run: list[str] = []
        for item in items:
            if instruction and "{input}" in instruction:
                # Agent has an {input} placeholder in its instruction — pass
                # the item as the full prompt so the agent fills in context.
                prompts_to_run.append(instruction.replace("{input}", str(item)))
            elif "{input}" in item:
                prompts_to_run.append(item.format(input=str(input_data)))
            else:
                prompts_to_run.append(str(item))

        # Emit count so the UI can expand the FAN_OUT group with N real nodes.
        print(f"__LEGO_FANOUT_INIT__ {len(prompts_to_run)}", flush=True)

        async def _run_parallel() -> list[Any]:
            semaphore = asyncio.Semaphore(self.max_workers)

            async def _run_one(i: int, p: str) -> tuple[int, Any, str]:
                async with semaphore:
                    buffer = io.StringIO()
                    if isinstance(self.agent, AsyncRunnable):
                        result = await self.agent.generate_async(p, timeout=self.timeout, output=buffer)
                    else:
                        result = await asyncio.to_thread(self.agent.run, p)
                    return i, result, buffer.getvalue()

            tasks = [_run_one(i, p) for i, p in enumerate(prompts_to_run)]
            indexed_results = await asyncio.gather(*tasks)

            # Print each worker's captured output sequentially with markers so
            # the UI can route logs to individual worker nodes.
            results: list[Any] = []
            for i, result, captured in sorted(indexed_results, key=lambda x: x[0]):
                print(f"__LEGO_WORKER_START__ {i}", flush=True)
                if captured:
                    print(captured, end="", flush=True)
                print(f"__LEGO_WORKER_END__ {i}", flush=True)
                results.append(result)
            return results

        return asyncio.run(_run_parallel())


class Summarize(Runnable):
    """Aggregates multiple inputs into a single summary."""

    def __init__(self, agent: Runnable, instruction: str):
        self.agent = agent
        self.instruction = instruction

    def run(self, input_data: Any) -> str:
        if isinstance(input_data, list):
            items_list: list[Any] = input_data  # pyright: ignore[reportUnknownVariableType]
            combined_input = "\n\n---\n\n".join([str(x) for x in items_list])
        else:
            combined_input = str(input_data)

        prompt = f"{self.instruction}\n\nHere are the inputs to summarize:\n{combined_input}"
        return self.agent.run(prompt)


class JudgeLoop(Runnable):
    """Iterative loop where a judge evaluates worker output."""

    def __init__(self, judge: Runnable, worker: Runnable, task: str, max_iterations: int):
        self.judge = judge
        self.worker = worker
        self.task = task
        self.max_iterations = max_iterations

    def run(self, input_data: Any) -> dict[str, str]:
        current_output = None

        for i in range(self.max_iterations):
            print(f"\n{Colors.BOLD}=== Iteration {i + 1}/{self.max_iterations} ==={Colors.ENDC}")
            current_output_line = (
                "Current Output: (None - Worker has not started yet)"
                if current_output is None
                else f"Current Output:\n{current_output}"
            )

            judge_prompt = (
                f"Task: {self.task}\n\n"
                f"{current_output_line}\n\n"
                "=== YOUR ROLE: JUDGE/EVALUATOR ===\n"
                "You are an evaluator who assesses whether the task is complete. "
                "You make decisions but DO NOT perform work.\n\n"
                "DO:\n"
                "- Evaluate if the task requirements are met\n"
                "- Provide specific, actionable feedback if work is needed\n"
                "- Mark as 'done' only when task is FULLY satisfied\n"
                '- Respond with strictly JSON: {"status": "continue" or "done", "feedback": "..."}\n'
            )

            logger.info(f"Judge prompt sent to {type(self.judge).__name__}")
            judge_resp = str(self.judge.run(judge_prompt))

            # Parse JSON
            try:
                start = judge_resp.find("{")
                end = judge_resp.rfind("}")
                if start != -1 and end != -1:
                    json_str = judge_resp[start : end + 1]
                    feedback_data = json.loads(json_str)
                else:
                    feedback_data = {
                        "status": "continue",
                        "feedback": f"Judge did not return JSON. Raw: {judge_resp}",
                    }
            except Exception as e:
                feedback_data = {
                    "status": "continue",
                    "feedback": f"Invalid JSON response: {e}",
                }

            logger.info(f"Judge feedback: {feedback_data}")

            if feedback_data.get("status") == "done":
                logger.info("Judge loop done")
                return {
                    "final_output": current_output if current_output is not None else "",
                    "judge_feedback": feedback_data.get("feedback", ""),
                    "iterations": str(i + 1),
                }

            # Worker Step
            worker_prompt = (
                f"Task: {self.task}\n\n"
                f"Previous Output:\n{current_output}\n\n"
                f"Feedback:\n{feedback_data.get('feedback')}\n\n"
                "=== YOUR ROLE: WORKER/IMPLEMENTER ===\n"
                "You are responsible for executing the task/refinements based on feedback.\n"
                "Please perform the task now."
            )

            logger.info(f"Worker prompt sent to {type(self.worker).__name__}")
            current_output = self.worker.run(worker_prompt)
            logger.info("Worker finished step")

        return {
            "final_output": current_output if current_output is not None else "",
            "judge_feedback": "Max iterations reached",
            "iterations": str(self.max_iterations),
        }


# ---------------------------------------------------------------------------
# Registry-based runnable builder
# ---------------------------------------------------------------------------


def _create_agent_from_config(config: dict[str, Any]) -> Runnable:
    return create_agent(
        instruction=config.get("instruction"),
        tools=config.get("tools"),
        provider=config.get("provider"),
        model=config.get("model"),
    )


def _create_chain_from_config(config: dict[str, Any]) -> Runnable:
    steps_config = config.get("steps", [])
    if not steps_config:
        raise ValueError("Chain must have 'steps'")
    steps = [_build_runnable(step) for step in steps_config]
    return Chain(steps)


def _create_fan_out_from_config(config: dict[str, Any]) -> Runnable:
    agent_config = config.get("agent")
    if not agent_config:
        raise ValueError("FanOut must have 'agent'")
    items = config.get("items", [])
    return FanOut(
        agent=_build_runnable(agent_config),
        items=items,
        max_workers=config.get("max_workers", DEFAULT_FAN_OUT_MAX_WORKERS),
        timeout=config.get("timeout", DEFAULT_AGENT_TIMEOUT),
    )


def _create_summarize_from_config(config: dict[str, Any]) -> Runnable:
    agent_config = config.get("agent")
    if not agent_config:
        raise ValueError("Summarize must have 'agent'")
    instruction = config.get("instruction", "Summarize the inputs.")
    return Summarize(agent=_build_runnable(agent_config), instruction=instruction)


def _create_judge_loop_from_config(config: dict[str, Any]) -> Runnable:
    judge_config = config.get("judge")
    if not judge_config:
        raise ValueError("JudgeLoop must have 'judge'")
    worker_config = config.get("worker")
    if not worker_config:
        raise ValueError("JudgeLoop must have 'worker'")
    task = config.get("task", "")
    max_iters = config.get("max_iterations", DEFAULT_JUDGE_LOOP_MAX_ITERATIONS)
    return JudgeLoop(
        judge=_build_runnable(judge_config),
        worker=_build_runnable(worker_config),
        task=task,
        max_iterations=max_iters,
    )


RUNNABLE_TYPES: dict[str, Callable[[dict[str, Any]], Runnable]] = {
    "agent": _create_agent_from_config,
    "chain": _create_chain_from_config,
    "fan_out": _create_fan_out_from_config,
    "summarize": _create_summarize_from_config,
    "judge_loop": _create_judge_loop_from_config,
}


def _build_runnable(config: dict[str, Any]) -> Runnable:
    """Recursively build a Runnable from dictionary config."""
    kind: str | None = config.get("type")
    factory = RUNNABLE_TYPES.get(kind)  # type: ignore[reportArgumentType]
    if factory is None:
        raise ValueError(f"Unknown Runnable type: {kind}")
    return factory(config)


def run_yaml(config_path: str) -> None:
    """Entry point to execute a YAML configuration."""
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with open(path) as f:
        config = yaml.safe_load(f)

    workflow_config = config.get("workflow")
    if not workflow_config:
        raise ValueError("YAML must contain a 'workflow' root object.")

    runner = _build_runnable(workflow_config)
    print(f"{Colors.BOLD}Starting Workflow execution from {config_path}...{Colors.ENDC}")
    result = runner.run(None)

    print(f"\n{Colors.BOLD}{Colors.GREEN}Workflow Complete!{Colors.ENDC}")
    print(f"Result:\n{result}")


# Wrapper functions for script usage


def fan_out(agent: Runnable, items: list[str], max_workers: int = DEFAULT_FAN_OUT_MAX_WORKERS) -> list[Any]:
    """Execute multiple items in parallel using the agent."""
    return FanOut(agent, items, max_workers).run(None)


def summarize(agent: Runnable, items: list[str], instruction: str = "Summarize the inputs.") -> str:
    """Summarize a list of items using the agent."""
    return Summarize(agent, instruction).run(items)


def judge_loop(
    judge: Runnable, worker: Runnable, task: str, max_iterations: int = DEFAULT_JUDGE_LOOP_MAX_ITERATIONS
) -> dict[str, str]:
    """Execute a judge-worker loop."""
    return JudgeLoop(judge, worker, task, max_iterations).run(None)
