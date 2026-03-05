import argparse
import json
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore[reportMissingImports]

from rich.console import Console
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table
from rich.text import Text

from app_operator.logger import logger


class _ActiveSpinnerColumn(SpinnerColumn):
    """Spinner that only animates once the task has been started."""

    def render(self, task):
        if task.start_time is None:
            return Text(" ")
        return super().render(task)


@dataclass
class AppResult:
    app: str
    success: bool
    status: str
    deployment_iterations: int | None
    repeat: int | None = None
    elapsed_seconds: float | None = None
    phase_durations: dict | None = None
    total_tokens: int | None = None


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
        "experiments",
        nargs="+",
        help="One or more experiment names or full paths to TOML config files",
    )
    parser.add_argument(
        "--parallel",
        type=int,
        default=1,
        help="Maximum number of applications to run in parallel",
    )


def _extract_results(exp_dir: Path) -> dict:
    """Extract deployment iterations, status, and token usage from trajectory files."""
    traj_dir = exp_dir / ".sds" / "trajectories"
    if not traj_dir.exists():
        return {"status": "unknown", "deployment_iterations": None, "total_tokens": None}

    traj_files = sorted(traj_dir.glob("trajectory_*.json"))
    if not traj_files:
        return {"status": "unknown", "deployment_iterations": None, "total_tokens": None}

    try:
        with open(traj_files[-1]) as f:
            traj = json.load(f)
        metadata = traj.get("metadata", {})
        status = metadata.get("status", "unknown")
        deployment_iterations = len(traj.get("deployment", []))
        token_usage = metadata.get("token_usage", {})
        total_tokens = token_usage.get("total_tokens") if token_usage else None
        return {
            "status": status,
            "deployment_iterations": deployment_iterations,
            "total_tokens": total_tokens,
        }
    except (json.JSONDecodeError, KeyError, OSError):
        return {"status": "unknown", "deployment_iterations": None, "total_tokens": None}


def _load_existing_results(log_dir: Path) -> list[AppResult]:
    """Load completed results from a previous run's results.json, if present."""
    results_path = log_dir / "results.json"
    if not results_path.exists():
        return []
    try:
        with open(results_path) as f:
            data = json.load(f)
        results = []
        for entry in data.get("results", []):
            results.append(
                AppResult(
                    app=entry["app"],
                    success=entry.get("success", entry.get("status") == "success"),
                    status=entry["status"],
                    deployment_iterations=entry.get("deployment_iterations"),
                    repeat=entry.get("repeat"),
                    elapsed_seconds=entry.get("elapsed_seconds"),
                    phase_durations=entry.get("phase_durations"),
                    total_tokens=entry.get("total_tokens"),
                )
            )
        return results
    except (json.JSONDecodeError, KeyError, OSError):
        return []


def _write_results(log_dir: Path, exp_name: str, results: list[AppResult]) -> None:
    """Write per-app results as JSON to the log directory."""
    has_repeats = any(r.repeat is not None for r in results)

    entries = []
    for r in results:
        entry: dict = {
            "app": r.app,
            "success": r.success,
            "status": r.status,
            "deployment_iterations": r.deployment_iterations,
            "elapsed_seconds": r.elapsed_seconds,
            "phase_durations": r.phase_durations,
            "total_tokens": r.total_tokens,
        }
        if has_repeats:
            entry["repeat"] = r.repeat
        entries.append(entry)

    output: dict = {"experiment": exp_name, "results": entries}

    if has_repeats:
        apps_seen: dict[str, list[AppResult]] = {}
        for r in results:
            apps_seen.setdefault(r.app, []).append(r)

        aggregated = []
        for app_name, app_results in sorted(apps_seen.items()):
            total = len(app_results)
            successes = sum(1 for r in app_results if r.success)
            iters = [r.deployment_iterations for r in app_results if r.deployment_iterations is not None]
            elapsed = [r.elapsed_seconds for r in app_results if r.elapsed_seconds is not None]
            tokens = [r.total_tokens for r in app_results if r.total_tokens is not None]
            agg: dict = {
                "app": app_name,
                "success_rate": f"{successes}/{total}",
            }
            if iters:
                agg["deploy_iterations_min"] = min(iters)
                agg["deploy_iterations_max"] = max(iters)
                agg["deploy_iterations_mean"] = round(sum(iters) / len(iters), 2)
            if elapsed:
                agg["elapsed_seconds_min"] = min(elapsed)
                agg["elapsed_seconds_max"] = max(elapsed)
                agg["elapsed_seconds_mean"] = round(sum(elapsed) / len(elapsed), 1)
            if tokens:
                agg["total_tokens_min"] = min(tokens)
                agg["total_tokens_max"] = max(tokens)
                agg["total_tokens_mean"] = round(sum(tokens) / len(tokens))
            aggregated.append(agg)
        output["aggregated"] = aggregated

    results_path = log_dir / "results.json"
    with open(results_path, "w") as f:
        json.dump(output, f, indent=2)


