"""Entry point for the pydantic_agent SREGym client."""

from __future__ import annotations

import argparse
import logging
import os
import time

import requests

from sregym_agents.pydantic_agent.agent import SREGymAgent
from sregym_agents.pydantic_agent.tools import SREGymDeps

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

_READY_STAGES = {"diagnosis", "mitigation"}


def _get_api_base() -> str:
    host = os.getenv("API_HOSTNAME", "localhost")
    port = os.getenv("API_PORT", "8000")
    return f"http://{host}:{port}"


def _wait_for_stage(api_base: str, timeout: int = 300) -> str:
    """Poll until conductor reaches a submission-ready stage."""
    start = time.time()
    while time.time() - start < timeout:
        try:
            resp = requests.get(f"{api_base}/status", timeout=5)
            resp.raise_for_status()
            stage = resp.json().get("stage")
            if stage in _READY_STAGES:
                logger.info(f"Conductor ready at stage: {stage!r}")
                return stage
            logger.debug(f"Stage: {stage!r}, waiting...")
        except Exception as e:
            logger.debug(f"Status check failed: {e}")
        time.sleep(1)
    raise TimeoutError(f"Conductor did not reach ready stage within {timeout}s")


def _build_instruction(api_base: str, app_info: dict, problem_id: str, stages: list[str]) -> str:
    app_name = app_info.get("app_name", "unknown")
    namespace = app_info.get("namespace", "default")
    descriptions = app_info.get("descriptions", "")
    has_mitigation = "mitigation" in stages
    task_count = "TWO" if has_mitigation else "ONE"
    task_verb = "diagnosing and fixing" if has_mitigation else "diagnosing"

    instruction = f"""You are an SRE agent tasked with {task_verb} issues in a Kubernetes application.

Application: {app_name}
Namespace: {namespace}

{descriptions}

WORKFLOW: You will perform {task_count} task{"s" if has_mitigation else ""} in sequence:

TASK 1: DIAGNOSIS
- Investigate the application to detect any anomalies or issues
- Analyze metrics, logs, and Kubernetes resources
- When ready, call submit_solution with a natural language description of the issue
"""

    if has_mitigation:
        instruction += """
TASK 2: MITIGATION
- Identify the root cause and implement a fix via kubectl
- When the fix is applied, call submit_solution(ans="") to trigger validation
"""

    instruction += f"""
Important:
- Use kubectl commands to inspect and modify resources in namespace '{namespace}'
- The conductor API is available at {api_base}
- Problem ID: {problem_id}
"""
    return instruction


def main() -> None:
    parser = argparse.ArgumentParser(description="Run pydantic_agent on SREGym tasks")
    parser.add_argument(
        "--model",
        type=str,
        default=os.getenv("MODEL_ID", "claude-sonnet-4-6"),
        help="Model to use (default: MODEL_ID env var or claude-sonnet-4-6)",
    )
    args = parser.parse_args()

    api_base = _get_api_base()
    mcp_port = os.getenv("MCP_SERVER_PORT", "9954")
    mcp_url = f"http://localhost:{mcp_port}/submit/sse"

    logger.info(f"pydantic_agent starting | model={args.model} api={api_base} mcp={mcp_url}")

    _wait_for_stage(api_base, timeout=300)

    app_info = requests.get(f"{api_base}/get_app").json()
    problem_id = requests.get(f"{api_base}/get_problem").json()["problem_id"]
    stages = requests.get(f"{api_base}/stages").json().get("stages", [])

    instruction = _build_instruction(api_base, app_info, problem_id, stages)
    logger.info(f"Problem: {problem_id} | Stages: {stages}")

    deps = SREGymDeps(
        namespace=app_info.get("namespace", "default"),
        submit_mcp_url=mcp_url,
    )
    agent = SREGymAgent(model=args.model, deps=deps)
    agent.run(instruction)
    logger.info("pydantic_agent finished.")


if __name__ == "__main__":
    main()
