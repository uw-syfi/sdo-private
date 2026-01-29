import argparse
import asyncio
from pathlib import Path

from agentflow.engine import AgentflowEngine
from agentflow.io import ConsoleIO
from app_operator.adk.models import build_adk_model
from app_operator.adk.runner import AdkAgentRunner
from app_operator.trajectory import init_trajectory
from app_operator.config import load_config
from app_operator.logger import logger
from agentflow.prompts import get_loader


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Agentflow: Autonomous script generator"
    )
    parser.add_argument("--prompt", help="Initial user prompt")
    parser.add_argument("--config", help="Path to sds.toml config file")
    parser.add_argument("--model", help="Override agent model")
    parser.add_argument(
        "--max-clarifications", type=int, default=5, help="Max clarification rounds"
    )
    parser.add_argument(
        "--loop-bound", type=int, help="Execution loop bound for generated script"
    )
    parser.add_argument(
        "--output-dir", default="agentflow_runs", help="Output directory"
    )
    parser.add_argument(
        "--work-dir", default=".", help="Directory to run the generated script in"
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    # Determine repo root
    # This file is agentflow/cli.py
    # parents[0]=agentflow, parents[1]=app_operator, parents[2]=root
    repo_root = Path(__file__).resolve().parents[2]

    try:
        config = load_config(str(repo_root), args.config)
        if args.model:
            config.agent.model = args.model
    except Exception as e:
        logger.error(f"Failed to load config: {e}")
        return 1

    # Setup Trajectory Recorder
    recorder = init_trajectory(repo_root)
    recorder.set_agent_name("Agentflow")

    try:
        model = build_adk_model(config)
        runner = AdkAgentRunner(
            app_name="sds-agentflow",
            recorder=recorder,
            repo_path=repo_root,
        )
    except Exception as e:
        logger.error(f"Failed to initialize ADK: {e}")
        return 1

    io = ConsoleIO()

    # Get prompt
    user_prompt = args.prompt
    if not user_prompt:
        user_prompt = io.read_prompt()
        if not user_prompt:
            logger.error("No prompt provided.")
            return 1

    # Get loop bound
    loop_bound = args.loop_bound if args.loop_bound is not None else 10

    output_dir = repo_root / args.output_dir
    work_dir = Path(args.work_dir).resolve()

    engine = AgentflowEngine(
        runner=runner,
        model=model,
        prompt_loader=get_loader(),
        io=io,
        loop_bound=loop_bound,
        max_clarifications=args.max_clarifications,
        agent_timeout=config.operator.agent_timeout,
        output_dir=output_dir,
        work_dir=work_dir,
    )

    try:
        result = asyncio.run(engine.run_async(user_prompt))
        io.info(f"\nSuccess! Script written to: {result.script_path}")

        # Execute the generated script
        import subprocess
        import sys
        import os

        if not work_dir.exists():
            work_dir.mkdir(parents=True, exist_ok=True)

        io.info(f"Executing script in {work_dir}...")

        env = os.environ.copy()
        env["PYTHONPATH"] = f"{repo_root}:{env.get('PYTHONPATH', '')}"

        subprocess.run(
            [sys.executable, str(result.script_path)],
            cwd=work_dir,
            env=env,
            check=True,
        )

        return 0
    except Exception as e:
        logger.error(f"Agentflow failed: {e}")
        return 1