def _fmt_seconds(seconds: float) -> str:
    """Format a duration in seconds as a human-readable string."""
    seconds = round(seconds)
    if seconds < 60:
        return f"{seconds}s"
    m, s = divmod(seconds, 60)
    return f"{m}m{s:02d}s"


_PHASE_LABELS = {
    "code_analysis": "analysis",
    "script_generation": "scripting",
    "deployment": "deploy",
    "monitoring": "monitor",
    "finishing": "finishing",
}


def _fmt_phase_durations(phases: dict) -> str:
    """Format phase durations as a compact string, e.g. 'analysis:2m deploy:5m mon:3m'."""
    parts = []
    for key, label in _PHASE_LABELS.items():
        if key in phases:
            parts.append(f"{label}:{_fmt_seconds(phases[key])}")
    # Include any phases not in the known list
    for key, val in phases.items():
        if key not in _PHASE_LABELS:
            parts.append(f"{key}:{_fmt_seconds(val)}")
    return " ".join(parts) if parts else "N/A"


def _print_summary(console: Console, results: list[AppResult]) -> None:
    """Print a Rich table summarizing experiment results."""
    has_repeats = any(r.repeat is not None for r in results)

    if has_repeats:
        table = Table()
        table.add_column("App")
        table.add_column("Success Rate")
        table.add_column("Deploy Iterations (min–max / mean / median)")
        table.add_column("Elapsed (min–max / mean)")
        table.add_column("Tokens (min–max / mean)")

        apps_seen: dict[str, list[AppResult]] = {}
        for r in results:
            apps_seen.setdefault(r.app, []).append(r)

        for app_name, app_results in sorted(apps_seen.items()):
            total = len(app_results)
            successes = sum(1 for r in app_results if r.success)
            iters = sorted(r.deployment_iterations for r in app_results if r.deployment_iterations is not None)
            elapsed = sorted(r.elapsed_seconds for r in app_results if r.elapsed_seconds is not None)
            tokens = sorted(r.total_tokens for r in app_results if r.total_tokens is not None)
            success_str = f"{successes}/{total}"
            if iters:
                mean = sum(iters) / len(iters)
                mid = len(iters) // 2
                median = iters[mid] if len(iters) % 2 != 0 else (iters[mid - 1] + iters[mid]) / 2
                iter_str = f"{iters[0]}–{iters[-1]} / {mean:.1f} / {median:.1f}"
            else:
                iter_str = "N/A"
            if elapsed:
                mean_elapsed = sum(elapsed) / len(elapsed)
                elapsed_str = f"{_fmt_seconds(elapsed[0])}–{_fmt_seconds(elapsed[-1])} / {_fmt_seconds(mean_elapsed)}"
            else:
                elapsed_str = "N/A"
            if tokens:
                tok_mean = sum(tokens) // len(tokens)
                tokens_str = f"{tokens[0]:,}–{tokens[-1]:,} / {tok_mean:,}"
            else:
                tokens_str = "N/A"
            table.add_row(app_name, success_str, iter_str, elapsed_str, tokens_str)
    else:
        table = Table()
        table.add_column("App")
        table.add_column("Status")
        table.add_column("Deploy Iterations")
        table.add_column("Elapsed")
        table.add_column("Total Tokens")
        table.add_column("Phase Durations")

        for r in results:
            iterations = str(r.deployment_iterations) if r.deployment_iterations is not None else "N/A"
            elapsed = _fmt_seconds(r.elapsed_seconds) if r.elapsed_seconds is not None else "N/A"
            tok_str = f"{r.total_tokens:,}" if r.total_tokens is not None else "N/A"
            phases = _fmt_phase_durations(r.phase_durations) if r.phase_durations else "N/A"
            table.add_row(r.app, r.status, iterations, elapsed, tok_str, phases)

    console.print(table)


