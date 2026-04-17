import argparse

from app_operator.core import SdsOperatorError, load_config, logger
from app_operator.operator_factory import create_operator

# POSIX exit code for processes terminated by SIGINT (128 + SIGINT=2).
EXIT_INTERRUPTED = 130


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Add arguments specific to the 'run' command."""
    parser.add_argument("directory", metavar="DIR", help="Directory path of the repository to deploy")
    parser.add_argument(
        "--config",
        metavar="FILE",
        help="Path to configuration file (default: sds.toml in target dir)",
    )


def run_command(args: argparse.Namespace) -> int:
    """Execute the 'run' command logic.

    Translates operator exceptions into POSIX process exit codes.  The
    operator itself raises on failure; this boundary decides the exit code.
    """
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
        create_operator(shared_kwargs, config).run()
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        return EXIT_INTERRUPTED
    except SdsOperatorError as e:
        # Domain failures: DeploymentError, MonitoringError, AgentError, etc.
        logger.error(f"Operator failed: {e}")
        return 1
    except ValueError as e:
        logger.error(f"Error: {e}")
        return 1
    except (OSError, RuntimeError) as e:
        logger.error(f"✗ Unexpected error: {e}", exc_info=True)
        return 1
    except Exception as e:
        # Top-level safety net: log with traceback and return non-zero so the
        # CLI always terminates cleanly rather than emitting a bare traceback.
        logger.error(f"✗ Unexpected error: {e}", exc_info=True)
        return 1

    return 0
