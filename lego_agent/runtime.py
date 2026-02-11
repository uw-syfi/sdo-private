import asyncio
import json
import ast
import yaml
from typing import (
    Optional,
    Dict,
    List,
    Any,
    Callable,
    Protocol,
    runtime_checkable,
)
from pathlib import Path

from langchain_core.runnables import RunnableConfig
from langchain_core.messages import HumanMessage
from langchain_core.tools import tool, StructuredTool
from langgraph.prebuilt import create_react_agent

from app_operator.langgraph.llm import build_llm
from app_operator.adk.tools import build_tools
from app_operator.config import load_config
from app_operator.filesystem import RealFilesystem
from app_operator.logger import logger
from lego_agent.io import Colors

# Global constant for compatibility with generated scripts
MAX_ITERATIONS = 10


@runtime_checkable
class Runnable(Protocol):
    """Protocol for any component that can execute a task."""

    def run(self, input_data: Any) -> Any: ...


class LangGraphAgent:
    """Agent wrapper around LangGraph prebuilt React agent."""

    # SDS-REVIEW: Architecture - Duplicate logic.
    # Streaming and tool handling logic overlaps significantly with `lego_agent.engine.LegoAgentEngine`.
    # Suggest extracting a common `BaseAgent` or `StreamProcessor`.

    def __init__(
        self,
        model_name: str,
        llm: Any,
        tools: List[Callable],
        instruction: str = "",
        agent_name: str = "LegoAgentWorker",
    ):
        self.model_name = model_name
        self.llm = llm
        self.tools = self._wrap_tools(tools)
        self.instruction = instruction
        self.agent_name = agent_name

        # Create the graph
        self.graph = create_react_agent(
            model=self.llm, tools=self.tools, prompt=self.instruction
        )

    def _wrap_tools(self, tools: List[Callable]) -> List[StructuredTool]:
        """Wrap ADK tools into LangChain StructuredTools."""
        wrapped_tools = []
        for t in tools:
            if isinstance(t, StructuredTool):
                wrapped_tools.append(t)
            elif callable(t):
                # Assume it's a function with docstrings
                # ADK tools return a dict, we want to return the string output mostly,
                # but returning the whole dict is also fine for the LLM to see status.
                wrapped_tools.append(tool(t))
            else:
                logger.warning(f"Unknown tool type: {type(t)}")
        return wrapped_tools

    def generate(
        self,
        prompt: str,
        cwd: Optional[str] = None,
        timeout: int = 300,
        silent: bool = False,
    ) -> str:
        """Generate response using LangGraph agent."""
        # Run in a separate thread if called from sync context to avoid blocking
        # But since we are likely inside an async loop (or not), safest is to run_async
        # However, generate() is sync API.
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            # If we are in an event loop, we can't use asyncio.run
            # We assume this method is called from a thread or we use a blocking call
            # But standard usage of generate() in generated scripts is sync.
            # If the generated script is sync, asyncio.run works.
            # If the generated script is running in an event loop, this will fail.
            # Generated scripts are "if __name__ == '__main__': main()", so usually sync main.
            return asyncio.run(self._generate_async(prompt, timeout))
        else:
            return asyncio.run(self._generate_async(prompt, timeout))

    async def _generate_async(self, prompt: str, timeout: int) -> str:
        messages = [HumanMessage(content=prompt)]
        config: RunnableConfig = {"recursion_limit": 50}

        accumulated_text = []

        async def run_stream():
            thinking_started = False
            async for event in self.graph.astream_events(
                {"messages": messages}, version="v1", config=config
            ):
                kind = event["event"]

                if kind == "on_chat_model_stream":
                    chunk = event["data"].get("chunk")
                    if not chunk:
                        continue
                    content = chunk.content
                    text_chunk = ""
                    if isinstance(content, str):
                        text_chunk = content
                    elif isinstance(content, list):
                        for part in content:
                            if isinstance(part, dict):
                                if part.get("type") == "text":
                                    text_chunk += part.get("text", "")
                                elif part.get("type") == "thinking":
                                    text_chunk += part.get("thinking", "")
                            elif isinstance(part, str):
                                text_chunk += part

                    if text_chunk:
                        if not thinking_started:
                            print(
                                f"\n{Colors.LIGHT_GRAY}[Thinking]{Colors.ENDC}",
                                flush=True,
                            )
                            thinking_started = True
                        print(
                            f"{Colors.LIGHT_GRAY}{text_chunk}{Colors.ENDC}",
                            end="",
                            flush=True,
                        )
                        accumulated_text.append(text_chunk)

                elif kind == "on_tool_start":
                    name = event["name"]
                    inputs = event["data"].get("input")
                    if thinking_started:
                        print("", flush=True)
                        thinking_started = False
                    print(
                        f"\n{Colors.BLUE}[Tool Use] {name}({inputs}){Colors.ENDC}",
                        flush=True,
                    )

                elif kind == "on_tool_end":
                    name = event["name"]
                    output = event["data"].get("output")

                    symbol = ""
                    result_text = ""

                    # Handle ToolMessage or simple output
                    content = getattr(output, "content", output)

                    try:
                        if isinstance(content, str):
                            # Try parsing as JSON first
                            try:
                                content_dict = json.loads(content)
                            except json.JSONDecodeError:
                                try:
                                    content_dict = ast.literal_eval(content)
                                except (ValueError, SyntaxError):
                                    content_dict = None

                            if isinstance(content_dict, dict):
                                status = content_dict.get("status")
                                if status == "success":
                                    symbol = f"{Colors.GREEN}✓{Colors.ENDC} "
                                elif status == "error":
                                    symbol = f"{Colors.RED}✗{Colors.ENDC} "

                                result_text = str(content_dict.get("output", ""))
                            else:
                                result_text = content
                        elif isinstance(content, dict):
                            status = content.get("status")
                            if status == "success":
                                symbol = f"{Colors.GREEN}✓{Colors.ENDC} "
                            elif status == "error":
                                symbol = f"{Colors.RED}✗{Colors.ENDC} "
                            result_text = str(content.get("output", ""))
                        else:
                            result_text = str(content)
                    except Exception:
                        result_text = str(content)

                    if len(result_text) > 500:
                        result_text = result_text[:500] + "\n... (truncated)"
                    print(
                        "\n"
                        f"{Colors.BLUE}[Tool Result] {name}: {symbol}{Colors.ENDC}\n"
                        f"{Colors.LIGHT_GRAY}{result_text}{Colors.ENDC}",
                        flush=True,
                    )

            if thinking_started:
                print("", flush=True)

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
            return f"Error: {str(e)}"

    def run(self, input_data: Any) -> Any:
        """Implement Runnable protocol."""
        # Input can be a string prompt or structured data
        prompt = str(input_data)
        return self.generate(prompt)