def tail_file(file_path: Path, stop_event: threading.Event, callback):
    """Tails a file and calls callback with new lines."""
    # Wait for file to exist
    while not stop_event.is_set():
        if file_path.exists():
            break
        time.sleep(0.1)

    try:
        with open(file_path) as f:
            while not stop_event.is_set():
                line = f.readline()
                if not line:
                    time.sleep(0.1)
                    continue
                callback(line.strip())

            # Read remaining
            for line in f:
                callback(line.strip())
    except Exception:
        pass


def run_experiment_task(
    app_path_str: str,
    exp_name: str,
    progress: Progress,
    task_id,
    overall_task_id,
    log_dir: Path,
    experiment_config: dict | None = None,
    repeat_idx: int = 0,
    total_repeats: int = 1,
) -> AppResult:
    # Make the task visible and start the timer
    progress.update(task_id, visible=True)
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

    repeat_suffix = f" ({repeat_idx + 1}/{total_repeats})" if total_repeats > 1 else ""
    display_name = f"{exp_name}/{app_name}{repeat_suffix}"

    # Update status to initializing
    progress.update(task_id, description=f"[cyan]{display_name}[/]: Initializing", completed=0)

    # 1. Init Experiment
    # Teardown any existing Docker stack before wiping the directory
    if exp_dir.exists():
        deploy_sh = exp_dir / ".sds" / "deploy.sh"
        if deploy_sh.exists():
            progress.update(task_id, description=f"[cyan]{display_name}[/]: Teardown", completed=0)
            subprocess.run(
                ["bash", str(deploy_sh), "cleanup"],
                cwd=exp_dir,
                capture_output=True,
                timeout=120,
            )
        # Remove existing exp dir to ensure fresh init
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
        init_proc = subprocess.run(init_cmd, stdout=f_log, stderr=subprocess.STDOUT, cwd=Path.cwd())

        if init_proc.returncode != 0:
            progress.update(task_id, description=f"[red]{display_name}[/]: Init Failed", completed=100)
            time.sleep(1)
            progress.update(task_id, visible=False)
            progress.advance(overall_task_id)
            return AppResult(
                app=app_name,
                success=False,
                status="unknown",
                deployment_iterations=None,
                repeat=repeat_idx + 1 if total_repeats > 1 else None,
            )

        # Write experiment sds config overrides into the experiment directory
        if experiment_config:
            _write_experiment_sds_config(exp_dir, experiment_config)

        progress.update(task_id, description=f"[cyan]{display_name}[/]: Starting Run", completed=10)

        f_log.write("\n=== Running Experiment ===\n")
        f_log.flush()

        # 2. Run Experiment
        run_cmd = [sys.executable, "-m", "app_operator", "run", str(exp_dir)]

        # Phase timing state
        run_start = time.monotonic()
        phase_state: dict = {"current": None, "start": None, "durations": {}}

        def _transition_phase(name: str) -> None:
            now = time.monotonic()
            prev = phase_state["current"]
            if prev is not None:
                phase_state["durations"][prev] = phase_state["durations"].get(prev, 0.0) + (now - phase_state["start"])
            phase_state["current"] = name
            phase_state["start"] = now

        # Start a thread to monitor the log file for status updates
        stop_tail = threading.Event()

        def check_status(line):
            lower_line = line.lower()
            if "code analysis" in lower_line and "step 1" in lower_line:
                _transition_phase("code_analysis")
                progress.update(task_id, description=f"[yellow]{display_name}[/]: Code Analysis", completed=20)
            elif "generating deployment scripts" in lower_line:
                _transition_phase("script_generation")
                progress.update(task_id, description=f"[yellow]{display_name}[/]: Script Generation", completed=30)
            elif "deployment attempt" in lower_line:
                _transition_phase("deployment")
                # Extract attempt number if possible "Deployment Attempt #1"
                try:
                    parts = line.split("#")
                    attempt = parts[-1].split()[0]
                    progress.update(
                        task_id, description=f"[yellow]{display_name}[/]: Deploy-loop (Attempt {attempt})", completed=40
                    )
                except Exception:
                    progress.update(task_id, description=f"[yellow]{display_name}[/]: Deployment", completed=40)
            elif "monitoring cycle" in lower_line:
                _transition_phase("monitoring")
                try:
                    parts = line.split("#")
                    cycle = parts[-1].split()[0]
                    progress.update(
                        task_id,
                        description=f"[yellow]{display_name}[/]: Health-monitor (Attempt {cycle})",
                        completed=70,
                    )
                except Exception:
                    progress.update(task_id, description=f"[yellow]{display_name}[/]: Monitoring", completed=70)
            elif "shutting down" in lower_line:
                _transition_phase("finishing")
                progress.update(task_id, description=f"[green]{display_name}[/]: Finishing", completed=90)

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

        # Finalize last phase and compute totals
        _transition_phase("_done")
        elapsed_seconds = round(time.monotonic() - run_start, 1)
        phase_durations = {k: round(v, 1) for k, v in phase_state["durations"].items()} or None

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
        repeat = repeat_idx + 1 if total_repeats > 1 else None

        if proc.returncode == 0:
            progress.update(task_id, description=f"[green]{display_name}[/]: Done", completed=100)
            time.sleep(1)
            progress.update(task_id, visible=False)
            progress.advance(overall_task_id)
            return AppResult(
                app=app_name,
                success=True,
                repeat=repeat,
                elapsed_seconds=elapsed_seconds,
                phase_durations=phase_durations,
                **extracted,
            )
        else:
            progress.update(task_id, description=f"[red]{display_name}[/]: Failed", completed=100)
            time.sleep(1)
            progress.update(task_id, visible=False)
            progress.advance(overall_task_id)
            return AppResult(
                app=app_name,
                success=False,
                repeat=repeat,
                elapsed_seconds=elapsed_seconds,
                phase_durations=phase_durations,
                **extracted,
            )


