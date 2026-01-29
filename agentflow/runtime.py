import json
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, Dict, List

from libs.agent_cli.base import CodingAgent, AGENT_REGISTRY
from libs.agent_cli.factory import create_agent_from_config
from app_operator.logger import logger


def create_agent(
    provider: Optional[str] = None,
    model: Optional[str] = None,
    config_path: Optional[str] = None,
    repo_path: Optional[str] = None,
) -> CodingAgent:
    """Create a coding agent instance."""
    if provider is None:
        # Load from config
        target_dir = repo_path or "."
        return create_agent_from_config(
            target_dir, model_override=model, config_path=config_path
        )

    # Direct instantiation
    provider_lower = provider.lower()
    if provider_lower in AGENT_REGISTRY:
        return AGENT_REGISTRY[provider_lower](model=model)

    raise ValueError(f"Unknown provider: {provider}")


def fan_out(
    agent: CodingAgent, prompts: List[str], max_workers: int = 4, timeout: int = 300
) -> List[str]:
    """Execute multiple prompts in parallel using the same agent type."""
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [
            executor.submit(agent.generate, prompt=p, timeout=timeout) for p in prompts
        ]
        return [f.result() for f in futures]


def summarize(
    agent: CodingAgent, responses: List[str], instruction: str, timeout: int = 300
) -> str:
    """Summarize a list of responses."""
    combined_input = "\n\n---\n\n".join(responses)
    prompt = f"{instruction}\n\nHere are the inputs to summarize:\n{combined_input}"
    return agent.generate(prompt=prompt, timeout=timeout)


def judge_loop(
    judge: CodingAgent,
    worker: CodingAgent,
    task: str,
    max_iterations: int,
    timeout: int = 300,
) -> Dict[str, str]:
    """Iterative loop where a judge evaluates worker output."""
    current_output = worker.generate(prompt=f"Task: {task}", timeout=timeout)

    for i in range(max_iterations):
        judge_prompt = (
            f"Task: {task}\n\n"
            f"Current Output:\n{current_output}\n\n"
            "Evaluate if the output meets the requirements.\n"
            'Respond with strictly JSON: {"status": "continue" or "done", "feedback": "..."}'
        )

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

        if feedback_data.get("status") == "done":
            return {
                "final_output": current_output,
                "judge_feedback": feedback_data.get("feedback", ""),
                "iterations": str(i + 1),
            }

        # Refine
        refine_prompt = (
            f"Task: {task}\n\n"
            f"Previous Output:\n{current_output}\n\n"
            f"Feedback:\n{feedback_data.get('feedback')}\n\n"
            "Please improve the output based on the feedback."
        )
        current_output = worker.generate(prompt=refine_prompt, timeout=timeout)

    return {
        "final_output": current_output,
        "judge_feedback": "Max iterations reached",
        "iterations": str(max_iterations),
    }
