import json
import asyncio
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, Dict, List, Any, Callable
from pathlib import Path

from google.adk.agents import LlmAgent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.tools.function_tool import FunctionTool
from google.genai import types

from app_operator.adk.models import build_adk_model
from app_operator.adk.tools import build_tools
from app_operator.config import load_config
from app_operator.filesystem import RealFilesystem
from app_operator.logger import logger


class GoogleAdkAgent:
    """Agent implementation using Google ADK LlmAgent directly."""

    def __init__(
        self,
        model_name: str,
        tools: List[Callable],
        instruction: str = "",
        agent_name: str = "AgentflowWorker",
    ):
        self.model_name = model_name
        self.tools = tools
        self.instruction = instruction
        self.agent_name = agent_name
        
        # Prepare tools for ADK (wrap in FunctionTool)
        self.adk_tools = []
        for tool in self.tools:
            if isinstance(tool, FunctionTool):
                self.adk_tools.append(tool)
            else:
                self.adk_tools.append(FunctionTool(tool))

        # Instantiate LlmAgent
        self.llm_agent = LlmAgent(
            name=self.agent_name,
            description=f"SDS Agent: {self.agent_name}",
            model=self.model_name,
            tools=self.adk_tools,
            instruction=self.instruction,
        )
        
        self.session_service = InMemorySessionService()

    def generate(
        self,
        prompt: str,
        cwd: Optional[str] = None,
        timeout: int = 300,
        silent: bool = False,
    ) -> str:
        """Generate response using ADK agent."""
        return asyncio.run(self._generate_async(prompt, timeout))

    async def _generate_async(self, prompt: str, timeout: int) -> str:
        # Create a new session for each generation
        session_id = str(uuid.uuid4())
        await self.session_service.create_session(
            app_name="agentflow", user_id="sds", session_id=session_id
        )

        runner = Runner(
            agent=self.llm_agent,
            app_name="agentflow",
            session_service=self.session_service,
        )
        
        content = types.Content(role="user", parts=[types.Part(text=prompt)])
        response_text = ""

        if hasattr(runner, "run_async"):
             async for event in runner.run_async(
                user_id="sds", session_id=session_id, new_message=content
            ):
                text = self._extract_text(event)
                if text:
                    response_text = text
                
                if hasattr(event, "is_final_response") and event.is_final_response():
                    if text:
                        response_text = text
                    break
        else:
             raise AttributeError("Runner does not provide run_async()")
             
        return response_text

    def _extract_text(self, event: Any) -> str:
        """Extract plain text content from an ADK event."""
        content = getattr(event, "content", None)
        if content is None:
            return ""
        parts = getattr(content, "parts", None) or []
        texts = []
        for part in parts:
            text = getattr(part, "text", None)
            if text:
                texts.append(text)
        return "".join(texts)


def create_agent(
    provider: Optional[str] = None,
    model: Optional[str] = None,
    config_path: Optional[str] = None,
    repo_path: Optional[str] = None,
    instruction: Optional[str] = None,
) -> GoogleAdkAgent:
    """Create a coding agent instance using Google ADK.

    Args:
        provider: Agent provider ("gemini", "claude", "codex", "opencode").
        model: Model name override.
        config_path: Path to sds.toml config file.
        repo_path: Repository path for config loading.
        instruction: System instruction for the agent.

    Returns:
        GoogleAdkAgent: Configured coding agent.
    """

    # 1. Load configuration
    target_dir = repo_path or "."
    try:
        config = load_config(target_dir, config_path)
    except Exception as e:
        logger.warning(f"Failed to load config: {e}. Using defaults.")
        raise

    # 2. Setup ADK components
    # Handle overrides
    if provider:
        config.agent.provider = provider
    if model:
        config.agent.model = model

    adk_model_name = build_adk_model(config)

    repo_path_obj = Path(target_dir).resolve()
    filesystem = RealFilesystem()
    tools = build_tools(repo_path_obj, filesystem)

    return GoogleAdkAgent(
        model_name=adk_model_name,
        tools=tools,
        instruction=instruction or ""
    )


def fan_out(
    agent: GoogleAdkAgent, prompts: List[str], max_workers: int = 4, timeout: int = 300
) -> List[str]:
    """Execute multiple prompts in parallel using the same agent type."""
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [
            executor.submit(agent.generate, prompt=p, timeout=timeout) for p in prompts
        ]
        return [f.result() for f in futures]

def summarize(
    agent: GoogleAdkAgent, responses: List[str], instruction: str, timeout: int = 300
) -> str:
    """Summarize a list of responses."""
    combined_input = "\n\n---\n\n".join(responses)
    prompt = f"{instruction}\n\nHere are the inputs to summarize:\n{combined_input}"
    return agent.generate(prompt=prompt, timeout=timeout)

def judge_loop(
    judge: GoogleAdkAgent,
    worker: GoogleAdkAgent,
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
                json_str = judge_resp[start : end + 1]
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