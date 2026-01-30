import asyncio
import json
import ast
from typing import Optional, Dict, List, Any, Callable
from pathlib import Path

from langchain_core.messages import HumanMessage
from langchain_core.tools import tool, StructuredTool
from langgraph.prebuilt import create_react_agent

from app_operator.langgraph.llm import build_llm
from app_operator.adk.tools import build_tools
from app_operator.config import load_config
from app_operator.filesystem import RealFilesystem
from app_operator.logger import logger
from agentflow.io import Colors


class LangGraphAgent:
    """Agent wrapper around LangGraph prebuilt React agent."""

    def __init__(
        self,
        model_name: str,
        llm: Any,
        tools: List[Callable],
        instruction: str = "",
        agent_name: str = "AgentflowWorker",
    ):
        self.model_name = model_name
        self.llm = llm
        self.tools = self._wrap_tools(tools)
        self.instruction = instruction
        self.agent_name = agent_name

        # Create the graph
        self.graph = create_react_agent(
            model=self.llm,
            tools=self.tools,
            prompt=self.instruction
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
        config = {"recursion_limit": 50}

        accumulated_text = []

        async def run_stream():
            thinking_started = False
            async for event in self.graph.astream_events(
                {"messages": messages},
                version="v1",
                config=config
            ):
                kind = event["event"]

                if kind == "on_chat_model_stream":
                    content = event["data"]["chunk"].content
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
                            print(f"\n{Colors.LIGHT_GRAY}[Thinking]{Colors.ENDC}", flush=True)
                            thinking_started = True
                        print(f"{Colors.LIGHT_GRAY}{text_chunk}{Colors.ENDC}", end="", flush=True)
                        accumulated_text.append(text_chunk)

                elif kind == "on_tool_start":
                    name = event["name"]
                    inputs = event["data"].get("input")
                    if thinking_started:
                        print("", flush=True)
                        thinking_started = False
                    print(f"\n{Colors.BLUE}[Tool Use] {name}({inputs}){Colors.ENDC}", flush=True)

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
                        f"\n{
                            Colors.BLUE}[Tool Result] {name}: {symbol}{
                            Colors.ENDC}\n{
                            Colors.LIGHT_GRAY}{result_text}{
                            Colors.ENDC}",
                        flush=True)

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


def create_agent(
    provider: Optional[str] = None,
    model: Optional[str] = None,
    config_path: Optional[str] = None,
    repo_path: Optional[str] = None,
    instruction: Optional[str] = None,
    tools: Optional[List[str]] = None,
) -> LangGraphAgent:
    """Create a coding agent instance using LangGraph.

    Args:
        provider: Agent provider ("gemini", "claude", "codex", "opencode").
        model: Model name override.
        config_path: Path to sds.toml config file.
        repo_path: Repository path for config loading.
        instruction: System instruction for the agent.
        tools: List of tool names to enable (e.g., ["read_file", "run_command"]).
               If None, all default tools are enabled.

    Returns:
        LangGraphAgent: Configured coding agent.
    """
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
                    f"Tool '{tool_name}' not found. Available: {
                        list(
                            available_tools_map.keys())}")

        agent_tools = selected_tools
    else:
        agent_tools = all_tools

    return LangGraphAgent(
        model_name=config.agent.model,
        llm=llm,
        tools=agent_tools,
        instruction=instruction or ""
    )


def fan_out(
    agent: LangGraphAgent, prompts: List[str], max_workers: int = 4, timeout: int = 300
) -> List[str]:
    """Execute multiple prompts in parallel using the same agent type."""

    async def _generate_with_semaphore(semaphore, prompt):
        async with semaphore:
            return await agent._generate_async(prompt, timeout)

    async def _run_all():
        semaphore = asyncio.Semaphore(max_workers)
        tasks = [_generate_with_semaphore(semaphore, p) for p in prompts]
        return await asyncio.gather(*tasks)

    try:
        # Check if we are already in an event loop
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        # This is tricky if fan_out is called from a thread that doesn't have its own loop
        # but the main thread does.
        # But usually generated scripts are sync.
        return asyncio.run(_run_all())
    else:
        return asyncio.run(_run_all())


def summarize(
    agent: LangGraphAgent, responses: List[str], instruction: str, timeout: int = 300
) -> str:
    """Summarize a list of responses."""
    combined_input = "\n\n---\n\n".join(responses)
    prompt = f"{instruction}\n\nHere are the inputs to summarize:\n{combined_input}"
    return agent.generate(prompt=prompt, timeout=timeout)


