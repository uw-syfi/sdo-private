import argparse

from app_operator.cli_agent.operator import AppOperator
from app_operator.cli_agent import create_agent_from_config
from app_operator.langgraph import LangGraphOperator
from app_operator.adk import AdkOperator
from app_operator.config import load_config
from app_operator.logger import logger


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Add arguments specific to the 'run' command."""
    parser.add_argument(
        "directory", metavar="DIR", help="Directory path of the repository to deploy"
    )
    parser.add_argument(
        "--config",
        metavar="FILE",
        help="Path to configuration file (default: sds.toml in target dir)",
    )
    parser.add_argument(
        "--tui",
        action="store_true",
        dest="tui",
        default=False,
        help="Enable Textual TUI (cli_agent runtime only)",
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

    use_tui = args.tui
    if use_tui and config.runtime.impl != "cli_agent":
        logger.warning("TUI is only supported for cli_agent; falling back to CLI.")
        use_tui = False

    shared_kwargs = dict(
        repo_path=args.directory,
        health_check_interval=interval,
        health_check_max_count=config.operator.monitoring_max_iters,
        max_deployment_attempts=config.operator.deployment_max_iters,
        config=config,
    )

    try:
        if config.runtime.impl == "langgraph":
            return LangGraphOperator(**shared_kwargs).run()
        elif config.runtime.impl == "adk":
            return AdkOperator(**shared_kwargs).run()
        else:
            # cli_agent
            if use_tui:
                from app_operator.ui.textual_tui import OperatorTUI

                def op_factory(ui):
                    agent = create_agent_from_config(args.directory, config=config)
                    return AppOperator(**shared_kwargs, agent=agent, ui=ui)

                app = OperatorTUI(op_factory)
                app.run()
                return app._exit_code
            else:
                agent = create_agent_from_config(args.directory, config=config)
                return AppOperator(**shared_kwargs, agent=agent).run()

    except ValueError as e:
        logger.error(f"Error: {e}")
        return 1

    except Exception as e:
        logger.error(f"✗ Unexpected error: {e}")
        return 1