def create_agent(
    provider: Optional[str] = None,
    model: Optional[str] = None,
    config_path: Optional[str] = None,
    repo_path: Optional[str] = None,
    instruction: Optional[str] = None,
    tools: Optional[List[str]] = None,
) -> LangGraphAgent:
    """Create a coding agent instance using LangGraph."""
    # 1. Load configuration
    target_dir = repo_path or "."
    try:
        config = load_config(target_dir, config_path)
    except Exception as e:
        logger.warning(f"Failed to load config: {e}. Using defaults.")
        # Create a dummy config if load fails, or re-raise?
        # load_config usually raises.
        raise

    # 2. Setup Components
    if provider:
        config.agent.provider = provider
    if model:
        config.agent.model = model

    llm = build_llm(config)

    repo_path_obj = Path(target_dir).resolve()
    filesystem = RealFilesystem()
    all_tools = build_tools(repo_path_obj, filesystem)

    # Filter tools if requested
    if tools:
        selected_tools = []
        available_tools_map = {t.__name__: t for t in all_tools}

        for tool_name in tools:
            if tool_name in available_tools_map:
                selected_tools.append(available_tools_map[tool_name])
            else:
                logger.warning(
                    f"Tool '{tool_name}' not found. Available: {list(available_tools_map.keys())}"
                )

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

    def __init__(self, steps: List[Runnable]):
        self.steps = steps

    def run(self, input_data: Any) -> Any:
        current_data = input_data
        for i, step in enumerate(self.steps):
            print(
                f"\n{Colors.BOLD}--- Step {i + 1}/{len(self.steps)} ({type(step).__name__}) ---{Colors.ENDC}"
            )
            current_data = step.run(current_data)
        return current_data