def run_app_repeats(
    app_path_str: str,
    exp_name: str,
    progress: Progress,
    repeat_task_ids: list[tuple[int, object]],
    overall_task_id,
    log_dir: Path,
    experiment_config: dict | None = None,
    total_repeats: int = 1,
) -> list[AppResult]:
    results = []
    for repeat_idx, task_id in repeat_task_ids:
        result = run_experiment_task(
            app_path_str,
            exp_name,
            progress,
            task_id,
            overall_task_id,
            log_dir,
            experiment_config,
            repeat_idx,
            total_repeats,
        )
        results.append(result)
    return results


def _resolve_experiment(experiment_str: str) -> tuple[str, Path, dict, Path] | None:
    """Resolve an experiment string to (exp_name, config_path, config, log_dir).

    Returns None and logs an error if resolution fails.
    """
    exp_config_dir = Path("exp_config") / experiment_str
    config_path = exp_config_dir / "config.toml"

    if not config_path.exists():
        potential_path = Path(experiment_str).resolve()
        if potential_path.exists() and potential_path.is_file():
            config_path = potential_path
        else:
            logger.error(f"Config file not found. Tried:\n  - {config_path}\n  - {potential_path}")
            return None

    try:
        abs_config_path = config_path.resolve()
        parts = abs_config_path.parts

        if "exp_config" in parts:
            idx = parts.index("exp_config")
            if idx + 1 < len(parts):
                exp_name = parts[idx + 1]
            else:
                exp_name = experiment_str
        else:
            exp_name = config_path.parent.name

    except Exception as e:
        logger.error(f"Error parsing experiment name from path: {e}")
        return None

    with open(config_path, "rb") as f:
        config = tomllib.load(f)

    log_dir = config_path.parent / "logs"

    return exp_name, config_path, config, log_dir