def judge_loop(
    judge: LangGraphAgent,
    worker: LangGraphAgent,
    task: str,
    max_iterations: int,
    timeout: int = 3000,
) -> Dict[str, str]:
    """Iterative loop where a judge evaluates worker output."""
    current_output = None

    for i in range(max_iterations):
        current_output_line = (
            "Current Output: (None - Worker has not started yet)"
            if current_output is None
            else f"Current Output:\n{current_output}"
        )
        judge_prompt = (
            f"Task: {task}\n\n"
            f"{current_output_line}\n\n"
            "=== YOUR ROLE: JUDGE/EVALUATOR ===\n"
            "You are an evaluator who assesses whether the task is complete. You make decisions but DO NOT perform work.\n\n"
            "DO:\n"
            "- Explore the codebase (READ ONLY) to verify current state\n"
            "- Evaluate if the task requirements are met\n"
            "- Provide specific, actionable feedback if work is needed\n"
            "- Mark as 'done' only when task is FULLY satisfied\n"
            "- Consider edge cases and completeness\n\n"
            "DO NOT:\n"
            "- Write, edit, or create any files\n"
            "- Execute commands or make changes\n"
            "- Perform the task yourself\n"
            "- Provide implementation details (that's the worker's job)\n"
            "- Mark as 'done' prematurely without verification\n\n"
            'Respond with strictly JSON: {"status": "continue" or "done", "feedback": "..."}\n'
            "If 'continue', provide clear feedback on what still needs to be done.\n"
            "If 'done', confirm what was accomplished."
        )

        logger.info(f"Judge prompt: {judge_prompt}")
        judge_resp = judge.generate(prompt=judge_prompt, timeout=timeout)
        try:
            # Basic JSON extraction
            start = judge_resp.find("{")
            end = judge_resp.rfind("}")
            if start != -1 and end != -1:
                json_str = judge_resp[start: end + 1]
                feedback_data = json.loads(json_str)
            else:
                # Fallback if no JSON found
                logger.warning(f"Judge did not return JSON. Response: {judge_resp}")
                feedback_data = {
                    "status": "continue",
                    "feedback": "Please format response as JSON.",
                }
        except json.JSONDecodeError:
            feedback_data = {"status": "continue", "feedback": "Invalid JSON response."}

        logger.info(f"Judge feedback: {feedback_data}")

        if feedback_data.get("status") == "done":
            logger.info("Judge loop done")
            return {
                "final_output": current_output if current_output is not None else "",
                "judge_feedback": feedback_data.get("feedback", ""),
                "iterations": str(i + 1),
            }

        # Worker Step
        if current_output is None:
            # First execution
            worker_prompt = (
                f"Task: {task}\n\n"
                f"Judge Feedback/Instructions: {feedback_data.get('feedback')}\n\n"
                "=== YOUR ROLE: WORKER/IMPLEMENTER ===\n"
                "You are responsible for executing the task. You take action and produce results.\n\n"
                "DO:\n"
                "- Perform the requested task completely\n"
                "- Write, edit, or create files as needed\n"
                "- Execute necessary commands and operations\n"
                "- Follow the judge's feedback precisely\n"
                "- Test your work to ensure correctness\n"
                "- Document what you've done clearly\n\n"
                "DO NOT:\n"
                "- Evaluate or judge if the task is complete (that's the judge's role)\n"
                "- Skip steps or cut corners\n"
                "- Ask rhetorical questions about what should be done\n"
                "- Provide only plans or suggestions without implementation\n"
                "- Wait for approval before taking action\n\n"
                "Please perform the task now."
            )
        else:
            # Refine
            worker_prompt = (
                f"Task: {task}\n\n"
                f"Previous Output:\n{current_output}\n\n"
                f"Feedback:\n{feedback_data.get('feedback')}\n\n"
                "=== YOUR ROLE: WORKER/IMPLEMENTER ===\n"
                "You are responsible for improving the previous work based on feedback.\n\n"
                "DO:\n"
                "- Address ALL points in the judge's feedback\n"
                "- Make concrete changes to fix identified issues\n"
                "- Build upon previous work (don't start from scratch)\n"
                "- Verify your improvements work correctly\n"
                "- Be thorough and complete the refinements\n\n"
                "DO NOT:\n"
                "- Ignore or partially address feedback\n"
                "- Debate whether the feedback is correct (implement first)\n"
                "- Provide explanations without making actual changes\n"
                "- Ask the judge to clarify (take your best interpretation)\n"
                "- Leave TODOs or incomplete work\n\n"
                "Please improve the output based on the feedback now."
            )

        logger.info(f"Worker prompt: {worker_prompt}")
        current_output = worker.generate(prompt=worker_prompt, timeout=timeout)

        logger.info(f"Worker output: {current_output}")

    return {
        "final_output": current_output if current_output is not None else "",
        "judge_feedback": "Max iterations reached",
        "iterations": str(max_iterations),
    }
