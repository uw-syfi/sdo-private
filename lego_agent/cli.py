import argparse
import asyncio
import sys
import os
import subprocess
from pathlib import Path

from lego_agent.engine import LegoAgentEngine
from lego_agent.io import ConsoleIO
from lego_agent.utils import find_repo_root
from loguru import logger
from lego_agent.config import load_config
from lego_agent.prompts import get_loader

DEFAULT_MAX_CLARIFICATIONS = 5  # maximum clarification rounds before proceeding
DEFAULT_LOOP_BOUND = 10  # default execution loop bound for generated scripts


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="LegoAgent: Autonomous script generator"
    )
    parser.add_argument("--prompt", help="Initial user prompt")
    parser.add_argument("--config", help="Path to sds.toml config file")
    parser.add_argument("--model", help="Override agent model")
    parser.add_argument(
        "--max-clarifications", type=int, default=DEFAULT_MAX_CLARIFICATIONS,
        help="Max clarification rounds"
    )
    parser.add_argument(
        "--loop-bound", type=int, help="Execution loop bound for generated script"
    )
    parser.add_argument(
        "--output-dir", default="lego_agent_runs", help="Output directory"
    )
    parser.add_argument(
        "--work-dir", required=True, help="Directory to run the generated script in"
    )
    parser.add_argument(
        "--no-run", action="store_true", help="Generate script but do not execute it"
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    repo_root = find_repo_root()

    try:
        config = load_config(str(repo_root), args.config)
        if args.model:
            config.agent.model = args.model
    except Exception as e:
        logger.error(f"Failed to load config: {e}")
        return 1

    output_dir = repo_root / args.output_dir
    work_dir = Path(args.work_dir).resolve()
    prompt_loader = get_loader()

    # Get loop bound
    loop_bound = args.loop_bound if args.loop_bound is not None else DEFAULT_LOOP_BOUND

    io = ConsoleIO()

    # Get prompt
    user_prompt = args.prompt
    if not user_prompt:
        user_prompt = io.read_prompt()
        if not user_prompt:
            logger.error("No prompt provided.")
            return 1

    engine = LegoAgentEngine(
        config=config,
        prompt_loader=prompt_loader,
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

        if args.no_run:
            return 0

        # Execute the generated script
        if not work_dir.exists():
            work_dir.mkdir(parents=True, exist_ok=True)

        io.info(f"Executing script in {work_dir}...")

        env = os.environ.copy()
        env["PYTHONPATH"] = f"{repo_root}:{env.get('PYTHONPATH', '')}"

        # SDS-REVIEW: Security - Arbitrary code execution.
        # Ensure the user is aware they are executing generated code.
        # Consider adding a prompt confirmation here (if not --no-tui/interactive).
        subprocess.run(
            [sys.executable, str(result.script_path)],
            cwd=work_dir,
            env=env,
            check=True,
        )

        return 0
    except Exception as e:
        logger.error(f"LegoAgent failed: {e}")
        return 1
