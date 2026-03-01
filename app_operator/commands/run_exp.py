import argparse
import json
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
from rich.table import Table

from app_operator.logger import logger


def _write_toml_simple(data: dict) -> str:
    """Serialize a dict to TOML format.

    Handles: bool, int, str, list[str], and nested dicts (as [section] tables).
    """
    lines = []
    top_level = {}
    tables = {}

    for key, value in data.items():
        if isinstance(value, dict):
            tables[key] = value
        else:
            top_level[key] = value

    for key, value in top_level.items():
        lines.append(f"{key} = {_toml_value(value)}")

    for section, fields in tables.items():
        sub_tables = {}
        plain_fields = {}
        for k, v in fields.items():
            if isinstance(v, dict):
                sub_tables[k] = v
            else:
                plain_fields[k] = v

        if plain_fields:
            lines.append(f"\n[{section}]")
            for k, v in plain_fields.items():
                lines.append(f"{k} = {_toml_value(v)}")

        for sub_name, sub_fields in sub_tables.items():
            lines.append(f"\n[{section}.{sub_name}]")
            for k, v in sub_fields.items():
                lines.append(f"{k} = {_toml_value(v)}")

    return "\n".join(lines) + "\n"


def _toml_value(value) -> str:
    """Format a Python value as a TOML value string."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    if isinstance(value, list):
        items = ", ".join(_toml_value(item) for item in value)
        return f"[{items}]"
    raise TypeError(f"Unsupported TOML value type: {type(value).__name__}")


def _write_experiment_sds_config(exp_dir: Path, experiment_config: dict) -> None:
    """Write non-apps sections from experiment config as sds.toml in the experiment dir."""
    sds_sections = {k: v for k, v in experiment_config.items() if k not in ("apps", "repeats")}
    if not sds_sections:
        return
    sds_toml_path = exp_dir / "sds.toml"
    sds_toml_path.write_text(_write_toml_simple(sds_sections))
    logger.info(f"Wrote experiment sds config to {sds_toml_path}")


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


def _extract_results(exp_dir: Path) -> dict:
    """Extract deployment iterations and status from trajectory files."""
    traj_dir = exp_dir / ".sds" / "trajectories"
    if not traj_dir.exists():
        return {"status": "unknown", "deployment_iterations": None}

    traj_files = sorted(traj_dir.glob("trajectory_*.json"))
    if not traj_files:
        return {"status": "unknown", "deployment_iterations": None}

    try:
        with open(traj_files[-1], "r") as f:
            traj = json.load(f)
        status = traj.get("metadata", {}).get("status", "unknown")
        deployment_iterations = len(traj.get("deployment", []))
        return {"status": status, "deployment_iterations": deployment_iterations}
    except (json.JSONDecodeError, KeyError, OSError):
        return {"status": "unknown", "deployment_iterations": None}


def _write_results(log_dir: Path, exp_name: str, results: list[dict]) -> None:
    """Write per-app results as JSON to the log directory."""
    has_repeats = any("repeat" in r for r in results)

    entries = []
    for r in results:
        entry = {
            "app": r["app"],
            "status": r["status"],
            "deployment_iterations": r["deployment_iterations"],
        }
        if has_repeats:
            entry["repeat"] = r["repeat"]
        entries.append(entry)

    output: dict = {"experiment": exp_name, "results": entries}

    if has_repeats:
        apps_seen: dict[str, list[dict]] = {}
        for r in results:
            apps_seen.setdefault(r["app"], []).append(r)

        aggregated = []
        for app_name, app_results in apps_seen.items():
            total = len(app_results)
            successes = sum(1 for r in app_results if r.get("success"))
            iters = [r["deployment_iterations"] for r in app_results if r["deployment_iterations"] is not None]
            agg: dict = {
                "app": app_name,
                "success_rate": f"{successes}/{total}",
            }
            if iters:
                agg["deploy_iterations_min"] = min(iters)
                agg["deploy_iterations_max"] = max(iters)
                agg["deploy_iterations_mean"] = round(sum(iters) / len(iters), 2)
            aggregated.append(agg)
        output["aggregated"] = aggregated

    results_path = log_dir / "results.json"
    with open(results_path, "w") as f:
        json.dump(output, f, indent=2)


def _print_summary(console: Console, results: list[dict]) -> None:
    """Print a Rich table summarizing experiment results."""
    has_repeats = any("repeat" in r for r in results)

    if has_repeats:
        table = Table()
        table.add_column("App")
        table.add_column("Success Rate")
        table.add_column("Deploy Iterations (min–max / mean / median)")

        apps_seen: dict[str, list[dict]] = {}
        for r in results:
            apps_seen.setdefault(r["app"], []).append(r)

        for app_name, app_results in apps_seen.items():
            total = len(app_results)
            successes = sum(1 for r in app_results if r.get("success"))
            iters = sorted(r["deployment_iterations"] for r in app_results if r["deployment_iterations"] is not None)
            success_str = f"{successes}/{total}"
            if iters:
                mean = sum(iters) / len(iters)
                mid = len(iters) // 2
                median = iters[mid] if len(iters) % 2 != 0 else (iters[mid - 1] + iters[mid]) / 2
                iter_str = f"{iters[0]}–{iters[-1]} / {mean:.1f} / {median:.1f}"
            else:
                iter_str = "N/A"
            table.add_row(app_name, success_str, iter_str)
    else:
        table = Table()
        table.add_column("App")
        table.add_column("Status")
        table.add_column("Deploy Iterations")

        for r in results:
            iterations = str(r["deployment_iterations"]) if r["deployment_iterations"] is not None else "N/A"
            table.add_row(r["app"], r["status"], iterations)

    console.print(table)


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
    experiment_config: dict | None = None,
    repeat_idx: int = 0,
    total_repeats: int = 1,
) -> dict:
    # Explicitly start the task timer
    progress.start_task(task_id)

    app_path = Path(app_path_str).resolve()
    app_name = app_path.name

    if total_repeats > 1:
        run_exp_name = f"{exp_name}_run_{repeat_idx + 1}"
        log_file = log_dir / f"{app_name}_run_{repeat_idx + 1}.log"
    else:
        run_exp_name = exp_name
        log_file = log_dir / f"{app_name}.log"

    exp_dir = Path.cwd() / "exp" / app_name / run_exp_name

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
        f_log.write(f"Exp: {run_exp_name}\n")
        f_log.write(f"Time: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        f_log.flush()

        # Run init-exp
        init_cmd = [sys.executable, "-m", "app_operator", "init-exp", str(app_path), run_exp_name]
        init_proc = subprocess.run(
            init_cmd,
            stdout=f_log,
            stderr=subprocess.STDOUT,
            cwd=Path.cwd()
        )

        if init_proc.returncode != 0:
            progress.update(task_id, description=f"[red]{app_name}[/]: Init Failed")
            result = {"app": app_name, "success": False, "status": "unknown", "deployment_iterations": None}
            if total_repeats > 1:
                result["repeat"] = repeat_idx + 1
            return result

        # Write experiment sds config overrides into the experiment directory
        if experiment_config:
            _write_experiment_sds_config(exp_dir, experiment_config)

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

        extracted = _extract_results(exp_dir)

        repeat_field = {"repeat": repeat_idx + 1} if total_repeats > 1 else {}
        if proc.returncode == 0:
            progress.update(task_id, description=f"[green]{app_name}[/]: Done", completed=100)
            return {"app": app_name, "success": True, **extracted, **repeat_field}
        else:
            progress.update(task_id, description=f"[red]{app_name}[/]: Failed", completed=100)
            return {"app": app_name, "success": False, **extracted, **repeat_field}


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

    repeats = config.get("repeats", 1)
    if not isinstance(repeats, int) or repeats < 1:
        logger.error(f"'repeats' must be a positive integer, got: {repeats!r}")
        return 1

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
    console.print(f"Repeats: {repeats}")
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
                for i in range(repeats):
                    if repeats > 1:
                        label = f"[white]{Path(app).name} (run {i + 1}/{repeats})[/]: Pending"
                    else:
                        label = f"[white]{Path(app).name}[/]: Pending"
                    task_id = progress.add_task(label, total=100, start=False)
                    futures[executor.submit(
                        run_experiment_task, app, exp_name,
                        progress, task_id, log_dir, config,
                        i, repeats,
                    )] = (app, i)

            results = []
            for future in as_completed(futures):
                app, _ = futures[future]
                try:
                    result = future.result()
                    results.append(result)
                except Exception as e:
                    logger.error(f"Error running {app}: {e}")
                    results.append({
                        "app": Path(app).name,
                        "success": False,
                        "status": "unknown",
                        "deployment_iterations": None,
                    })

    _write_results(log_dir, exp_name, results)
    _print_summary(console, results)

    return 0