class FanOut(Runnable):
    """Executes multiple prompts/tasks in parallel using the same agent/runnable."""

    def __init__(self, agent: Runnable, items: List[str], max_workers: int = 4):
        self.agent = agent
        self.items = items
        self.max_workers = max_workers

    def run(self, input_data: Any) -> List[Any]:
        # input_data is ignored if items are hardcoded, or could be used to format items?
        # For now, we assume items are fully formed prompts or instructions.
        # But for composition, maybe input_data *is* the list of items?
        # Or input_data is context injected into items?
        # Let's assume self.items are templates or direct prompts.
        # If input_data is provided, we can prepend it or use it.

        prompts_to_run = []
        if input_data:
            # If input provided, format items with it or append it?
            # Simple approach: If items are strings, format them with input_data if it's a string
            for item in self.items:
                if isinstance(item, str) and "{input}" in item:
                    prompts_to_run.append(item.format(input=str(input_data)))
                else:
                    # Append input as context
                    prompts_to_run.append(f"{item}\n\nContext:\n{input_data}")
        else:
            prompts_to_run = self.items

        # We need to run this async, but run() is sync.
        # Check for event loop.
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        async def _run_parallel():
            semaphore = asyncio.Semaphore(self.max_workers)

            async def _run_one(p):
                async with semaphore:
                    # We need to call agent.run(p). If agent.run is blocking/sync, we should wrap it.
                    # Since agent is likely LangGraphAgent, it has internal async handling but exposed via sync run().
                    # Ideally we should use agent.generate_async if available.
                    if isinstance(self.agent, LangGraphAgent):
                        return await self.agent._generate_async(p, timeout=300)
                    else:
                        # General runnable, run in thread
                        return await asyncio.to_thread(self.agent.run, p)

            tasks = [_run_one(p) for p in prompts_to_run]
            return await asyncio.gather(*tasks)

        if loop and loop.is_running():
            return asyncio.run(_run_parallel())  # This fails if loop is running.
            # If we are nested, we are already in async context usually?
            # Wait, `run` is called synchronously.
            # If we are inside a `Chain` which called `run`, we might be in sync code.
            # If `Chain` was called from `fan_out`, we are in async?
            # Let's stick to the pattern used in `fan_out` function previously.
            # But here we are inside a class method.

        # Simplified: Just use asyncio.run if no loop.
        if loop and loop.is_running():
            # We can't use asyncio.run. We are likely in a thread or nested.
            # This is a known issue with mixing sync/async.
            # For now, let's assume top level is sync.
            # If nested, this will crash.
            # WORKAROUND: Use nest_asyncio if needed, or just handle the top level.
            # Or, use a fresh loop in a new thread.
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor() as pool:
                return pool.submit(asyncio.run, _run_parallel()).result()
        else:
            return asyncio.run(_run_parallel())


class Summarize(Runnable):
    """Aggregates multiple inputs into a single summary."""

    def __init__(self, agent: Runnable, instruction: str):
        self.agent = agent
        self.instruction = instruction

    def run(self, input_data: Any) -> str:
        # input_data is expected to be a list of strings (from FanOut)
        if isinstance(input_data, list):
            combined_input = "\n\n---\n\n".join([str(x) for x in input_data])
        else:
            combined_input = str(input_data)

        prompt = (
            f"{self.instruction}\n\nHere are the inputs to summarize:\n{combined_input}"
        )
        return self.agent.run(prompt)


