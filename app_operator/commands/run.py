import argparse
import subprocess
import time

from app_operator.config import load_config
from app_operator.logger import logger
from app_operator.operator_factory import create_operator
from app_operator.prompts import get_loader


def trigger_ai_remediation(max_retries: int):
    """
    Runs the Gemini SRE agent in a loop until the system is healthy
    or we run out of retries.
    """
    try:
        playbook_content = get_loader().render("sre/startup_playbook.jinja2")
    except Exception as e:
        logger.warning(f"⚠️  Failed to load SRE playbook: {e}")
        return False

    print("\n" + "=" * 50)
    print(f"🤖 [SDS Operator] STARTING AUTO-HEALING LOOP (Max Retries: {max_retries})")
    print("=" * 50)

    for attempt in range(1, max_retries + 1):
        print(f"\n🔄 [Attempt {attempt}/{max_retries}] Summoning SRE Agent...")

        try:
            process = subprocess.Popen(
                ["gemini", "-y", playbook_content],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                universal_newlines=True,
            )

            full_output = ""
            for line in process.stdout or []:
                print(line, end="")
                full_output += line

            process.wait()

            if "SYSTEM HEALTHY" in full_output:
                print(f"\n✅ [SDS Operator] Success! System healed on attempt {attempt}.")
                return True

            else:
                print("\n⚠️ [SDS Operator] Agent finished, but system is NOT healthy yet.")
                print("   Retrying in 5 seconds...")
                time.sleep(5)

        except Exception as e:
            logger.error(f"❌ Execution error: {e}")
            time.sleep(5)

    print(f"\n❌ [SDS Operator] Failed to heal system after {max_retries} attempts.")
    return False


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Add arguments specific to the 'run' command."""
    parser.add_argument("directory", metavar="DIR", help="Directory path of the repository to deploy")
    parser.add_argument(
        "--config",
        metavar="FILE",
        help="Path to configuration file (default: sds.toml in target dir)",
    )


def run_command(args: argparse.Namespace) -> int:
    """Execute the 'run' command logic."""
    if not args.directory:
        # This case should ideally be handled by argparse if 'directory' was required
        # but including for robustness.
        logger.error("Error: Directory not specified for 'run' command.")
        return 1

    config = load_config(args.directory, args.config)
    interval = config.operator.interval

    if interval < 1:
        logger.error("Error: interval must be at least 1 second")
        return 1

    shared_kwargs = {
        "repo_path": args.directory,
        "health_check_interval": interval,
        "health_check_max_count": config.operator.monitoring_max_iters,
        "max_deployment_attempts": config.operator.deployment_max_iters,
        "config": config,
    }

    try:
        exit_code = create_operator(shared_kwargs, config).run()

    except ValueError as e:
        logger.error(f"Error: {e}")
        return 1

    except Exception as e:
        logger.error(f"✗ Unexpected error: {e}")
        return 1

    if exit_code == 0:
        trigger_ai_remediation(max_retries=5)
    return exit_code
