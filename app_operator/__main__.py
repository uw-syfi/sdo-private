import argparse
import shutil
import subprocess
import sys
import time

from dotenv import load_dotenv
from pathlib import Path

from app_operator.commands import (
    run,
    init_exp,
    analyze_prompts,
    optimize_prompts,
    e2e_optimize,
    run_exp,
    plot_exp,
)
import app_operator.langgraph.viz_graph as viz_graph
from app_operator.logger import logger

# Load environment variables from .env file
load_dotenv()

REQUIRED_DEPENDENCIES = ["docker", "kubectl"]

INSTALL_HINTS = {
    "docker": "Install Docker: https://docs.docker.com/engine/install/",
    "kubectl": "Install kubectl: https://kubernetes.io/docs/tasks/tools/",
}


def trigger_ai_remediation(max_retries: int):
    """
    Runs the Gemini SRE agent in a loop until the system is healthy 
    or we run out of retries.
    """
    # 1. Setup Paths
    current_file = Path(__file__).resolve()
    sds_root = current_file.parent.parent
    playbook_path = sds_root / "agents" / "sre_startup_playbook.md"

    if not playbook_path.exists():
        logger.warning(f"⚠️  Playbook not found at {playbook_path}")
        return False

    playbook_content = playbook_path.read_text()
    
    print("\n" + "="*50)
    print(f"🤖 [SDS Operator] STARTING AUTO-HEALING LOOP (Max Retries: {max_retries})")
    print("="*50)

    for attempt in range(1, max_retries + 1):
        print(f"\n🔄 [Attempt {attempt}/{max_retries}] Summoning SRE Agent...")

        try:
            # 2. Run Gemini and CAPTURE the output
            #    We use 'tee' behavior: print to screen AND capture to variable
            process = subprocess.Popen(
                ["gemini", "-y", playbook_content],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                universal_newlines=True
            )

            # Stream output to console in real-time while capturing it
            full_output = ""
            for line in process.stdout:
                print(line, end="") # Print to user
                full_output += line # Save for analysis
            
            
            process.wait() # Wait for agent to finish

            # 3. Analyze the Result
            if "SYSTEM HEALTHY" in full_output:
                print(f"\n✅ [SDS Operator] Success! System healed on attempt {attempt}.")
                return True # Exit the loop
            
            else:
                print("\n⚠️ [SDS Operator] Agent finished, but system is NOT healthy yet.")
                print("   Retrying in 5 seconds...")
                time.sleep(5)

        except Exception as e:
            logger.error(f"❌ Execution error: {e}")
            time.sleep(5)

    print(f"\n❌ [SDS Operator] Failed to heal system after {max_retries} attempts.")
    return False


def check_dependencies():
    """Check if required system dependencies are installed."""
    missing = []
    for tool in REQUIRED_DEPENDENCIES:
        if not shutil.which(tool):
            missing.append(tool)

    if missing:
        for tool in missing:
            hint = INSTALL_HINTS.get(tool, f"Please install '{tool}' to continue.")
            logger.error(f"Missing required system dependencies: {tool}: {hint}")
        sys.exit(1)

    # Check if docker daemon is running
    try:
        subprocess.check_call(
            ["docker", "container", "ls"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except subprocess.CalledProcessError:
        logger.error("Docker daemon is not running or not accessible.")
        logger.info("Start Docker and try again: https://docs.docker.com/engine/install/")
        sys.exit(1)


def main() -> int:
    """Main entry point for the operator CLI.

    Returns:
        int: Exit code (0 for success, non-zero for failure).
    """
    parser = argparse.ArgumentParser(
        prog="operator",
        description="Codex-assisted deployment mode with automatic error fixing.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run Codex-assisted deployment on a repository
  ./sds_operator /path/to/repository

  # Use a custom health check interval
  ./sds_operator /path/to/repository --interval 60
        """,
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    subparsers.required = True

    # 'run' command
    run_parser = subparsers.add_parser(
        "run", help="Run Codex-assisted deployment on a repository"
    )
    run.add_arguments(run_parser)

    # 'init-exp' command
    init_exp_parser = subparsers.add_parser(
        "init-exp", help="Initialize a new experiment from an existing application"
    )
    init_exp.add_arguments(init_exp_parser)

    # 'viz-graph' command
    viz_graph_parser = subparsers.add_parser(
        "viz-graph", help="Visualize the agent's dependency graph"
    )
    viz_graph.add_arguments(viz_graph_parser)

    # 'analyze-prompts' command
    analyze_prompts_parser = subparsers.add_parser(
        "analyze-prompts", help="Analyze prompt performance from trajectory data"
    )
    analyze_prompts.add_arguments(analyze_prompts_parser)

    # 'optimize-prompts' command
    optimize_prompts_parser = subparsers.add_parser(
        "optimize-prompts", help="Optimize prompts using DSPy"
    )
    optimize_prompts.add_arguments(optimize_prompts_parser)

    # 'e2e-optimize' command
    e2e_optimize_parser = subparsers.add_parser(
        "e2e-optimize", help="Run end-to-end optimization loop"
    )
    e2e_optimize.add_arguments(e2e_optimize_parser)

    # 'run-exp' command
    run_exp_parser = subparsers.add_parser(
        "run-exp", help="Run experiments defined in a TOML config file"
    )
    run_exp.add_arguments(run_exp_parser)

    # 'plot-exp' command
    plot_exp_parser = subparsers.add_parser(
        "plot-exp", help="Plot and compare experiment results"
    )
    plot_exp.add_arguments(plot_exp_parser)

    if len(sys.argv) > 1 and sys.argv[1] not in subparsers.choices:
        sys.argv.insert(1, "run")

    args = parser.parse_args()

    # Check Docker dependencies for commands that need them
    if args.command in ["run", "init-exp"]:
        check_dependencies()

    if args.command == "run":
        # return run.run_command(args)
        exit_code = run.run_command(args)
        
        if exit_code == 0:
            # Call the loop (it handles the retries internally)
            trigger_ai_remediation(max_retries=5)
            
        return exit_code
    elif args.command == "init-exp":
        return init_exp.run_command(args)
    elif args.command == "viz-graph":
        return viz_graph.run_command(args)
    elif args.command == "analyze-prompts":
        return analyze_prompts.run_command(args)
    elif args.command == "optimize-prompts":
        return optimize_prompts.run_command(args)
    elif args.command == "e2e-optimize":
        return e2e_optimize.run_command(args)
    elif args.command == "run-exp":
        return run_exp.run_command(args)
    elif args.command == "plot-exp":
        return plot_exp.run_command(args)
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