def run_command(args: argparse.Namespace) -> int:
    console = Console()
    multi = len(args.experiments) > 1

    # Resolve all experiments up front
    resolved = []
    for experiment_str in args.experiments:
        result = _resolve_experiment(experiment_str)
        if result is None:
            return 1
        resolved.append(result)

    # Validate all experiments before starting any work
    for exp_name, _config_path, config, _log_dir in resolved:
        apps = config.get("apps", [])
        if not apps:
            logger.warning(f"No apps found in config for experiment '{exp_name}'")
            return 0

        repeats = config.get("repeats", 1)
        if not isinstance(repeats, int) or repeats < 1:
            logger.error(f"'repeats' must be a positive integer, got: {repeats!r} (experiment '{exp_name}')")
            return 1

    # Print summary header
    for exp_name, _config_path, config, log_dir in resolved:
        apps = config.get("apps", [])
        repeats = config.get("repeats", 1)
        console.print(f"[bold]Running Experiment: {exp_name}[/bold]")
        console.print(f"Log Directory: {log_dir}")
        console.print(f"Apps: {len(apps)}")
        console.print(f"Repeats: {repeats}")
    console.print(f"Parallelism: {args.parallel}")

    # Prepare log directories and load any existing results (for resume support)
    results_by_exp: dict[str, list[AppResult]] = {}
    completed_keys_by_exp: dict[str, set[tuple[str, int | None]]] = {}
    for exp_name, _config_path, _config, log_dir in resolved:
        log_dir.mkdir(parents=True, exist_ok=True)
        existing = _load_existing_results(log_dir)
        results_by_exp[exp_name] = list(existing)
        completed_keys_by_exp[exp_name] = {(r.app, r.repeat) for r in existing}
        if existing:
            console.print(f"  Resuming: {len(existing)} run(s) already complete, skipping.")

    exp_locks = {exp_name: threading.Lock() for exp_name, _, _, _ in resolved}

    # Count total runs and already-completed runs for the global progress bar
    total_runs = 0
    already_done_count = 0
    for exp_name, _config_path, config, _log_dir in resolved:
        apps = config.get("apps", [])
        repeats = config.get("repeats", 1)
        completed_keys = completed_keys_by_exp[exp_name]
        for app in apps:
            app_name = Path(app).name
            for i in range(repeats):
                repeat = i + 1 if repeats > 1 else None
                total_runs += 1
                if (app_name, repeat) in completed_keys:
                    already_done_count += 1

    with Progress(
        _ActiveSpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        overall_task_id = progress.add_task(
            "[bold]Total runs[/bold]",
            total=total_runs,
            completed=already_done_count,
        )

        # futures maps future -> (app, exp_name, log_dir, config)
        futures = {}
        with ThreadPoolExecutor(max_workers=args.parallel) as executor:
            for exp_name, _config_path, config, log_dir in resolved:
                apps = config.get("apps", [])
                repeats = config.get("repeats", 1)
                completed_keys = completed_keys_by_exp[exp_name]
                for app in apps:
                    app_name = Path(app).name
                    repeat_task_ids = []
                    for i in range(repeats):
                        repeat = i + 1 if repeats > 1 else None
                        if (app_name, repeat) in completed_keys:
                            continue
                        repeat_suffix = f" ({i + 1}/{repeats})" if repeats > 1 else ""
                        label = f"[white]{exp_name}/{app_name}{repeat_suffix}[/]: Pending"
                        task_id = progress.add_task(label, total=100, completed=0, start=False, visible=False)
                        repeat_task_ids.append((i, task_id))
                    if not repeat_task_ids:
                        continue
                    future = executor.submit(
                        run_app_repeats,
                        app,
                        exp_name,
                        progress,
                        repeat_task_ids,
                        overall_task_id,
                        log_dir,
                        config,
                        repeats,
                    )
                    futures[future] = (app, exp_name, log_dir, config)

            # Collect results and write incrementally so ctrl-c preserves progress
            for future in as_completed(futures):
                app, exp_name, log_dir, config = futures[future]
                lock = exp_locks[exp_name]
                try:
                    repeat_results = future.result()
                    with lock:
                        results_by_exp[exp_name].extend(repeat_results)
                        _write_results(log_dir, exp_name, results_by_exp[exp_name])
                except Exception as e:
                    logger.error(f"Error running {app}: {e}")
                    repeats = config.get("repeats", 1)
                    completed_keys = completed_keys_by_exp[exp_name]
                    with lock:
                        for i in range(repeats):
                            repeat = i + 1 if repeats > 1 else None
                            if (Path(app).name, repeat) not in completed_keys:
                                results_by_exp[exp_name].append(
                                    AppResult(
                                        app=Path(app).name,
                                        success=False,
                                        status="unknown",
                                        deployment_iterations=None,
                                        repeat=repeat,
                                    )
                                )
                        _write_results(log_dir, exp_name, results_by_exp[exp_name])

    for exp_name, _config_path, _config, log_dir in resolved:
        results = results_by_exp[exp_name]
        _write_results(log_dir, exp_name, results)
        if multi:
            console.print(f"\n[bold]Experiment: {exp_name}[/bold]")
        _print_summary(console, results)

    return 0
