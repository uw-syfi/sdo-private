import argparse
import sys
import shutil
import subprocess
import threading
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    import tomllib
except ImportError:
    import tomli as tomllib

from rich.progress import (
    Progress,
    SpinnerColumn,
    TextColumn,
    BarColumn,
    TaskProgressColumn,
    TimeElapsedColumn,
)
from rich.console import Console

from app_operator.logger import logger


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "experiment",
        help="Experiment name (e.g., 'example') or full path to TOML config file",
    )
    parser.add_argument(
        "--parallel",
        type=int,
        default=1,
        help="Maximum number of applications to run in parallel",
    )


def tail_file(file_path: Path, stop_event: threading.Event, callback):
    """Tails a file and calls callback with new lines."""
    # Wait for file to exist
    while not stop_event.is_set():
        if file_path.exists():
            break
        time.sleep(0.1)

    try:
        with open(file_path, "r") as f:
            while not stop_event.is_set():
                line = f.readline()
                if not line:
                    time.sleep(0.1)
                    continue
                callback(line.strip())

            # Read remaining
            for line in f.readlines():
                callback(line.strip())
    except Exception:
        pass


def run_experiment_task(
    app_path_str: str,
    exp_name: str,
    progress: Progress,
    task_id,
    log_dir: Path,
) -> bool:
    # Explicitly start the task timer
    progress.start_task(task_id)

    app_path = Path(app_path_str).resolve()
    app_name = app_path.name
    exp_dir = Path.cwd() / "exp" / app_name / exp_name

    log_file = log_dir / f"{app_name}.log"

    # Update status to initializing
    progress.update(task_id, description=f"[cyan]{app_name}[/]: Initializing", completed=0)

    # 1. Init Experiment
    # Remove existing exp dir if it exists to ensure fresh init
    if exp_dir.exists():
        if exp_dir.is_dir():
            shutil.rmtree(exp_dir)
        else:
            exp_dir.unlink()

    # We write logs to the file
    with open(log_file, "w") as f_log:
        f_log.write("=== Initializing Experiment ===\n")
        f_log.write(f"App: {app_path}\n")
        f_log.write(f"Exp: {exp_name}\n")
        f_log.write(f"Time: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        f_log.flush()

        # Run init-exp
        init_cmd = [sys.executable, "-m", "app_operator", "init-exp", str(app_path), exp_name]
        init_proc = subprocess.run(
            init_cmd,
            stdout=f_log,
            stderr=subprocess.STDOUT,
            cwd=Path.cwd()
        )

        if init_proc.returncode != 0:
            progress.update(task_id, description=f"[red]{app_name}[/]: Init Failed")
            return False

        progress.update(task_id, description=f"[cyan]{app_name}[/]: Starting Run", completed=10)

        f_log.write("\n=== Running Experiment ===\n")
        f_log.flush()

        # 2. Run Experiment
        run_cmd = [sys.executable, "-m", "app_operator", "run", str(exp_dir)]

        # Start a thread to monitor the log file for status updates
        stop_tail = threading.Event()

        def check_status(line):
            lower_line = line.lower()
            if "code analysis" in lower_line and "step 1" in lower_line:
                progress.update(
                    task_id,
                    description=f"[yellow]{app_name}[/]: Code Analysis",
                    completed=20)
            elif "generating deployment scripts" in lower_line:
                progress.update(
                    task_id,
                    description=f"[yellow]{app_name}[/]: Script Generation",
                    completed=30)
            elif "deployment attempt" in lower_line:
                # Extract attempt number if possible "Deployment Attempt #1"
                try:
                    parts = line.split("#")
                    attempt = parts[-1].split()[0]
                    progress.update(
                        task_id,
                        description=f"[yellow]{app_name}[/]: Deploy-loop (Attempt {attempt})",
                        completed=40)
                except BaseException:
                    progress.update(
                        task_id,
                        description=f"[yellow]{app_name}[/]: Deployment",
                        completed=40)
            elif "monitoring cycle" in lower_line:
                try:
                    parts = line.split("#")
                    cycle = parts[-1].split()[0]
                    progress.update(
                        task_id,
                        description=f"[yellow]{app_name}[/]: Health-monitor (Attempt {cycle})",
                        completed=70)
                except BaseException:
                    progress.update(
                        task_id,
                        description=f"[yellow]{app_name}[/]: Monitoring",
                        completed=70)
            elif "shutting down" in lower_line:
                progress.update(
                    task_id,
                    description=f"[green]{app_name}[/]: Finishing",
                    completed=90)

        tail_thread = threading.Thread(target=tail_file, args=(log_file, stop_tail, check_status))
        tail_thread.start()

        try:
            # Run the command
            proc = subprocess.run(
                run_cmd,
                stdout=f_log,
                stderr=subprocess.STDOUT,
                cwd=Path.cwd(),
            )
        finally:
            stop_tail.set()
            tail_thread.join()

        # Save trajectory and session logs
        f_log.write("\n=== Collecting Artifacts ===\n")

        # Trajectories
        sds_traj_dir = exp_dir / ".sds" / "trajectories"
        if sds_traj_dir.exists():
            for traj_file in sds_traj_dir.glob("*.json"):
                shutil.copy(traj_file, log_dir)

        # Session files
        sds_dir = exp_dir / ".sds"
        if sds_dir.exists():
            for session_file in sds_dir.glob("gemini_session*.json"):
                shutil.copy(session_file, log_dir)

        if proc.returncode == 0:
            progress.update(task_id, description=f"[green]{app_name}[/]: Done", completed=100)
            return True
        else:
            progress.update(task_id, description=f"[red]{app_name}[/]: Failed", completed=100)
            return False


def run_command(args: argparse.Namespace) -> int:
    # Try to resolve argument as experiment name first
    exp_config_dir = Path("exp_config") / args.experiment
    config_path = exp_config_dir / "config.toml"

    if not config_path.exists():
        # Fallback: check if argument is a direct path to a file
        potential_path = Path(args.experiment).resolve()
        if potential_path.exists() and potential_path.is_file():
            config_path = potential_path
        else:
            logger.error(f"Config file not found. Tried:\n  - {config_path}\n  - {potential_path}")
            return 1

    # Derive exp_name from path structure
    try:
        abs_config_path = config_path.resolve()
        parts = abs_config_path.parts

        # Look for exp_config in path to determine experiment name
        if "exp_config" in parts:
            idx = parts.index("exp_config")
            if idx + 1 < len(parts):
                # Use directory name inside exp_config as experiment name
                exp_name = parts[idx + 1]
            else:
                # Should not happen given logic above but safe fallback
                exp_name = args.experiment
        else:
            # Fallback for paths outside exp_config structure
            exp_name = config_path.parent.name

    except Exception as e:
        logger.error(f"Error parsing experiment name from path: {e}")
        return 1

    with open(config_path, "rb") as f:
        config = tomllib.load(f)

    apps = config.get("apps", [])
    if not apps:
        logger.warning("No apps found in config")
        return 0

    # Ensure log directory matches the experiment location
    # If using exp_config structure: exp_config/<exp_name>/logs
    # If custom path: <config_dir>/logs
    log_dir = config_path.parent / "logs"

    if log_dir.exists():
        shutil.rmtree(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    console = Console()
    console.print(f"[bold]Running Experiment: {exp_name}[/bold]")
    console.print(f"Log Directory: {log_dir}")
    console.print(f"Apps: {len(apps)}")
    console.print(f"Parallelism: {args.parallel}")

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        console=console
    ) as progress:

        futures = {}
        with ThreadPoolExecutor(max_workers=args.parallel) as executor:
            for app in apps:
                task_id = progress.add_task(
                    f"[white]{
                        Path(app).name}[/]: Pending",
                    total=100,
                    start=False)
                futures[executor.submit(run_experiment_task, app, exp_name,
                                        progress, task_id, log_dir)] = app

            for future in as_completed(futures):
                app = futures[future]
                try:
                    future.result()
                except Exception as e:
                    logger.error(f"Error running {app}: {e}")

    return 0