class JudgeLoop(Runnable):
    """Iterative loop where a judge evaluates worker output."""

    def __init__(
        self, judge: Runnable, worker: Runnable, task: str, max_iterations: int
    ):
        self.judge = judge
        self.worker = worker
        self.task = task
        self.max_iterations = max_iterations

    def run(self, input_data: Any) -> Dict[str, str]:
        # input_data can be optional initial context
        current_output = None

        for i in range(self.max_iterations):
            print(
                f"\n{Colors.BOLD}=== Iteration {i + 1}/{self.max_iterations} ==={Colors.ENDC}"
            )
            current_output_line = (
                "Current Output: (None - Worker has not started yet)"
                if current_output is None
                else f"Current Output:\n{current_output}"
            )

            judge_prompt = (
                f"Task: {self.task}\n\n"
                f"{current_output_line}\n\n"
                "=== YOUR ROLE: JUDGE/EVALUATOR ===\n"
                "You are an evaluator who assesses whether the task is complete. You make decisions but DO NOT perform work.\n\n"
                "DO:\n"
                "- Evaluate if the task requirements are met\n"
                "- Provide specific, actionable feedback if work is needed\n"
                "- Mark as 'done' only when task is FULLY satisfied\n"
                '- Respond with strictly JSON: {"status": "continue" or "done", "feedback": "..."}\n'
            )

            logger.info(f"Judge prompt sent to {type(self.judge).__name__}")
            # Judge can be a Chain or Agent. Its output should be the JSON string.
            judge_resp = str(self.judge.run(judge_prompt))

            # Parse JSON
            try:
                start = judge_resp.find("{")
                end = judge_resp.rfind("}")
                if start != -1 and end != -1:
                    json_str = judge_resp[start: end + 1]
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
                    "final_output": current_output
                    if current_output is not None
                    else "",
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


def _build_runnable(config: Dict[str, Any]) -> Runnable:
    """Recursively build a Runnable from dictionary config."""
    kind = config.get("type")

    if kind == "agent":
        return create_agent(
            instruction=config.get("instruction"),
            tools=config.get("tools"),
            provider=config.get("provider"),
            model=config.get("model"),
        )

    elif kind == "chain":
        steps_config = config.get("steps", [])
        if not steps_config:
            raise ValueError("Chain must have 'steps'")
        steps = [_build_runnable(step) for step in steps_config]
        return Chain(steps)

    elif kind == "fan_out":
        agent_config = config.get("agent")
        if not agent_config:
            raise ValueError("FanOut must have 'agent'")
        items = config.get("items", [])
        return FanOut(
            agent=_build_runnable(agent_config),
            items=items,
            max_workers=config.get("max_workers", 4),
        )

    elif kind == "summarize":
        agent_config = config.get("agent")
        if not agent_config:
            raise ValueError("Summarize must have 'agent'")
        instruction = config.get("instruction", "Summarize the inputs.")
        return Summarize(agent=_build_runnable(agent_config), instruction=instruction)

    elif kind == "judge_loop":
        judge_config = config.get("judge")
        if not judge_config:
            raise ValueError("JudgeLoop must have 'judge'")
        worker_config = config.get("worker")
        if not worker_config:
            raise ValueError("JudgeLoop must have 'worker'")
        task = config.get("task", "")
        max_iters = config.get("max_iterations", 10)
        return JudgeLoop(
            judge=_build_runnable(judge_config),
            worker=_build_runnable(worker_config),
            task=task,
            max_iterations=max_iters,
        )

    else:
        raise ValueError(f"Unknown Runnable type: {kind}")


def run_yaml(config_path: str):
    """Entry point to execute a YAML configuration."""
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with open(path, "r") as f:
        config = yaml.safe_load(f)

    workflow_config = config.get("workflow")
    if not workflow_config:
        raise ValueError("YAML must contain a 'workflow' root object.")

    runner = _build_runnable(workflow_config)
    print(
        f"{Colors.BOLD}Starting Workflow execution from {config_path}...{Colors.ENDC}"
    )
    result = runner.run(None)

    print(f"\n{Colors.BOLD}{Colors.GREEN}Workflow Complete!{Colors.ENDC}")
    print(f"Result:\n{result}")
