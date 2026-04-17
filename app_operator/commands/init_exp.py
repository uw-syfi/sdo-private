import argparse
import shutil
import subprocess
from pathlib import Path

from app_operator.core import logger


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Add arguments specific to the 'init-exp' command."""
    parser.add_argument(
        "app_path",
        help="Path to the source application directory (e.g., apps/train-ticket)",
    )
    parser.add_argument(
        "exp_name",
        help="Name of the new experiment (will be created under exp/<app-name>/<exp-name>)",
    )


def run_command(args: argparse.Namespace) -> int:
    """Execute the 'init-exp' command logic."""
    app_path = Path(args.app_path).resolve()
    exp_name = args.exp_name

    if not app_path.exists():
        logger.error(f"Error: Source path '{app_path}' does not exist.")
        return 1

    if not app_path.is_dir():
        logger.error(f"Error: Source path '{app_path}' is not a directory.")
        return 1

    app_name = app_path.name
    # Target path: exp/<app-name>/<exp-name>
    # We resolve this relative to current working directory
    target_path = Path.cwd() / "exp" / app_name / exp_name

    if target_path.exists():
        logger.error(f"Error: Experiment directory '{target_path}' already exists.")
        return 1

    logger.info(f"Initializing experiment '{exp_name}' for app '{app_name}'...")
    logger.info(f"Source: {app_path}")
    logger.info(f"Target: {target_path}")

    try:
        # Ensure parent directories exist
        target_path.parent.mkdir(parents=True, exist_ok=True)

        # Copy application
        shutil.copytree(app_path, target_path)

        # Remove git history
        git_path = target_path / ".git"
        if git_path.exists():
            if git_path.is_dir():
                shutil.rmtree(git_path, ignore_errors=True)
            else:
                git_path.unlink()

        # Remove .sds directory if it exists
        sds_dir = target_path / ".sds"
        if sds_dir.exists():
            shutil.rmtree(sds_dir, ignore_errors=True)

        # Seed .sds/ from .sds-seed/ in the source app if present
        sds_seed_dir = app_path / ".sds-seed"
        if sds_seed_dir.exists():
            shutil.copytree(sds_seed_dir, sds_dir)

        # Initialize new git repo
        subprocess.run(
            ["git", "init", "-b", "main"],
            cwd=target_path,
            check=True,
            stdout=subprocess.DEVNULL,
        )
        subprocess.run(
            ["git", "add", "."],
            cwd=target_path,
            check=True,
            stdout=subprocess.DEVNULL,
        )
        subprocess.run(
            ["git", "-c", "user.name=sds", "-c", "user.email=sds@localhost", "commit", "-m", "Initial commit"],
            cwd=target_path,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        logger.info(f"Successfully initialized experiment at '{target_path}'")
        return 0

    except (OSError, subprocess.CalledProcessError, ValueError) as e:
        logger.error(f"Error initializing experiment: {e}")
        # Cleanup if partial failure?
        # For now, let user handle it to avoid accidental data loss logic
        return 1
