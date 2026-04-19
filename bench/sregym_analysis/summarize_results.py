import argparse
import csv
import glob
import json
import os
import re
import sys

try:
    import matplotlib.pyplot as plt
    import numpy as np

    HAS_PLOTTING = True

    _base = plt.rcParams["font.size"]  # default 10
    plt.rcParams.update(
        {
            "font.size": _base * 1.25,
            "axes.titlesize": _base * 1.5,  # default "large" ~= 1.2x base
            "axes.labelsize": _base * 1.25,
            "xtick.labelsize": _base * 1.25,
            "ytick.labelsize": _base * 1.25,
            "legend.fontsize": _base * 1.25,
        }
    )
except ImportError:
    HAS_PLOTTING = False
    plt = None
    np = None

try:
    import yaml

    HAS_YAML = True
except ImportError:
    HAS_YAML = False


try:
    import tomllib  # py311+
except ModuleNotFoundError:  # pragma: no cover
    try:
        import tomli as tomllib
    except ModuleNotFoundError:
        tomllib = None


def _is_autonomous_submit_experiment(csv_path):
    """Return True iff the experiment producing ``csv_path`` ran in
    autonomous-submit mode.

    Detected by walking up from the CSV until we find an adjacent
    ``experiment_config.toml`` and reading
    ``[agent.cli_agent].autonomous_submit``. In that mode TTL and TTM are
    both measured from fault injection, so the analytics must not treat
    ``TTM`` as ``TTL + mitigation_phase``.
    """
    if tomllib is None:
        return False
    d = os.path.dirname(os.path.abspath(csv_path))
    for _ in range(8):  # bounded walk-up; experiment dir is always shallow
        cfg = os.path.join(d, "experiment_config.toml")
        if os.path.isfile(cfg):
            try:
                with open(cfg, "rb") as f:
                    data = tomllib.load(f)
            except Exception:
                return False
            agents_block = data.get("agent", {}) if isinstance(data, dict) else {}
            cli_cfg = agents_block.get("cli_agent", {}) if isinstance(agents_block, dict) else {}
            return bool(cli_cfg.get("autonomous_submit"))
        parent = os.path.dirname(d)
        if parent == d:
            return False
        d = parent
    return False


def _time_to_mitigation(row):
    """Mitigation-phase duration (seconds), or None when unavailable.

    Sequential mode: ``TTM`` is wall-clock from fault injection through
    mitigation submit, so the mitigation-phase-only time is ``TTM - TTL``.
    Autonomous mode: ``TTM`` is already wall-clock from fault injection to
    the agent's ``submit_mitigation`` call, independent of when/if
    diagnosis was submitted; return it directly.
    """
    try:
        ttm = float(row["TTM"])
    except (ValueError, TypeError, KeyError):
        return None
    if row.get("autonomous"):
        return ttm
    try:
        ttl = float(row["TTL"])
    except (ValueError, TypeError, KeyError):
        return None
    return ttm - ttl


def _time_to_resolution(row):
    """Wall-clock from fault injection to full resolution, or None.

    Sequential mode: diagnosis then mitigation, so resolution is just
    ``TTM`` (which already includes the diagnosis phase).
    Autonomous mode: diagnosis and mitigation are submitted independently
    and in either order, so resolution is ``max(TTL, TTM)``.
    """
    try:
        ttm = float(row["TTM"])
    except (ValueError, TypeError, KeyError):
        return None
    try:
        ttl = float(row["TTL"])
    except (ValueError, TypeError, KeyError):
        return ttm
    if row.get("autonomous"):
        return max(ttl, ttm)
    return ttm


def find_results_csvs(log_dir):
    """Return SREGym results CSV paths under *log_dir*.

    Prefers the current layout (``problem_runs/<ts>_<pid>/results_<ts>.csv``).
    Falls back to the legacy flat layout (``*_results.csv`` at any depth)
    when no new-layout files are present. The two layouts are never mixed.
    """
    new_pattern = os.path.join(log_dir, "**", "problem_runs", "*", "results_*.csv")
    new_files = glob.glob(new_pattern, recursive=True)
    if new_files:
        return new_files
    legacy_pattern = os.path.join(log_dir, "**", "*_results.csv")
    return glob.glob(legacy_pattern, recursive=True)


def load_results(target_path=None):
    # Determine which files to process
    if target_path:
        if os.path.isdir(target_path):
            # If user provided a directory, search recursively for results inside
            print(f"Searching for result files in directory: '{target_path}'")
            files = find_results_csvs(target_path)
        elif not os.path.exists(target_path) and not any(c in target_path for c in "*?[]"):
            print(f"Error: The path '{target_path}' does not exist.")
            sys.exit(1)
        else:
            # User provided a file pattern
            files = glob.glob(target_path, recursive=True)
            print(f"Searching for files matching: '{target_path}'")
    else:
        # Default behavior: find all CSV files recursively in current directory
        print("Searching for results CSVs recursively in current directory...")
        files = find_results_csvs(".")

    all_runs_raw = []

    print(f"Scanning {len(files)} CSV files for results...")

    for file_path in sorted(files):
        # Skip aggregate files and output files ONLY IF we are running in default mode.
        if not target_path and ("ALL_results" in file_path or "_output.csv" in file_path):
            continue

        try:
            with open(file_path, encoding="utf-8") as f:
                reader = csv.DictReader(f)
                if not reader.fieldnames:
                    continue

                has_results_columns = "Diagnosis.success" in reader.fieldnames
                has_mitigation = "Mitigation.success" in reader.fieldnames or "Mitigation.judgment" in reader.fieldnames
                autonomous = _is_autonomous_submit_experiment(file_path)

                for row in reader:
                    pid = row.get("problem_id")
                    if not pid:
                        continue

                    row["source_file"] = os.path.basename(file_path)
                    row["has_mitigation"] = has_mitigation
                    row["autonomous"] = autonomous

                    if has_results_columns:
                        if has_mitigation:
                            row["status"] = (
                                "Completed"
                                if (row.get("Diagnosis.success") and row.get("Mitigation.success"))
                                else "Incomplete"
                            )
                        else:
                            row["status"] = (
                                "Completed"
                                if (row.get("Diagnosis.success") is not None and row.get("Diagnosis.success") != "")
                                else "Incomplete"
                            )
                    else:
                        row["status"] = "Incomplete"

                    all_runs_raw.append(row)

        except Exception as e:
            print(f"Error reading {file_path}: {e}")

    # --- Filter: Keep only latest run per problem_id ---
    # In sequence mode (sequence_index present), treat each (problem_id, sequence_index) as distinct.
    has_sequence = any(r.get("sequence_index") for r in all_runs_raw)

    runs_by_id = {}
    for run in all_runs_raw:
        if has_sequence and run.get("sequence_index") is not None:
            key = f"{run['problem_id']}/{run.get('sequence_index', '')}"
        else:
            key = run["problem_id"]
        runs_by_id[key] = run

    sorted_runs = list(runs_by_id.values())
    # Sort by sequence_index (if present) then source_file for chronological order
    sorted_runs.sort(
        key=lambda x: (x.get("source_file", ""), int(x["sequence_index"]) if x.get("sequence_index") else 0)
    )

    return runs_by_id, sorted_runs


def load_stratus_tokens(log_dir):
    """Load token usage from Stratus *_stratus_output.csv files.
    Sums diagnosis + mitigation (all agent rows) per problem.
    Returns dict[problem_id, total_tokens] or empty dict if none found.
    """
    pattern = os.path.join(log_dir, "**", "*_stratus_output.csv")
    files = glob.glob(pattern, recursive=True)
    if not files:
        return {}

    tokens_by_pid = {}
    for file_path in files:
        # Extract problem_id: {MMDD_HHMM}_{problem_id}_stratus_output.csv
        basename = os.path.basename(file_path)
        if not basename.endswith("_stratus_output.csv"):
            continue
        rest = basename[: -len("_stratus_output.csv")]
        parts = rest.split("_", 2)  # MMDD, HHMM, problem_id (may contain underscores)
        if len(parts) < 3:
            continue
        problem_id = parts[2]

        try:
            with open(file_path, encoding="utf-8") as f:
                reader = csv.DictReader(f)
                total = 0
                for row in reader:
                    try:
                        total += int(row.get("total_tokens", 0) or 0)
                    except (ValueError, TypeError):
                        pass
                if total > 0:
                    tokens_by_pid[problem_id] = total
        except Exception as e:
            print(f"Warning: could not read {file_path}: {e}")
    return tokens_by_pid


def load_crucible_turns(log_dir):
    """Load per-problem turn counts from crucible_results_*.json files.

    Each JSON stores ``usage_metrics.total.turns`` (or, on older runs, just
    ``usage_metrics.total`` without the nested ``total`` wrapper). Returns
    dict[problem_id, turns] or empty dict if no crucible JSON files are found.
    """
    pattern = os.path.join(log_dir, "**", "crucible_results_*.json")
    files = glob.glob(pattern, recursive=True)
    if not files:
        return {}

    turns_by_pid = {}
    # Sort chronologically so later runs overwrite earlier ones for the same pid.
    for file_path in sorted(files):
        basename = os.path.basename(file_path)
        m = re.match(r"crucible_results_(.+)_\d{8}_\d{6}\.json", basename)
        if not m:
            continue
        problem_id = m.group(1)

        try:
            with open(file_path, encoding="utf-8") as f:
                data = json.load(f)
            um = data.get("usage_metrics", {})
            total_block = um.get("total", {}) if isinstance(um, dict) else {}
            turns = int(total_block.get("turns", 0) or 0)
            if turns > 0:
                turns_by_pid[problem_id] = turns
        except Exception as e:
            print(f"Warning: could not read {file_path}: {e}")

    return turns_by_pid


def load_crucible_tokens(log_dir):
    """Load per-problem total token usage from crucible_results_*.json files.

    Returns dict[problem_id, total_tokens] where total = input + output
    (``cached_input_tokens`` is a subset of ``input_tokens`` and is not double-counted).
    Empty dict if no crucible JSON files are found.
    """
    pattern = os.path.join(log_dir, "**", "crucible_results_*.json")
    files = glob.glob(pattern, recursive=True)
    if not files:
        return {}

    tokens_by_pid: dict[str, int] = {}
    for file_path in sorted(files):
        basename = os.path.basename(file_path)
        m = re.match(r"crucible_results_(.+)_\d{8}_\d{6}\.json", basename)
        if not m:
            continue
        problem_id = m.group(1)
        try:
            with open(file_path, encoding="utf-8") as f:
                data = json.load(f)
            total = (data.get("usage_metrics") or {}).get("total") or {}
            tokens = int(total.get("input_tokens", 0) or 0) + int(total.get("output_tokens", 0) or 0)
            if tokens > 0:
                tokens_by_pid[problem_id] = tokens
        except Exception as e:
            print(f"Warning: could not read {file_path}: {e}")
    return tokens_by_pid


def load_crucible_tokens_by_phase(log_dir):
    """Load per-problem diagnosis/mitigation token usage from crucible_results_*.json.

    Reads ``usage_metrics.diagnosis`` / ``usage_metrics.mitigation`` and sums
    ``input_tokens + output_tokens`` per phase. Returns a tuple
    ``(diagnosis_by_pid, mitigation_by_pid)``. Files that predate the phase
    split lack these fields and contribute 0 for that file.
    """
    pattern = os.path.join(log_dir, "**", "crucible_results_*.json")
    files = glob.glob(pattern, recursive=True)
    if not files:
        return {}, {}

    diag_by_pid: dict[str, int] = {}
    mitig_by_pid: dict[str, int] = {}
    for file_path in sorted(files):
        basename = os.path.basename(file_path)
        m = re.match(r"crucible_results_(.+)_\d{8}_\d{6}\.json", basename)
        if not m:
            continue
        problem_id = m.group(1)
        try:
            with open(file_path, encoding="utf-8") as f:
                data = json.load(f)
            um = data.get("usage_metrics") or {}
            for key, target in (("diagnosis", diag_by_pid), ("mitigation", mitig_by_pid)):
                block = um.get(key) or {}
                tokens = int(block.get("input_tokens", 0) or 0) + int(block.get("output_tokens", 0) or 0)
                if tokens > 0:
                    target[problem_id] = tokens
        except Exception as e:
            print(f"Warning: could not read {file_path}: {e}")
    return diag_by_pid, mitig_by_pid


def load_crucible_turns_by_phase(log_dir):
    """Load per-problem diagnosis/mitigation turn counts from crucible_results_*.json.

    Reads ``usage_metrics.diagnosis.turns`` and ``usage_metrics.mitigation.turns``
    (emitted by ``_build_usage_metrics`` in the orchestrator). Returns a tuple
    ``(diagnosis_by_pid, mitigation_by_pid)``. Files that predate the phase split
    lack these fields and contribute 0 turns for that file.
    """
    pattern = os.path.join(log_dir, "**", "crucible_results_*.json")
    files = glob.glob(pattern, recursive=True)
    if not files:
        return {}, {}

    diag_by_pid: dict[str, int] = {}
    mitig_by_pid: dict[str, int] = {}
    for file_path in sorted(files):
        basename = os.path.basename(file_path)
        m = re.match(r"crucible_results_(.+)_\d{8}_\d{6}\.json", basename)
        if not m:
            continue
        problem_id = m.group(1)

        try:
            with open(file_path, encoding="utf-8") as f:
                data = json.load(f)
            um = data.get("usage_metrics", {}) or {}
            diag = int((um.get("diagnosis") or {}).get("turns", 0) or 0)
            mitig = int((um.get("mitigation") or {}).get("turns", 0) or 0)
            if diag > 0:
                diag_by_pid[problem_id] = diag
            if mitig > 0:
                mitig_by_pid[problem_id] = mitig
        except Exception as e:
            print(f"Warning: could not read {file_path}: {e}")

    return diag_by_pid, mitig_by_pid


def load_gemini_tokens(log_dir):
    """Load token usage from Gemini gemini_cli_results_*.json files.
    Returns dict[problem_id, total_tokens] or empty dict if none found.
    """
    # Check log_dir/gemini_cli/ and log_dir/
    for subdir in ["gemini_cli", ""]:
        base = os.path.join(log_dir, subdir) if subdir else log_dir
        pattern = os.path.join(base, "gemini_cli_results_*.json")
        files = glob.glob(pattern)
        if not files:
            continue

        tokens_by_pid = {}
        for file_path in files:
            basename = os.path.basename(file_path)
            m = re.match(r"gemini_cli_results_(.+)_\d{8}_\d{6}\.json", basename)
            if not m:
                continue
            problem_id = m.group(1)

            try:
                with open(file_path, encoding="utf-8") as f:
                    data = json.load(f)
                um = data.get("usage_metrics", {})
                inp = int(um.get("input_tokens", 0) or 0)
                out = int(um.get("output_tokens", 0) or 0)
                total = inp + out
                if total > 0:
                    tokens_by_pid[problem_id] = total
            except Exception as e:
                print(f"Warning: could not read {file_path}: {e}")
        if tokens_by_pid:
            return tokens_by_pid
    return {}


def load_problem_type_mapping():
    """Load problem type definitions from YAML files.

    Returns dict mapping problem_id -> type_name.
    """
    if not HAS_YAML:
        return {}
    yaml_dir = os.path.join(
        os.path.dirname(__file__),
        "..",
        "sregym",
        "sregym",
        "conductor",
        "problem_types",
    )
    yaml_dir = os.path.normpath(yaml_dir)
    if not os.path.isdir(yaml_dir):
        return {}
    mapping = {}
    for yml_path in glob.glob(os.path.join(yaml_dir, "tasklist.*.yml")):
        basename = os.path.basename(yml_path)
        # Extract type name: tasklist.<type_name>.yml
        type_name = basename.replace("tasklist.", "").replace(".yml", "")
        try:
            with open(yml_path, encoding="utf-8") as f:
                data = yaml.safe_load(f)
            if data and "all" in data and "problems" in data["all"]:
                for problem_id in data["all"]["problems"]:
                    mapping[problem_id] = type_name
        except Exception:
            pass
    return mapping


def _compute_group_stats(runs):
    count = len(runs)
    diag_ok = sum(1 for r in runs if r.get("Diagnosis.success") == "True")
    with_mitig = [r for r in runs if r.get("has_mitigation")]
    mitig_ok = sum(1 for r in with_mitig if r.get("Mitigation.success") == "True")

    diag_pct = f"{100 * diag_ok / count:.0f}%" if count else "-"
    mitig_pct = f"{100 * mitig_ok / len(with_mitig):.0f}%" if with_mitig else "-"

    ttls = []
    ttms = []
    tres = []
    for r in runs:
        try:
            if r.get("TTL"):
                ttls.append(float(r["TTL"]))
        except ValueError:
            pass
        if r.get("has_mitigation") and r.get("TTM"):
            tm = _time_to_mitigation(r)
            tr = _time_to_resolution(r)
            if tm is not None:
                ttms.append(tm)
            if tr is not None:
                tres.append(tr)
    avg_ttl = f"{sum(ttls) / len(ttls):.1f}" if ttls else "-"
    avg_ttm = f"{sum(ttms) / len(ttms):.1f}" if ttms else "-"
    avg_tres = f"{sum(tres) / len(tres):.1f}" if tres else "-"

    return count, diag_pct, mitig_pct, avg_ttl, avg_ttm, avg_tres


def print_type_breakdown(completed_runs, type_mapping, table_width=130):
    """Print success rates grouped by problem type."""
    # Group runs by type
    groups = {}
    for r in completed_runs:
        pid = r.get("problem_id", "")
        type_name = type_mapping.get(pid, "other")
        groups.setdefault(type_name, []).append(r)

    # Sort alphabetically, "other" last
    sorted_types = sorted(t for t in groups if t != "other")
    if "other" in groups:
        sorted_types.append("other")

    # Header
    type_w = 20
    print("-" * table_width)
    header = (
        f"{'Type':<{type_w}} | {'Count':>5} | {'Diag %':>7} | {'Mitig %':>7} | "
        f"{'Avg Diag':>8} | {'Avg Mitig':>9} | {'Avg Res':>8}"
    )
    print(header)
    print("-" * table_width)

    for type_name in sorted_types:
        count, diag_pct, mitig_pct, avg_ttl, avg_ttm, avg_tres = _compute_group_stats(groups[type_name])
        print(
            f"{type_name:<{type_w}} | {count:>5} | {diag_pct:>7} | {mitig_pct:>7} | "
            f"{avg_ttl:>8} | {avg_ttm:>9} | {avg_tres:>8}"
        )

    # Aggregate row across all problems (ungrouped)
    print("-" * table_width)
    count, diag_pct, mitig_pct, avg_ttl, avg_ttm, avg_tres = _compute_group_stats(completed_runs)
    print(
        f"{'all':<{type_w}} | {count:>5} | {diag_pct:>7} | {mitig_pct:>7} | {avg_ttl:>8} | {avg_ttm:>9} | {avg_tres:>8}"
    )

    print("-" * table_width + "\n")


def summarize_results(target_path=None):
    _, all_runs = load_results(target_path)

    if not all_runs:
        print("No result data found in the CSV files.")
        return

    # --- Aggregation ---
    total_runs = len(all_runs)
    completed_runs = [r for r in all_runs if r["status"] == "Completed"]
    incomplete_count = total_runs - len(completed_runs)

    diag_success_count = sum(1 for r in completed_runs if r.get("Diagnosis.success") == "True")
    runs_with_mitigation = [r for r in completed_runs if r.get("has_mitigation")]
    mitig_success_count = sum(1 for r in runs_with_mitigation if r.get("Mitigation.success") == "True")

    ttls = []
    ttms = []
    t_resolutions = []
    for r in completed_runs:
        try:
            if r.get("TTL"):
                ttls.append(float(r["TTL"]))
        except ValueError:
            pass
        if r.get("has_mitigation") and r.get("TTM"):
            tm = _time_to_mitigation(r)
            tr = _time_to_resolution(r)
            if tm is not None:
                ttms.append(tm)
            if tr is not None:
                t_resolutions.append(tr)

    avg_ttl = sum(ttls) / len(ttls) if ttls else 0.0
    avg_ttm = sum(ttms) / len(ttms) if ttms else 0.0

    # --- Print Summary Table ---
    problem_id_width = 50
    table_width = 130
    print("\n" + "=" * table_width)
    print(f"{'SREGym Benchmark Run History':^{table_width}}")
    print("=" * table_width)
    print(f"Total Runs Detected: {total_runs}")
    print(f"  - Completed: {len(completed_runs)}")
    print(f"  - Incomplete: {incomplete_count}")
    print("-" * table_width)
    diag_pct = diag_success_count / len(completed_runs) * 100 if completed_runs else 0
    print(f"Diagnosis Success Rate (of completed): {diag_success_count}/{len(completed_runs)} ({diag_pct:.1f}%)")
    if runs_with_mitigation:
        mitig_pct = mitig_success_count / len(runs_with_mitigation) * 100
        print(
            f"Mitigation Success Rate (of runs with mitigation): "
            f"{mitig_success_count}/{len(runs_with_mitigation)} ({mitig_pct:.1f}%)"
        )
    else:
        print("Mitigation Success Rate: No runs with mitigation.")
    avg_tres = sum(t_resolutions) / len(t_resolutions) if t_resolutions else 0.0
    print(f"Average Diagnosis Time:   {avg_ttl:.2f}s")
    print(f"Average Mitigation Time:  {avg_ttm:.2f}s")
    print(f"Average Resolution Time:  {avg_tres:.2f}s")
    print("-" * table_width)

    def pad_emoji(s, width):
        """Pad so visual width aligns; emojis render as 2 cols in many terminals."""
        has_emoji = "✅" in s or "❌" in s or "⚠️" in s
        visual_len = len(s) + (1 if has_emoji else 0)
        return s + " " * max(0, width - visual_len)

    diag_width = 8  # "✅ PASS" / "❌ FAIL"
    mitig_width = 8
    status_width = 16  # "DONE" / "⚠️  INCOMPLETE"
    header = (
        f"{'Run Date':<14} | {'Problem ID':<{problem_id_width}} | "
        f"{'Diag':<{diag_width}} | {'Mitig':<{mitig_width}} | "
        f"{'Diag(s)':<8} | {'Mitig(s)':<8} | {'Res(s)':<8} | {'Status':<{status_width}}"
    )
    print(header)
    print("-" * table_width)

    for r in all_runs:
        fname = r.get("source_file", "")
        # Extract timestamp 'MMDD_HHMM'
        run_date = fname[:9]

        pid = r.get("problem_id", "Unknown")
        if len(pid) > problem_id_width - 1:
            pid = pid[: problem_id_width - 4] + "..."

        if r["status"] == "Completed":
            d_res = "✅ PASS" if r.get("Diagnosis.success") == "True" else "❌ FAIL"
            if r.get("has_mitigation"):
                m_res = "✅ PASS" if r.get("Mitigation.success") == "True" else "❌ FAIL"
            else:
                m_res = "-"
            try:
                ttl = f"{float(r.get('TTL', 0)):.1f}"
            except Exception:
                ttl = "N/A"
            if r.get("has_mitigation"):
                tm_val = _time_to_mitigation(r)
                ttm = f"{tm_val:.1f}" if tm_val is not None else "N/A"
                tr_val = _time_to_resolution(r)
                tres = f"{tr_val:.1f}" if tr_val is not None else "N/A"
            else:
                ttm = "-"
                tres = "-"
            status = "DONE"
        else:
            d_res = "-"
            m_res = "-"
            ttl = "-"
            ttm = "-"
            tres = "-"
            status = "⚠️  INCOMPLETE"

        d_str = pad_emoji(d_res, diag_width)
        m_str = pad_emoji(m_res, mitig_width)
        status_str = pad_emoji(status, status_width)
        print(
            f"{run_date:<14} | {pid:<{problem_id_width}} | {d_str} | {m_str} | "
            f"{ttl:<8} | {ttm:<8} | {tres:<8} | {status_str}"
        )

    print("=" * table_width + "\n")

    # --- Success Rates by Problem Type ---
    type_mapping = load_problem_type_mapping()
    if type_mapping and completed_runs:
        print(f"{'Success Rates by Problem Type':^{table_width}}")
        print_type_breakdown(completed_runs, type_mapping, table_width=table_width)

    # --- Plot CDFs ---
    valid_ttls = [t for t in ttls if t > 0]
    valid_ttms = [t for t in ttms if t > 0]

    if not valid_ttls and not valid_ttms:
        print("No valid TTL or TTM data for plotting.")
        return

    if not HAS_PLOTTING:
        print("Matplotlib/Numpy not found. Skipping plots.")
        return

    plt.figure(figsize=(10, 6))

    if valid_ttls:
        valid_ttls.sort()
        y_ttls = np.arange(1, len(valid_ttls) + 1) / len(valid_ttls)
        plt.plot(
            valid_ttls,
            y_ttls,
            marker=".",
            linestyle="-",
            color="tab:blue",
            label=f"Diagnosis Time (n={len(valid_ttls)})",
        )

    if valid_ttms:
        valid_ttms.sort()
        y_ttms = np.arange(1, len(valid_ttms) + 1) / len(valid_ttms)
        plt.plot(
            valid_ttms,
            y_ttms,
            marker=".",
            linestyle="-",
            color="tab:orange",
            label=f"Mitigation Time (n={len(valid_ttms)})",
        )

    plt.xlabel("Time (s)")
    plt.ylabel("CDF")
    plt.title("CDF of Time to Diagnosis and Mitigation")
    plt.grid(True)
    plt.legend()

    if target_path and os.path.isdir(target_path):
        plot_dir = os.path.join(target_path, "plots")
    else:
        plot_dir = "plots"
    os.makedirs(plot_dir, exist_ok=True)

    output_plot = os.path.join(plot_dir, "cdf_results.png")
    plt.savefig(output_plot)
    plt.close()
    print(f"CDF plot saved to {output_plot}")

    # --- Resolution Time CDF ---
    valid_tres = [t for t in t_resolutions if t > 0]
    if valid_tres:
        plt.figure(figsize=(10, 6))
        valid_tres.sort()
        y_tres = np.arange(1, len(valid_tres) + 1) / len(valid_tres)
        plt.plot(
            valid_tres,
            y_tres,
            marker=".",
            linestyle="-",
            color="tab:green",
            label=f"Resolution Time (n={len(valid_tres)})",
        )
        plt.xlabel("Time (s)")
        plt.ylabel("CDF")
        plt.title("CDF of Resolution Time")
        plt.grid(True)
        plt.legend()

        res_plot = os.path.join(plot_dir, "cdf_resolution.png")
        plt.savefig(res_plot)
        plt.close()
        print(f"Resolution CDF plot saved to {res_plot}")

    # --- Turns CDF (crucible agents only) ---
    search_root = target_path if (target_path and os.path.isdir(target_path)) else "."
    turns_by_pid = load_crucible_turns(search_root)
    valid_turns = sorted(t for t in turns_by_pid.values() if t and t > 0)
    if valid_turns:
        plt.figure(figsize=(10, 6))
        y_turns = np.arange(1, len(valid_turns) + 1) / len(valid_turns)
        plt.plot(
            valid_turns,
            y_turns,
            marker=".",
            linestyle="-",
            color="tab:purple",
            label=f"Turns (n={len(valid_turns)})",
        )
        plt.xlabel("Turns")
        plt.ylabel("CDF")
        plt.title("CDF of Number of Turns")
        plt.grid(True)
        plt.legend()

        turns_plot = os.path.join(plot_dir, "cdf_turns.png")
        plt.savefig(turns_plot)
        plt.close()
        print(f"Turns CDF plot saved to {turns_plot}")

    # --- Per-phase Turns CDFs ---
    diag_by_pid, mitig_by_pid = load_crucible_turns_by_phase(search_root)
    for name, data_map, color in (
        ("diagnosis", diag_by_pid, "tab:blue"),
        ("mitigation", mitig_by_pid, "tab:orange"),
    ):
        vals = sorted(t for t in data_map.values() if t and t > 0)
        if not vals:
            continue
        plt.figure(figsize=(10, 6))
        y_phase = np.arange(1, len(vals) + 1) / len(vals)
        plt.plot(
            vals,
            y_phase,
            marker=".",
            linestyle="-",
            color=color,
            label=f"{name.capitalize()} Turns (n={len(vals)})",
        )
        plt.xlabel("Turns")
        plt.ylabel("CDF")
        plt.title(f"CDF of {name.capitalize()}-Phase Turns")
        plt.grid(True)
        plt.legend()
        phase_plot = os.path.join(plot_dir, f"cdf_turns_{name}.png")
        plt.savefig(phase_plot)
        plt.close()
        print(f"{name.capitalize()} turns CDF plot saved to {phase_plot}")


def diff_results(dirs, names=None, limit_to_index=None):
    if len(dirs) < 2:
        print("Error: --diff requires at least 2 directories.")
        sys.exit(1)

    n_dirs = len(dirs)

    if limit_to_index is not None and not (0 <= limit_to_index < n_dirs):
        print(f"Error: --limit-to-index must be in [0, {n_dirs - 1}], got {limit_to_index}.")
        sys.exit(1)

    # Load results from each directory
    runs_maps = []
    for d in dirs:
        print(f"\n--- Loading results from {d} ---")
        run_map, _ = load_results(d)
        runs_maps.append(run_map)

    # Restrict every dataset to the pids present in runs_maps[limit_to_index].
    # All downstream plots/tables iterate over these filtered maps, so the
    # restriction flows through comparison tables, CDFs, and success-rate bars.
    if limit_to_index is not None:
        keep_pids = set(runs_maps[limit_to_index].keys())
        print(
            f"\n--limit-to-index={limit_to_index} ({dirs[limit_to_index]}): "
            f"restricting all datasets to {len(keep_pids)} problem(s)."
        )
        runs_maps = [{pid: v for pid, v in rm.items() if pid in keep_pids} for rm in runs_maps]
    else:
        keep_pids = None

    # Derive names and output directory
    dir_basenames = [os.path.basename(os.path.normpath(d)) for d in dirs]
    if names is None:
        names = dir_basenames
    common_parent = os.path.commonpath([os.path.abspath(d) for d in dirs])
    output_basename = "--".join(dir_basenames)
    if keep_pids is not None:
        output_basename += f"__limit-to-{limit_to_index}"
    output_dir = os.path.join(common_parent, "diff", output_basename)
    os.makedirs(output_dir, exist_ok=True)
    print(f"\nDiff results will be stored in: {output_dir}")

    # --- Statistics Summary ---
    def get_stats(run_map):
        runs = list(run_map.values())
        total = len(runs)
        completed = [r for r in runs if r["status"] == "Completed"]
        n_comp = len(completed)
        d_succ = sum(1 for r in completed if r.get("Diagnosis.success") == "True")
        runs_with_mitig = [r for r in completed if r.get("has_mitigation")]
        m_succ = sum(1 for r in runs_with_mitig if r.get("Mitigation.success") == "True")
        n_mitig = len(runs_with_mitig)
        ttls, ttms, tres = [], [], []
        for r in completed:
            try:
                if r.get("TTL"):
                    ttls.append(float(r["TTL"]))
            except (ValueError, TypeError):
                pass
            if r.get("has_mitigation") and r.get("TTM"):
                tm = _time_to_mitigation(r)
                if tm is not None:
                    ttms.append(tm)
                tr = _time_to_resolution(r)
                if tr is not None and r.get("TTL"):
                    tres.append(tr)
        avg_ttl = sum(ttls) / len(ttls) if ttls else 0.0
        avg_ttm = sum(ttms) / len(ttms) if ttms else 0.0
        avg_tres = sum(tres) / len(tres) if tres else 0.0
        return total, n_comp, d_succ, m_succ, n_mitig, avg_ttl, avg_ttm, avg_tres

    all_stats = [get_stats(rm) for rm in runs_maps]

    w_col = max(max(len(n) for n in names), 18)

    def fmt_row(label, values, diff_str=None):
        """Format a stats table row with N value columns and optional diff."""
        parts = [f"{label:<25}"] + [f"{v:<{w_col}}" for v in values]
        if n_dirs == 2 and diff_str is not None:
            parts.append(f"{diff_str:<10}")
        return " | ".join(parts)

    header = fmt_row("Statistic", names, "Diff" if n_dirs == 2 else None)
    sep_width = len(header)

    print("\n" + "=" * sep_width)
    print(header)
    print("-" * sep_width)

    totals = [s[0] for s in all_stats]
    completeds = [s[1] for s in all_stats]
    print(fmt_row("Total Runs", totals, f"{totals[1] - totals[0]:+d}" if n_dirs == 2 else None))
    print(fmt_row("Completed Runs", completeds, f"{completeds[1] - completeds[0]:+d}" if n_dirs == 2 else None))

    d_pcts = [(s[2] / s[1] * 100) if s[1] else 0.0 for s in all_stats]
    d_strs = [f"{s[2]}/{s[1]} ({p:.1f}%)" for s, p in zip(all_stats, d_pcts, strict=True)]
    print(fmt_row("Diagnosis Success", d_strs, f"{d_pcts[1] - d_pcts[0]:+.1f}%" if n_dirs == 2 else None))

    m_pcts = [(s[3] / s[4] * 100) if s[4] else 0.0 for s in all_stats]
    m_strs = [f"{s[3]}/{s[4]} ({p:.1f}%)" if s[4] else "N/A" for s, p in zip(all_stats, m_pcts, strict=True)]
    if n_dirs == 2:
        m_diff = f"{m_pcts[1] - m_pcts[0]:+.1f}%" if (all_stats[0][4] and all_stats[1][4]) else "-"
    else:
        m_diff = None
    print(fmt_row("Mitigation Success (of mitig)", m_strs, m_diff))

    attls = [s[5] for s in all_stats]
    print(fmt_row("Avg TTL", [f"{v:.1f}s" for v in attls], f"{attls[1] - attls[0]:+.1f}s" if n_dirs == 2 else None))

    attms = [s[6] for s in all_stats]
    print(fmt_row("Avg TTM", [f"{v:.1f}s" for v in attms], f"{attms[1] - attms[0]:+.1f}s" if n_dirs == 2 else None))

    atres = [s[7] for s in all_stats]
    print(
        fmt_row(
            "Avg Resolution (TTM)",
            [f"{v:.1f}s" for v in atres],
            f"{atres[1] - atres[0]:+.1f}s" if n_dirs == 2 else None,
        )
    )

    print("=" * sep_width + "\n")

    # --- Per-Problem Comparison Table ---
    all_pids = sorted(set().union(*(rm.keys() for rm in runs_maps)))

    # Dynamic PID width based on data
    max_pid_len = max([len(p) for p in all_pids] + [len("Problem ID")])
    pid_width = max_pid_len

    # Dynamic column widths based on name length, minimum 10
    truncated_names = [n if len(n) <= 20 else n[:17] + "..." for n in names]
    w_d = [max(10, len(tn) + 2) for tn in truncated_names]
    w_m = [max(10, len(tn) + 2) for tn in truncated_names]
    diff_width = 12

    # Build header
    header_parts = [f"{'Problem ID':<{pid_width}}"]
    for i, tn in enumerate(truncated_names):
        header_parts.append(f"{'D:' + tn:<{w_d[i]}}")
    if n_dirs == 2:
        header_parts.append(f"{'Diff(TTL)':<{diff_width}}")
    for i, tn in enumerate(truncated_names):
        header_parts.append(f"{'M:' + tn:<{w_m[i]}}")
    if n_dirs == 2:
        header_parts.append(f"{'Diff(TTM)':<{diff_width}}")
    header = " | ".join(header_parts)
    sep = "-" * len(header)
    summary_lines = []
    summary_lines.append(header)
    summary_lines.append(sep)

    # Per-dir CDF data
    ttd_per_dir = [[] for _ in range(n_dirs)]
    ttm_per_dir = [[] for _ in range(n_dirs)]
    ttd_success_per_dir = [[] for _ in range(n_dirs)]
    ttm_success_per_dir = [[] for _ in range(n_dirs)]
    res_per_dir = [[] for _ in range(n_dirs)]
    res_success_per_dir = [[] for _ in range(n_dirs)]

    # 2-dir only: per-problem comparison data for scatter plots
    if n_dirs == 2:
        comp_diag_data, comp_mitig_data = [], []
        comp_diag_success_data, comp_mitig_success_data = [], []
        comp_diag_fail_data, comp_mitig_fail_data = [], []
        comp_res_data, comp_res_success_data, comp_res_fail_data = [], [], []

    def pad_emoji(s, width):
        # In many terminals, emoji is 2 chars wide. Python len() counts it as 1.
        has_emoji = "✅" in s or "❌" in s
        visual_len = len(s) + (1 if has_emoji else 0)
        padding = max(0, width - visual_len)
        return s + " " * padding

    def get_data(r):
        if not r:
            return "MISSING", "MISSING", None, None

        def parse_float(val):
            try:
                return float(val)
            except (ValueError, TypeError):
                return None

        t_d = parse_float(r.get("TTL"))
        t_m = _time_to_mitigation(r)

        if r["status"] == "Completed":
            d_stat = "✅ PASS" if r.get("Diagnosis.success") == "True" else "❌ FAIL"
            if r.get("has_mitigation"):
                m_stat = "✅ PASS" if r.get("Mitigation.success") == "True" else "❌ FAIL"
            else:
                m_stat = "-"
        else:
            d_stat = "-"
            m_stat = "-"

        return d_stat, m_stat, t_d, t_m

    for pid in all_pids:
        runs = [rm.get(pid) for rm in runs_maps]
        data = [get_data(r) for r in runs]
        # Collect CDF data per dir
        for i, (r, (_, _, td, tm)) in enumerate(zip(runs, data, strict=True)):
            if r and td is not None and td > 0:
                ttd_per_dir[i].append(td)
                if r.get("Diagnosis.success") == "True":
                    ttd_success_per_dir[i].append(td)

            if r and tm is not None and tm > 0:
                ttm_per_dir[i].append(tm)
                if r.get("Mitigation.success") == "True":
                    ttm_success_per_dir[i].append(tm)

            tres = _time_to_resolution(r) if r else None
            if tres is not None and tres > 0:
                res_per_dir[i].append(tres)
                if r.get("Mitigation.success") == "True":
                    res_success_per_dir[i].append(tres)

        # 2-dir specific comparison data collection
        if n_dirs == 2:
            r1, r2 = runs[0], runs[1]
            td1, tm1 = data[0][2], data[0][3]
            td2, tm2 = data[1][2], data[1][3]
            diag_s1 = r1 and r1.get("Diagnosis.success") == "True"
            diag_s2 = r2 and r2.get("Diagnosis.success") == "True"
            mitig_s1 = r1 and r1.get("Mitigation.success") == "True"
            mitig_s2 = r2 and r2.get("Mitigation.success") == "True"

            if (td1 is not None and td1 > 0) or (td2 is not None and td2 > 0):
                comp_diag_data.append((pid, td1, td2, diag_s1, diag_s2))

            td1_succ = td1 if diag_s1 else None
            td2_succ = td2 if diag_s2 else None
            if (td1_succ is not None and td1_succ > 0) and (td2_succ is not None and td2_succ > 0):
                comp_diag_success_data.append((pid, td1_succ, td2_succ, True, True))

            td1_fail = td1 if (r1 and not diag_s1) else None
            td2_fail = td2 if (r2 and not diag_s2) else None
            if (td1_fail is not None and td1_fail > 0) and (td2_fail is not None and td2_fail > 0):
                comp_diag_fail_data.append((pid, td1_fail, td2_fail, False, False))

            if (tm1 is not None and tm1 > 0) or (tm2 is not None and tm2 > 0):
                comp_mitig_data.append((pid, tm1, tm2, mitig_s1, mitig_s2))

            tm1_succ = tm1 if mitig_s1 else None
            tm2_succ = tm2 if mitig_s2 else None
            if (tm1_succ is not None and tm1_succ > 0) and (tm2_succ is not None and tm2_succ > 0):
                comp_mitig_success_data.append((pid, tm1_succ, tm2_succ, True, True))

            tm1_fail = tm1 if (r1 and not mitig_s1) else None
            tm2_fail = tm2 if (r2 and not mitig_s2) else None
            if (tm1_fail is not None and tm1_fail > 0) and (tm2_fail is not None and tm2_fail > 0):
                comp_mitig_fail_data.append((pid, tm1_fail, tm2_fail, False, False))

            tres1 = _time_to_resolution(r1) if r1 else None
            tres2 = _time_to_resolution(r2) if r2 else None
            if tres1 is not None and tres1 <= 0:
                tres1 = None
            if tres2 is not None and tres2 <= 0:
                tres2 = None

            if tres1 is not None or tres2 is not None:
                comp_res_data.append((pid, tres1, tres2, mitig_s1, mitig_s2))

            tres1_succ = tres1 if mitig_s1 else None
            tres2_succ = tres2 if mitig_s2 else None
            if (tres1_succ is not None and tres1_succ > 0) and (tres2_succ is not None and tres2_succ > 0):
                comp_res_success_data.append((pid, tres1_succ, tres2_succ, True, True))

            tres1_fail = tres1 if (r1 and not mitig_s1) else None
            tres2_fail = tres2 if (r2 and not mitig_s2) else None
            if (tres1_fail is not None and tres1_fail > 0) and (tres2_fail is not None and tres2_fail > 0):
                comp_res_fail_data.append((pid, tres1_fail, tres2_fail, False, False))

        # Build table line
        line_parts = [f"{pid:<{pid_width}}"]
        line_parts.extend(pad_emoji(data[i][0], w_d[i]) for i in range(n_dirs))
        if n_dirs == 2:
            td1_v, td2_v = data[0][2], data[1][2]
            diff_td = f"{td2_v - td1_v:+.1f}s" if (td1_v is not None and td2_v is not None) else "-"
            line_parts.append(f"{diff_td:<{diff_width}}")
        line_parts.extend(pad_emoji(data[i][1], w_m[i]) for i in range(n_dirs))
        if n_dirs == 2:
            tm1_v, tm2_v = data[0][3], data[1][3]
            diff_tm = f"{tm2_v - tm1_v:+.1f}s" if (tm1_v is not None and tm2_v is not None) else "-"
            line_parts.append(f"{diff_tm:<{diff_width}}")
        summary_lines.append(" | ".join(line_parts))

    # Write summary
    summary_path = os.path.join(output_dir, "summary.txt")
    with open(summary_path, "w", encoding="utf-8") as f:
        f.write("\n".join(summary_lines))

    print("\n".join(summary_lines))
    print(f"\nSummary saved to {summary_path}")

    # --- CDF Plots (generalized to N dirs) ---
    if n_dirs == 2:
        diag_colors = ["#004d99", "#66b3ff"]
        mitig_colors = ["#cc5200", "#ff9933"]
        res_colors = ["#7b2d8b", "#c792ea"]
    else:
        diag_colors = mitig_colors = res_colors = [f"C{i}" for i in range(n_dirs)]

    plot_cdfs(
        ttd_per_dir,
        names,
        "Time to Diagnosis (TTL)",
        os.path.join(output_dir, "cdf_diagnosis.png"),
        colors=diag_colors,
    )
    plot_cdfs(
        ttm_per_dir,
        names,
        "Time to Mitigation (TTM)",
        os.path.join(output_dir, "cdf_mitigation.png"),
        colors=mitig_colors,
    )
    plot_cdfs(
        ttd_success_per_dir,
        names,
        "Time to Diagnosis (Success Only)",
        os.path.join(output_dir, "cdf_diagnosis_success.png"),
        colors=diag_colors,
    )
    plot_cdfs(
        ttm_success_per_dir,
        names,
        "Time to Mitigation (Success Only)",
        os.path.join(output_dir, "cdf_mitigation_success.png"),
        colors=mitig_colors,
    )
    plot_cdfs(
        res_per_dir,
        names,
        "Time to Resolution (TTM)",
        os.path.join(output_dir, "cdf_resolution.png"),
        colors=res_colors,
    )
    plot_cdfs(
        res_success_per_dir,
        names,
        "Time to Resolution (Success Only)",
        os.path.join(output_dir, "cdf_resolution_success.png"),
        colors=res_colors,
    )

    # --- 2-dir-only plots (scatter, comparison, success rates) ---
    if n_dirs == 2:
        name1, name2 = names[0], names[1]

        plot_comparison_by_problem(
            comp_diag_data,
            name1,
            name2,
            "Diagnosis Time",
            os.path.join(output_dir, "comparison_diagnosis.png"),
            colors=["#004d99", "#66b3ff"],
            use_status_colors=True,
        )
        plot_comparison_by_problem(
            comp_diag_data,
            name1,
            name2,
            "Diagnosis Time",
            os.path.join(output_dir, "comparison_diagnosis_compact.png"),
            colors=["#004d99", "#66b3ff"],
            compact=True,
            use_status_colors=True,
        )
        plot_comparison_by_problem(
            comp_mitig_data,
            name1,
            name2,
            "Mitigation Time",
            os.path.join(output_dir, "comparison_mitigation.png"),
            colors=["#cc5200", "#ff9933"],
            use_status_colors=True,
        )
        plot_comparison_by_problem(
            comp_mitig_data,
            name1,
            name2,
            "Mitigation Time",
            os.path.join(output_dir, "comparison_mitigation_compact.png"),
            colors=["#cc5200", "#ff9933"],
            compact=True,
            use_status_colors=True,
        )
        plot_comparison_by_problem(
            comp_diag_success_data,
            name1,
            name2,
            "Diagnosis Time (Success Only)",
            os.path.join(output_dir, "comparison_diagnosis_success.png"),
            colors=["#004d99", "#66b3ff"],
        )
        plot_comparison_by_problem(
            comp_diag_success_data,
            name1,
            name2,
            "Diagnosis Time (Success Only)",
            os.path.join(output_dir, "comparison_diagnosis_success_compact.png"),
            colors=["#004d99", "#66b3ff"],
            compact=True,
        )
        plot_comparison_by_problem(
            comp_mitig_success_data,
            name1,
            name2,
            "Mitigation Time (Success Only)",
            os.path.join(output_dir, "comparison_mitigation_success.png"),
            colors=["#cc5200", "#ff9933"],
        )
        plot_comparison_by_problem(
            comp_mitig_success_data,
            name1,
            name2,
            "Mitigation Time (Success Only)",
            os.path.join(output_dir, "comparison_mitigation_success_compact.png"),
            colors=["#cc5200", "#ff9933"],
            compact=True,
        )
        plot_comparison_by_problem(
            comp_diag_fail_data,
            name1,
            name2,
            "Diagnosis Time (Failure Only)",
            os.path.join(output_dir, "comparison_diagnosis_failure.png"),
            colors=["#004d99", "#66b3ff"],
        )
        plot_comparison_by_problem(
            comp_diag_fail_data,
            name1,
            name2,
            "Diagnosis Time (Failure Only)",
            os.path.join(output_dir, "comparison_diagnosis_failure_compact.png"),
            colors=["#004d99", "#66b3ff"],
            compact=True,
        )
        plot_comparison_by_problem(
            comp_mitig_fail_data,
            name1,
            name2,
            "Mitigation Time (Failure Only)",
            os.path.join(output_dir, "comparison_mitigation_failure.png"),
            colors=["#cc5200", "#ff9933"],
        )
        plot_comparison_by_problem(
            comp_mitig_fail_data,
            name1,
            name2,
            "Mitigation Time (Failure Only)",
            os.path.join(output_dir, "comparison_mitigation_failure_compact.png"),
            colors=["#cc5200", "#ff9933"],
            compact=True,
        )

        # --- By Name Variations ---
        plot_comparison_by_problem(
            comp_diag_data,
            name1,
            name2,
            "Diagnosis Time (By Name)",
            os.path.join(output_dir, "comparison_diagnosis_by_name.png"),
            colors=["#004d99", "#66b3ff"],
            use_status_colors=True,
            sort_by_name=True,
        )
        plot_comparison_by_problem(
            comp_mitig_data,
            name1,
            name2,
            "Mitigation Time (By Name)",
            os.path.join(output_dir, "comparison_mitigation_by_name.png"),
            colors=["#cc5200", "#ff9933"],
            use_status_colors=True,
            sort_by_name=True,
        )
        plot_comparison_by_problem(
            comp_diag_success_data,
            name1,
            name2,
            "Diagnosis Time (Success Only, By Name)",
            os.path.join(output_dir, "comparison_diagnosis_success_by_name.png"),
            colors=["#004d99", "#66b3ff"],
            sort_by_name=True,
        )
        plot_comparison_by_problem(
            comp_mitig_success_data,
            name1,
            name2,
            "Mitigation Time (Success Only, By Name)",
            os.path.join(output_dir, "comparison_mitigation_success_by_name.png"),
            colors=["#cc5200", "#ff9933"],
            sort_by_name=True,
        )
        plot_comparison_by_problem(
            comp_diag_fail_data,
            name1,
            name2,
            "Diagnosis Time (Failure Only, By Name)",
            os.path.join(output_dir, "comparison_diagnosis_failure_by_name.png"),
            colors=["#004d99", "#66b3ff"],
            sort_by_name=True,
        )
        plot_comparison_by_problem(
            comp_mitig_fail_data,
            name1,
            name2,
            "Mitigation Time (Failure Only, By Name)",
            os.path.join(output_dir, "comparison_mitigation_failure_by_name.png"),
            colors=["#cc5200", "#ff9933"],
            sort_by_name=True,
        )

        # --- Resolution (TTL + TTM) plots ---
        plot_comparison_by_problem(
            comp_res_data,
            name1,
            name2,
            "Resolution Time",
            os.path.join(output_dir, "comparison_resolution.png"),
            colors=["#7b2d8b", "#c792ea"],
            use_status_colors=True,
        )
        plot_comparison_by_problem(
            comp_res_data,
            name1,
            name2,
            "Resolution Time",
            os.path.join(output_dir, "comparison_resolution_compact.png"),
            colors=["#7b2d8b", "#c792ea"],
            compact=True,
            use_status_colors=True,
        )
        plot_comparison_by_problem(
            comp_res_data,
            name1,
            name2,
            "Resolution Time (By Name)",
            os.path.join(output_dir, "comparison_resolution_by_name.png"),
            colors=["#7b2d8b", "#c792ea"],
            use_status_colors=True,
            sort_by_name=True,
        )
        plot_comparison_by_problem(
            comp_res_success_data,
            name1,
            name2,
            "Resolution Time (Success Only)",
            os.path.join(output_dir, "comparison_resolution_success.png"),
            colors=["#7b2d8b", "#c792ea"],
        )
        plot_comparison_by_problem(
            comp_res_success_data,
            name1,
            name2,
            "Resolution Time (Success Only, By Name)",
            os.path.join(output_dir, "comparison_resolution_success_by_name.png"),
            colors=["#7b2d8b", "#c792ea"],
            sort_by_name=True,
        )
        plot_comparison_by_problem(
            comp_res_fail_data,
            name1,
            name2,
            "Resolution Time (Failure Only)",
            os.path.join(output_dir, "comparison_resolution_failure.png"),
            colors=["#7b2d8b", "#c792ea"],
        )
        plot_comparison_by_problem(
            comp_res_fail_data,
            name1,
            name2,
            "Resolution Time (Failure Only, By Name)",
            os.path.join(output_dir, "comparison_resolution_failure_by_name.png"),
            colors=["#7b2d8b", "#c792ea"],
            sort_by_name=True,
        )

        # --- Success rate bar chart ---
        plot_success_rates(
            all_stats[0][2],
            all_stats[0][3],
            all_stats[0][1],
            name1,
            all_stats[1][2],
            all_stats[1][3],
            all_stats[1][1],
            name2,
            os.path.join(output_dir, "success_rates_comparison.png"),
            colors=["#1f77b4", "#ff7f0e"],
        )

    # --- Token-based plots ---
    # Fall back through the known sources: Stratus CSV, Gemini CSV, crucible JSONs.
    tokens_maps = [load_stratus_tokens(d) or load_gemini_tokens(d) or load_crucible_tokens(d) for d in dirs]
    if keep_pids is not None:
        tokens_maps = [{pid: v for pid, v in tm.items() if pid in keep_pids} for tm in tokens_maps]
    tokens_lists = [[t for t in tm.values() if t and t > 0] for tm in tokens_maps]

    if any(tokens_lists):
        plot_cdf_tokens(
            tokens_lists,
            names,
            os.path.join(output_dir, "cdf_tokens.png"),
            colors=diag_colors,
        )

    # Per-phase token CDFs (requires usage_metrics.diagnosis / .mitigation in each JSON).
    token_phase_maps = [load_crucible_tokens_by_phase(d) for d in dirs]
    if keep_pids is not None:
        token_phase_maps = [
            (
                {pid: v for pid, v in diag.items() if pid in keep_pids},
                {pid: v for pid, v in mitig.items() if pid in keep_pids},
            )
            for diag, mitig in token_phase_maps
        ]
    diag_token_lists = [[t for t in m[0].values() if t > 0] for m in token_phase_maps]
    mitig_token_lists = [[t for t in m[1].values() if t > 0] for m in token_phase_maps]

    if any(diag_token_lists):
        plot_cdf_tokens(
            diag_token_lists,
            names,
            os.path.join(output_dir, "cdf_tokens_diagnosis.png"),
            colors=diag_colors,
            phase_label="Diagnosis",
        )
    if any(mitig_token_lists):
        plot_cdf_tokens(
            mitig_token_lists,
            names,
            os.path.join(output_dir, "cdf_tokens_mitigation.png"),
            colors=diag_colors,
            phase_label="Mitigation",
        )

    # --- Turn-count plots (crucible agents only) ---
    turns_maps = [load_crucible_turns(d) for d in dirs]
    if keep_pids is not None:
        turns_maps = [{pid: v for pid, v in tm.items() if pid in keep_pids} for tm in turns_maps]
    turns_lists = [[t for t in tm.values() if t and t > 0] for tm in turns_maps]

    if any(turns_lists):
        plot_cdf_turns(
            turns_lists,
            names,
            os.path.join(output_dir, "cdf_turns.png"),
            colors=diag_colors,
        )

    # Per-phase turn CDFs (requires usage_metrics.diagnosis / .mitigation in each JSON).
    phase_maps = [load_crucible_turns_by_phase(d) for d in dirs]
    if keep_pids is not None:
        phase_maps = [
            (
                {pid: v for pid, v in diag.items() if pid in keep_pids},
                {pid: v for pid, v in mitig.items() if pid in keep_pids},
            )
            for diag, mitig in phase_maps
        ]
    diag_lists = [[t for t in m[0].values() if t > 0] for m in phase_maps]
    mitig_lists = [[t for t in m[1].values() if t > 0] for m in phase_maps]

    if any(diag_lists):
        plot_cdf_turns(
            diag_lists,
            names,
            os.path.join(output_dir, "cdf_turns_diagnosis.png"),
            colors=diag_colors,
            title="CDF of Diagnosis-Phase Turns",
        )
    if any(mitig_lists):
        plot_cdf_turns(
            mitig_lists,
            names,
            os.path.join(output_dir, "cdf_turns_mitigation.png"),
            colors=diag_colors,
            title="CDF of Mitigation-Phase Turns",
        )

    if n_dirs == 2 and (turns_maps[0] or turns_maps[1]):
        name1, name2 = names[0], names[1]
        turns1_map, turns2_map = turns_maps[0], turns_maps[1]

        comp_turns_data = []
        for pid in all_pids:
            t1 = turns1_map.get(pid)
            t2 = turns2_map.get(pid)
            if (t1 is not None and t1 > 0) or (t2 is not None and t2 > 0):
                r1 = runs_maps[0].get(pid)
                r2 = runs_maps[1].get(pid)
                ds1 = r1 and r1.get("Diagnosis.success") == "True"
                ms1 = r1 and r1.get("Mitigation.success") == "True"
                ds2 = r2 and r2.get("Diagnosis.success") == "True"
                ms2 = r2 and r2.get("Mitigation.success") == "True"
                comp_turns_data.append((pid, t1 or 0, t2 or 0, ds1 and ms1, ds2 and ms2))
        plot_turns_comparison_by_problem(
            comp_turns_data,
            name1,
            name2,
            os.path.join(output_dir, "comparison_turns.png"),
            colors=["#004d99", "#66b3ff"],
            use_status_colors=True,
        )
        plot_turns_comparison_by_problem(
            comp_turns_data,
            name1,
            name2,
            os.path.join(output_dir, "comparison_turns_by_name.png"),
            colors=["#004d99", "#66b3ff"],
            use_status_colors=True,
            sort_by_name=True,
        )

    if n_dirs == 2 and (tokens_maps[0] or tokens_maps[1]):
        name1, name2 = names[0], names[1]
        tokens1_map, tokens2_map = tokens_maps[0], tokens_maps[1]

        comp_token_data = []
        for pid in all_pids:
            t1 = tokens1_map.get(pid)
            t2 = tokens2_map.get(pid)
            if (t1 is not None and t1 > 0) or (t2 is not None and t2 > 0):
                r1 = runs_maps[0].get(pid)
                r2 = runs_maps[1].get(pid)
                ds1 = r1 and r1.get("Diagnosis.success") == "True"
                ms1 = r1 and r1.get("Mitigation.success") == "True"
                ds2 = r2 and r2.get("Diagnosis.success") == "True"
                ms2 = r2 and r2.get("Mitigation.success") == "True"
                comp_token_data.append((pid, t1 or 0, t2 or 0, ds1 and ms1, ds2 and ms2))
        plot_token_comparison_by_problem(
            comp_token_data,
            name1,
            name2,
            os.path.join(output_dir, "comparison_tokens.png"),
            colors=["#004d99", "#66b3ff"],
            use_status_colors=True,
        )
        plot_token_comparison_by_problem(
            comp_token_data,
            name1,
            name2,
            os.path.join(output_dir, "comparison_tokens_by_name.png"),
            colors=["#004d99", "#66b3ff"],
            use_status_colors=True,
            sort_by_name=True,
        )

        tokens_time_data1 = []
        tokens_time_data2 = []
        for pid in all_pids:
            tok1 = tokens1_map.get(pid)
            tok2 = tokens2_map.get(pid)
            r1 = runs_maps[0].get(pid)
            r2 = runs_maps[1].get(pid)

            def parse_float(val):
                try:
                    return float(val)
                except (ValueError, TypeError):
                    return None

            ttl1 = parse_float(r1.get("TTL")) if r1 else None
            ttm1 = parse_float(r1.get("TTM")) if r1 else None
            ttl2 = parse_float(r2.get("TTL")) if r2 else None
            ttm2 = parse_float(r2.get("TTM")) if r2 else None
            agg1 = (ttl1 or 0) + (ttm1 or 0)
            agg2 = (ttl2 or 0) + (ttm2 or 0)
            if tok1 and tok1 > 0 and agg1 > 0:
                tokens_time_data1.append((tok1, agg1, pid))
            if tok2 and tok2 > 0 and agg2 > 0:
                tokens_time_data2.append((tok2, agg2, pid))

        plot_tokens_vs_time(
            tokens_time_data1,
            tokens_time_data2,
            name1,
            name2,
            os.path.join(output_dir, "scatter_tokens_vs_time.png"),
            colors=["#004d99", "#66b3ff"],
        )


def plot_tokens_vs_time(data1, data2, label1, label2, output_path, colors=None):
    """Scatter plot: X=tokens (diag+mitigation), Y=aggregate time (TTL+TTM)."""
    if not HAS_PLOTTING:
        print(f"Matplotlib/Numpy not found. Skipping plot: {output_path}")
        return

    if not data1 and not data2:
        print("No tokens+time data for scatter plot.")
        return

    if colors is None:
        colors = ["tab:blue", "tab:orange"]

    plt.figure(figsize=(10, 6))
    has_data = False

    if data1:
        x1 = [d[0] / 1e6 for d in data1]
        y1 = [d[1] for d in data1]
        plt.scatter(x1, y1, color=colors[0], label=f"{label1} (n={len(data1)})", marker="o", alpha=0.7)
        has_data = True

    if data2:
        x2 = [d[0] / 1e6 for d in data2]
        y2 = [d[1] for d in data2]
        plt.scatter(x2, y2, color=colors[1], label=f"{label2} (n={len(data2)})", marker="x", alpha=0.7)
        has_data = True

    if has_data:
        plt.xlabel("Tokens (M)")
        plt.ylabel("Time (s) — TTL + TTM")
        plt.title("Tokens vs Aggregate Time (Diagnosis + Mitigation)")
        plt.grid(True)
        plt.legend()
        plt.tight_layout()
        plt.savefig(output_path)
        plt.close()
        print(f"Tokens vs time scatter plot saved to {output_path}")


def plot_cdf_tokens(data_list, labels, output_path, colors=None, phase_label=None):
    """Plot CDF of token usage for N agents."""
    if not HAS_PLOTTING:
        print(f"Matplotlib/Numpy not found. Skipping plot: {output_path}")
        return

    if not any(data_list):
        print("No token data for CDF plot.")
        return

    _markers = [".", "x", "^", "s", "D", "v", "<", ">"]
    _linestyles = ["-", "--", "-.", ":"]

    if colors is None:
        colors = [f"C{i}" for i in range(len(data_list))]

    phase_str = phase_label or "Diagnosis + Mitigation"

    plt.figure(figsize=(10, 6))
    has_data = False

    for i, (data, label) in enumerate(zip(data_list, labels, strict=True)):
        data = sorted(data)
        if data:
            y = np.arange(1, len(data) + 1) / len(data)
            plt.plot(
                [t / 1e6 for t in data],
                y,
                marker=_markers[i % len(_markers)],
                linestyle=_linestyles[i % len(_linestyles)],
                color=colors[i % len(colors)],
                label=f"{label} (n={len(data)})",
            )
            has_data = True

    if has_data:
        plt.xlabel("Tokens (M)")
        plt.ylabel("CDF")
        plt.title(f"CDF of Token Usage — {phase_str}")
        plt.grid(True)
        plt.legend()
        plt.savefig(output_path)
        plt.close()
        print(f"Token CDF plot saved to {output_path}")
    else:
        plt.close()


def plot_cdf_turns(data_list, labels, output_path, colors=None, title="CDF of Number of Turns"):
    """Plot CDF of turn counts for N agents."""
    if not HAS_PLOTTING:
        print(f"Matplotlib/Numpy not found. Skipping plot: {output_path}")
        return

    if not any(data_list):
        print("No turn-count data for CDF plot.")
        return

    _markers = [".", "x", "^", "s", "D", "v", "<", ">"]
    _linestyles = ["-", "--", "-.", ":"]

    if colors is None:
        colors = [f"C{i}" for i in range(len(data_list))]

    plt.figure(figsize=(10, 6))
    has_data = False

    for i, (data, label) in enumerate(zip(data_list, labels, strict=True)):
        data = sorted(data)
        if data:
            y = np.arange(1, len(data) + 1) / len(data)
            plt.plot(
                data,
                y,
                marker=_markers[i % len(_markers)],
                linestyle=_linestyles[i % len(_linestyles)],
                color=colors[i % len(colors)],
                label=f"{label} (n={len(data)})",
            )
            has_data = True

    if has_data:
        plt.xlabel("Turns")
        plt.ylabel("CDF")
        plt.title(title)
        plt.grid(True)
        plt.legend()
        plt.savefig(output_path)
        plt.close()
        print(f"Turns CDF plot saved to {output_path}")
    else:
        plt.close()


def plot_turns_comparison_by_problem(
    data,
    name1,
    name2,
    output_path,
    colors=None,
    use_status_colors=False,
    sort_by_name=False,
):
    """Plot scatter: Y=problem labels, X=turn count for both agents."""
    if not HAS_PLOTTING:
        print(f"Matplotlib/Numpy not found. Skipping plot: {output_path}")
        return

    if not data:
        print("No turn data for comparison plot.")
        return

    if colors is None:
        colors = ["tab:blue", "tab:orange"]

    if sort_by_name:
        data = sorted(data, key=lambda x: x[0])
    else:
        data = sorted(data, key=lambda x: x[1] or 0)

    pids = [d[0] for d in data]
    fig_height = max(6, len(pids) * 0.3)
    plt.figure(figsize=(10, fig_height))

    y_vals = np.arange(len(pids))

    x1_succ, x1_fail, y1_succ, y1_fail = [], [], [], []
    x2_succ, x2_fail, y2_succ, y2_fail = [], [], [], []

    for i, (_pid, t1, t2, s1, s2) in enumerate(data):
        if t1 and t1 > 0:
            if s1:
                x1_succ.append(t1)
                y1_succ.append(i)
            else:
                x1_fail.append(t1)
                y1_fail.append(i)
        if t2 and t2 > 0:
            if s2:
                x2_succ.append(t2)
                y2_succ.append(i)
            else:
                x2_fail.append(t2)
                y2_fail.append(i)

    c_succ, c_fail = "tab:green", "tab:red"
    m1, m2 = "o", "x"

    if use_status_colors:
        if x1_succ:
            plt.scatter(x1_succ, y1_succ, color=c_succ, label=f"{name1} (Success)", marker=m1, alpha=0.7)
        if x1_fail:
            plt.scatter(x1_fail, y1_fail, color=c_fail, label=f"{name1} (Fail)", marker=m1, alpha=0.7)
        if x2_succ:
            plt.scatter(x2_succ, y2_succ, color=c_succ, label=f"{name2} (Success)", marker=m2, alpha=0.7)
        if x2_fail:
            plt.scatter(x2_fail, y2_fail, color=c_fail, label=f"{name2} (Fail)", marker=m2, alpha=0.7)
    else:
        x1_all = x1_succ + x1_fail
        y1_all = y1_succ + y1_fail
        x2_all = x2_succ + x2_fail
        y2_all = y2_succ + y2_fail
        if x1_all:
            plt.scatter(x1_all, y1_all, color=colors[0], label=name1, marker=m1, alpha=0.7)
        if x2_all:
            plt.scatter(x2_all, y2_all, color=colors[1], label=name2, marker=m2, alpha=0.7)

    plt.yticks(y_vals, pids)
    plt.xlabel("Turns")
    plt.title("Per-Problem Number of Turns")
    plt.grid(True, axis="y", linestyle=":", alpha=0.3)
    plt.grid(True, axis="x", linestyle="--", alpha=0.7)
    plt.legend()
    plt.tight_layout()
    plt.savefig(output_path)
    plt.close()
    print(f"Turns comparison plot saved to {output_path}")


def plot_token_comparison_by_problem(
    data,
    name1,
    name2,
    output_path,
    colors=None,
    use_status_colors=False,
    sort_by_name=False,
):
    """Plot scatter: Y=problem labels, X=token usage for both agents (dots like time-based)."""
    if not HAS_PLOTTING:
        print(f"Matplotlib/Numpy not found. Skipping plot: {output_path}")
        return

    if not data:
        print("No token data for comparison plot.")
        return

    if colors is None:
        colors = ["tab:blue", "tab:orange"]

    if sort_by_name:
        data = sorted(data, key=lambda x: x[0])
    else:
        data = sorted(data, key=lambda x: x[1] or 0)

    pids = [d[0] for d in data]
    fig_height = max(6, len(pids) * 0.3)
    plt.figure(figsize=(10, fig_height))

    y_vals = np.arange(len(pids))

    x1_succ, x1_fail, y1_succ, y1_fail = [], [], [], []
    x2_succ, x2_fail, y2_succ, y2_fail = [], [], [], []

    for i, (_pid, t1, t2, s1, s2) in enumerate(data):
        if t1 and t1 > 0:
            if s1:
                x1_succ.append(t1 / 1e6)
                y1_succ.append(i)
            else:
                x1_fail.append(t1 / 1e6)
                y1_fail.append(i)
        if t2 and t2 > 0:
            if s2:
                x2_succ.append(t2 / 1e6)
                y2_succ.append(i)
            else:
                x2_fail.append(t2 / 1e6)
                y2_fail.append(i)

    c_succ, c_fail = "tab:green", "tab:red"
    m1, m2 = "o", "x"

    if use_status_colors:
        if x1_succ:
            plt.scatter(x1_succ, y1_succ, color=c_succ, label=f"{name1} (Success)", marker=m1, alpha=0.7)
        if x1_fail:
            plt.scatter(x1_fail, y1_fail, color=c_fail, label=f"{name1} (Fail)", marker=m1, alpha=0.7)
        if x2_succ:
            plt.scatter(x2_succ, y2_succ, color=c_succ, label=f"{name2} (Success)", marker=m2, alpha=0.7)
        if x2_fail:
            plt.scatter(x2_fail, y2_fail, color=c_fail, label=f"{name2} (Fail)", marker=m2, alpha=0.7)
    else:
        x1_all = x1_succ + x1_fail
        y1_all = y1_succ + y1_fail
        x2_all = x2_succ + x2_fail
        y2_all = y2_succ + y2_fail
        if x1_all:
            plt.scatter(x1_all, y1_all, color=colors[0], label=name1, marker=m1, alpha=0.7)
        if x2_all:
            plt.scatter(x2_all, y2_all, color=colors[1], label=name2, marker=m2, alpha=0.7)

    plt.yticks(y_vals, pids)
    plt.xlabel("Tokens (M)")
    plt.title("Per-Problem Token Usage")
    plt.grid(True, axis="y", linestyle=":", alpha=0.3)
    plt.grid(True, axis="x", linestyle="--", alpha=0.7)
    plt.legend()
    plt.tight_layout()
    plt.savefig(output_path)
    plt.close()
    print(f"Token comparison plot saved to {output_path}")


def plot_cdfs(data_list, labels, title_metric, output_path, colors=None):
    """Plot CDF for N data series."""
    if not HAS_PLOTTING:
        print(f"Matplotlib/Numpy not found. Skipping plot: {output_path}")
        return

    _markers = [".", "x", "^", "s", "D", "v", "<", ">"]
    _linestyles = ["-", "--", "-.", ":"]

    if colors is None:
        colors = [f"C{i}" for i in range(len(data_list))]

    plt.figure(figsize=(10, 6))
    has_data = False

    for i, (data, label) in enumerate(zip(data_list, labels, strict=True)):
        data = sorted(data)
        if data:
            y = np.arange(1, len(data) + 1) / len(data)
            plt.plot(
                data,
                y,
                marker=_markers[i % len(_markers)],
                linestyle=_linestyles[i % len(_linestyles)],
                color=colors[i % len(colors)],
                label=f"{label} (n={len(data)})",
            )
            has_data = True

    if not has_data:
        print(f"No valid data to plot for {title_metric}")
        plt.close()
        return

    plt.xlabel("Time (s)")
    plt.ylabel("CDF")
    plt.title(f"CDF of {title_metric}")
    plt.grid(True)
    plt.legend()
    plt.savefig(output_path)
    plt.close()
    print(f"Plot saved to {output_path}")


def plot_comparison_by_problem(
    data,
    name1,
    name2,
    title_metric,
    output_path,
    colors=None,
    compact=False,
    use_status_colors=False,
    sort_by_name=False,
):
    if not HAS_PLOTTING:
        print(f"Matplotlib/Numpy not found. Skipping plot: {output_path}")
        return

    if not data:
        print(f"No valid data to plot for {title_metric}")
        return

    if colors is None:
        colors = ["tab:blue", "tab:orange"]

    if sort_by_name:
        data.sort(key=lambda x: x[0])
    else:
        # Sort by first dir's output time (x[1]).
        # Place None/Missing at the end (top of graph).
        data.sort(key=lambda x: x[1] if x[1] is not None else float("inf"))

    pids = [d[0] for d in data]

    if compact:
        # Compact mode: tighter vertical spacing
        fig_height = max(6, len(pids) * 0.05)
    else:
        # Standard mode: enough space for labels
        fig_height = max(6, len(pids) * 0.3)

    plt.figure(figsize=(10, fig_height))

    y_vals = np.arange(len(pids))

    # Extract valid points for series 1
    x1_succ, y1_succ = [], []
    x1_fail, y1_fail = [], []

    # Extract valid points for series 2
    x2_succ, y2_succ = [], []
    x2_fail, y2_fail = [], []

    for i, item in enumerate(data):
        # Unpack with default for backward compatibility if needed, though we updated all calls
        # item structure: (pid, v1, v2, s1, s2)
        v1 = item[1]
        v2 = item[2]
        s1 = item[3] if len(item) > 3 else True
        s2 = item[4] if len(item) > 4 else True

        if v1 is not None and v1 > 0:
            if s1:
                x1_succ.append(v1)
                y1_succ.append(i)
            else:
                x1_fail.append(v1)
                y1_fail.append(i)

        if v2 is not None and v2 > 0:
            if s2:
                x2_succ.append(v2)
                y2_succ.append(i)
            else:
                x2_fail.append(v2)
                y2_fail.append(i)

    # Plot Series 1
    # Colors: Success=Green, Fail=Red
    # Shapes: Series1=Circle('o'), Series2=Cross('x')
    c_succ = "tab:green"
    c_fail = "tab:red"

    m1 = "o"
    m2 = "x"

    if use_status_colors:
        if x1_succ:
            plt.scatter(x1_succ, y1_succ, color=c_succ, label=f"{name1} (Success)", marker=m1, alpha=0.7)
        if x1_fail:
            plt.scatter(x1_fail, y1_fail, color=c_fail, label=f"{name1} (Fail)", marker=m1, alpha=0.7)

        # Plot Series 2
        if x2_succ:
            plt.scatter(x2_succ, y2_succ, color=c_succ, label=f"{name2} (Success)", marker=m2, alpha=0.7)
        if x2_fail:
            plt.scatter(x2_fail, y2_fail, color=c_fail, label=f"{name2} (Fail)", marker=m2, alpha=0.7)
    else:
        # Use provided colors for series distinction
        # Combine succ/fail lists for each series since color is uniform
        x1_all = x1_succ + x1_fail
        y1_all = y1_succ + y1_fail
        x2_all = x2_succ + x2_fail
        y2_all = y2_succ + y2_fail

        if x1_all:
            plt.scatter(x1_all, y1_all, color=colors[0], label=name1, marker=m1, alpha=0.7)
        if x2_all:
            plt.scatter(x2_all, y2_all, color=colors[1], label=name2, marker=m2, alpha=0.7)

    if not compact:
        plt.yticks(y_vals, pids)
        plt.grid(True, axis="y", linestyle=":", alpha=0.3)
    else:
        plt.yticks([])

    plt.xlabel("Time (s)")
    plt.title(f"Per-Problem {title_metric}")
    plt.grid(True, axis="x", linestyle="--", alpha=0.7)
    plt.legend()

    plt.tight_layout()
    plt.savefig(output_path)
    plt.close()
    print(f"Comparison plot saved to {output_path}")


def plot_success_rates(d1, m1, n1, name1, d2, m2, n2, name2, output_path, colors=None):
    """
    Bar chart comparing diagnosis and mitigation success rates.
    """
    if not HAS_PLOTTING:
        print(f"Matplotlib/Numpy not found. Skipping plot: {output_path}")
        return

    if colors is None:
        colors = ["tab:blue", "tab:orange"]

    # Calculate rates
    d1_rate = (d1 / n1 * 100) if n1 > 0 else 0.0
    m1_rate = (m1 / n1 * 100) if n1 > 0 else 0.0
    d2_rate = (d2 / n2 * 100) if n2 > 0 else 0.0
    m2_rate = (m2 / n2 * 100) if n2 > 0 else 0.0

    labels = ["Diagnosis", "Mitigation"]
    x = np.arange(len(labels))
    width = 0.35

    plt.figure(figsize=(8, 6))

    # Agent 1 bars
    rects1 = plt.bar(
        x - width / 2,
        [d1_rate, m1_rate],
        width,
        label=f"{name1} (n={n1})",
        color=colors[0],
    )
    # Agent 2 bars
    rects2 = plt.bar(
        x + width / 2,
        [d2_rate, m2_rate],
        width,
        label=f"{name2} (n={n2})",
        color=colors[1],
    )

    plt.ylabel("Success Rate (%)")
    plt.title("Diagnosis & Mitigation Success Rates")
    plt.xticks(x, labels)
    plt.ylim(0, 110)  # Extra space for labels
    plt.legend()
    plt.grid(True, axis="y", linestyle="--", alpha=0.7)

    def autolabel(rects):
        """Attach a text label above each bar in *rects*, displaying its height."""
        for rect in rects:
            height = rect.get_height()
            plt.annotate(
                f"{height:.1f}%",
                xy=(rect.get_x() + rect.get_width() / 2, height),
                xytext=(0, 3),  # 3 points vertical offset
                textcoords="offset points",
                ha="center",
                va="bottom",
            )

    autolabel(rects1)
    autolabel(rects2)

    plt.tight_layout()
    plt.savefig(output_path)
    plt.close()
    print(f"Success rate plot saved to {output_path}")


def _load_sequence_rows(log_dir):
    """Shared helper: load all rows with sequence_index from a log directory."""
    files = find_results_csvs(log_dir)
    rows = []
    for fpath in sorted(files):
        if "ALL_results" in fpath or "_output.csv" in fpath:
            continue
        try:
            with open(fpath, newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                if not reader.fieldnames or "sequence_index" not in reader.fieldnames:
                    continue
                has_mitigation = "Mitigation.success" in reader.fieldnames or "Mitigation.judgment" in reader.fieldnames
                for row in reader:
                    try:
                        seq_idx = int(row["sequence_index"])
                    except (ValueError, TypeError):
                        continue
                    rows.append({"seq_idx": seq_idx, "row": row, "has_mitigation": has_mitigation})
        except Exception as e:
            print(f"Warning: could not read {fpath}: {e}")
    rows.sort(key=lambda r: r["seq_idx"])
    return rows


def _rolling_avg(xs, ys, w):
    """Return (x_out, smoothed_y) using a trailing lookback window.

    The first output is at index w-1, averaging ys[0:w]. Each subsequent
    output averages the preceding w values. Points where y is None are
    excluded before computing.
    """
    pairs = [(x, y) for x, y in zip(xs, ys, strict=True) if y is not None]
    if len(pairs) < w:
        return [], []
    xs_f, ys_f = zip(*pairs, strict=True)
    xs_out = list(xs_f[w - 1 :])
    smoothed = [sum(ys_f[i - w + 1 : i + 1]) / w for i in range(w - 1, len(ys_f))]
    return xs_out, smoothed


def plot_sequence_success_rate(log_dir, output_path=None, window=5):
    """Plot diagnosis and mitigation success rate vs sequence index.

    Each data point is 1 (success) or 0 (failure); the rolling average gives
    a smoothed success-rate trend line.

    Args:
        log_dir: Path to the experiment log directory.
        output_path: Where to save the PNG (default: <log_dir>/sequence_success_rate.png).
        window: Rolling-average window size for the trend line.
    """
    if not HAS_PLOTTING:
        print("Matplotlib/Numpy not found. Skipping sequence success rate plot.")
        return

    raw = _load_sequence_rows(log_dir)
    if not raw:
        print("No sequence_index data found. Is this a sequence-mode run?")
        return

    seq_idxs = [r["seq_idx"] for r in raw]
    diag_ys = [1 if r["row"].get("Diagnosis.success") == "True" else 0 for r in raw]
    mitig_ys = [
        1 if r["row"].get("Mitigation.success") == "True" else (0 if r.get("has_mitigation") else None) for r in raw
    ]

    fig, ax = plt.subplots(figsize=(12, 6))

    # Scatter: jitter y slightly so overlapping 0/1 points are visible
    jitter = 0.03
    diag_jitter = [y + jitter for y in diag_ys]
    mitig_xs = [r["seq_idx"] for r in raw if r.get("has_mitigation")]
    mitig_ys_plot = [1 if r["row"].get("Mitigation.success") == "True" else 0 for r in raw if r.get("has_mitigation")]
    mitig_jitter = [y - jitter for y in mitig_ys_plot]

    ax.scatter(seq_idxs, diag_jitter, color="tab:blue", alpha=0.25, s=15, zorder=2)
    if mitig_xs:
        ax.scatter(mitig_xs, mitig_jitter, color="tab:orange", alpha=0.25, s=15, zorder=2)

    rx, ry = _rolling_avg(seq_idxs, diag_ys, window)
    if rx:
        ax.plot(rx, ry, color="tab:blue", linewidth=2, label=f"Diagnosis (rolling avg w={window})")

    rx, ry = _rolling_avg(seq_idxs, mitig_ys, window)
    if rx:
        ax.plot(rx, ry, color="tab:orange", linewidth=2, label=f"Mitigation (rolling avg w={window})")

    ax.set_xlabel("Sequence Index")
    ax.set_ylabel("Success Rate")
    ax.set_ylim(-0.1, 1.1)
    ax.set_yticks([0, 0.25, 0.5, 0.75, 1.0])
    ax.set_yticklabels(["0%", "25%", "50%", "75%", "100%"])
    ax.set_title("Success Rate vs Sequence Index")
    ax.grid(True, linestyle="--", alpha=0.5)
    ax.legend()
    fig.tight_layout()

    if output_path is None:
        plot_dir = os.path.join(log_dir, "plots")
        os.makedirs(plot_dir, exist_ok=True)
        output_path = os.path.join(plot_dir, "sequence_success_rate.png")
    fig.savefig(output_path)
    plt.close(fig)
    print(f"Sequence success rate plot saved to {output_path}")


def plot_sequence_time(log_dir, output_path=None, window=5):
    """Plot solving time (TTL, TTM, and total) vs sequence index.

    Loads all *_results.csv files that contain a 'sequence_index' column,
    then plots each metric as a scatter with a rolling-average trend line.

    Args:
        log_dir: Path to the experiment log directory.
        output_path: Where to save the PNG (default: <log_dir>/sequence_time.png).
        window: Rolling-average window size for the trend line.
    """
    if not HAS_PLOTTING:
        print("Matplotlib/Numpy not found. Skipping sequence time plot.")
        return

    raw = _load_sequence_rows(log_dir)
    if not raw:
        print("No sequence_index data found. Is this a sequence-mode run?")
        return

    seq_idxs = [r["seq_idx"] for r in raw]
    ttls, ttms, tots = [], [], []
    for r in raw:
        row = r["row"]
        try:
            ttl = float(row["TTL"]) if row.get("TTL") else None
        except (ValueError, TypeError):
            ttl = None
        try:
            ttm_raw = float(row["TTM"]) if row.get("TTM") else None
            ttm = (ttm_raw - ttl) if (ttm_raw is not None and ttl is not None) else ttm_raw
        except (ValueError, TypeError):
            ttm = None
        ttls.append(ttl)
        ttms.append(ttm)
        tots.append((ttl + ttm) if (ttl is not None and ttm is not None) else None)

    fig, ax = plt.subplots(figsize=(12, 6))

    # Scatter points
    ttl_xs = [x for x, y in zip(seq_idxs, ttls, strict=True) if y is not None]
    ttl_ys = [y for y in ttls if y is not None]
    ttm_xs = [x for x, y in zip(seq_idxs, ttms, strict=True) if y is not None]
    ttm_ys = [y for y in ttms if y is not None]
    tot_xs = [x for x, y in zip(seq_idxs, tots, strict=True) if y is not None]
    tot_ys = [y for y in tots if y is not None]

    if ttl_ys:
        ax.scatter(ttl_xs, ttl_ys, color="tab:blue", alpha=0.35, s=20, zorder=2)
        rx, ry = _rolling_avg(ttl_xs, ttl_ys, window)
        if rx:
            ax.plot(rx, ry, color="tab:blue", linewidth=2, label=f"TTL (rolling avg w={window})")

    if ttm_ys:
        ax.scatter(ttm_xs, ttm_ys, color="tab:orange", alpha=0.35, s=20, zorder=2)
        rx, ry = _rolling_avg(ttm_xs, ttm_ys, window)
        if rx:
            ax.plot(rx, ry, color="tab:orange", linewidth=2, label=f"TTM (rolling avg w={window})")

    if tot_ys:
        ax.scatter(tot_xs, tot_ys, color="tab:green", alpha=0.35, s=20, zorder=2)
        rx, ry = _rolling_avg(tot_xs, tot_ys, window)
        if rx:
            ax.plot(rx, ry, color="tab:green", linewidth=2, label=f"Total (rolling avg w={window})")

    ax.set_xlabel("Sequence Index")
    ax.set_ylabel("Time (s)")
    ax.set_title("Solving Time vs Sequence Index")
    ax.grid(True, linestyle="--", alpha=0.5)
    ax.legend()
    fig.tight_layout()

    if output_path is None:
        plot_dir = os.path.join(log_dir, "plots")
        os.makedirs(plot_dir, exist_ok=True)
        output_path = os.path.join(plot_dir, "sequence_time.png")
    fig.savefig(output_path)
    plt.close(fig)
    print(f"Sequence time plot saved to {output_path}")


def _parse_mmdd_hhmm(ts):
    """Parse a MMDD_HHMM string into total minutes since Jan 1."""
    mm = int(ts[0:2])
    dd = int(ts[2:4])
    hh = int(ts[5:7])
    mi = int(ts[7:9])
    # Approximate: assume 31 days per month for sorting purposes
    return ((mm - 1) * 31 + (dd - 1)) * 24 * 60 + hh * 60 + mi


def _load_timeline_rows(log_dir, end_time=False):
    """Load results from a directory and sort chronologically by filename timestamp.

    Args:
        log_dir: Path to the experiment log directory.
        end_time: If True, treat the filename timestamp as end time and
            subtract solving time (TTL + TTM) to derive start time for sorting.

    Returns list of dicts with keys: incident_idx, ttl, ttm, diag_success, mitig_success.
    """
    run_map, _ = load_results(log_dir)
    if not run_map:
        return []

    # Build list with timestamp extracted from source_file
    entries = []
    for pid, r in run_map.items():
        if r["status"] != "Completed":
            continue
        fname = r.get("source_file", "")
        timestamp = fname[:9]  # MMDD_HHMM
        try:
            ttl = float(r["TTL"]) if r.get("TTL") else None
        except (ValueError, TypeError):
            ttl = None
        try:
            ttm_raw = float(r["TTM"]) if r.get("has_mitigation") and r.get("TTM") else None
            ttm = (ttm_raw - ttl) if (ttm_raw is not None and ttl is not None) else ttm_raw
        except (ValueError, TypeError):
            ttm = None
        diag_success = r.get("Diagnosis.success") == "True"
        mitig_success = r.get("Mitigation.success") == "True" if r.get("has_mitigation") else None

        # Compute sort key: start time in minutes
        try:
            file_minutes = _parse_mmdd_hhmm(timestamp)
        except (ValueError, IndexError):
            file_minutes = 0
        if end_time:
            # Subtract total solving time to get start time
            total_secs = (ttl or 0) + (ttm or 0)
            start_minutes = file_minutes - total_secs / 60.0
        else:
            start_minutes = file_minutes

        entries.append(
            {
                "timestamp": timestamp,
                "start_minutes": start_minutes,
                "problem_id": pid,
                "ttl": ttl,
                "ttm": ttm,
                "diag_success": diag_success,
                "mitig_success": mitig_success,
            }
        )

    # Sort by derived start time
    entries.sort(key=lambda e: e["start_minutes"])
    for i, e in enumerate(entries):
        e["incident_idx"] = i
    return entries


def plot_timeline(data_per_dir, names, metric, ylabel, output_path, colors, window):
    """Plot a timeline chart with scatter points and rolling average trend lines.

    Args:
        data_per_dir: list of (xs, ys) tuples, one per directory.
        names: legend names for each directory.
        metric: metric name for the title (e.g. "Diagnosis Time (TTL)").
        ylabel: Y-axis label.
        output_path: where to save the PNG.
        colors: list of color strings.
        window: rolling average window size.
    """
    if not HAS_PLOTTING:
        print(f"Matplotlib/Numpy not found. Skipping plot: {output_path}")
        return

    _markers = [".", "x", "^", "s", "D", "v", "<", ">"]
    _linestyles = ["-", "--", "-.", ":"]

    fig, ax = plt.subplots(figsize=(12, 6))
    has_data = False

    # Scatter points per directory
    for i, ((xs, ys), name) in enumerate(zip(data_per_dir, names, strict=True)):
        valid_xs = [x for x, y in zip(xs, ys, strict=True) if y is not None]
        valid_ys = [y for y in ys if y is not None]
        if not valid_xs:
            continue
        has_data = True
        color = colors[i % len(colors)]
        marker = _markers[i % len(_markers)]
        ax.scatter(
            valid_xs,
            valid_ys,
            color=color,
            alpha=0.35,
            s=20,
            zorder=2,
            marker=marker,
            label=name,
        )

    # Single rolling average across all directories combined
    combined = []
    for xs, ys in data_per_dir:
        combined.extend((x, y) for x, y in zip(xs, ys, strict=True) if y is not None)
    combined.sort(key=lambda p: p[0])
    if combined:
        all_xs, all_ys = zip(*combined, strict=True)
        rx, ry = _rolling_avg(list(all_xs), list(all_ys), window)
        if rx:
            ax.plot(
                rx,
                ry,
                color="black",
                linewidth=2,
                linestyle="-",
                label=f"Rolling avg (w={window})",
            )

    if not has_data:
        print(f"No valid data to plot for {metric}")
        plt.close(fig)
        return

    ax.set_xlabel("Incident Number (chronological)")
    ax.set_ylabel(ylabel)
    ax.set_title(f"{metric} vs Incident Order")
    ax.grid(True, linestyle="--", alpha=0.5)
    ax.legend()
    fig.tight_layout()
    fig.savefig(output_path)
    plt.close(fig)
    print(f"Timeline plot saved to {output_path}")


def timeline_results(dirs, names=None, window=5, end_time=False):
    """Plot timeline of solving times across chronologically ordered incidents."""
    # Load data from each directory, sequencing indices across dirs
    all_entries = []
    offset = 0
    for d in dirs:
        print(f"\n--- Loading timeline data from {d} ---")
        entries = _load_timeline_rows(d, end_time=end_time)
        for e in entries:
            e["incident_idx"] += offset
        offset += len(entries)
        print(f"  {len(entries)} completed incidents found")
        all_entries.append(entries)

    # Derive names and output directory
    dir_basenames = [os.path.basename(os.path.normpath(d)) for d in dirs]
    if names is None:
        names = dir_basenames
    if len(dirs) == 1:
        output_dir = os.path.abspath(dirs[0])
    else:
        # Reuse the diff/ output directory for multi-dir comparisons
        common_parent = os.path.commonpath([os.path.abspath(d) for d in dirs])
        output_dir = os.path.join(common_parent, "diff", "--".join(dir_basenames))
    os.makedirs(output_dir, exist_ok=True)
    print(f"\nTimeline results will be stored in: {output_dir}")

    colors = [f"C{i}" for i in range(len(dirs))]

    # Build per-dir data for each metric
    metrics = [
        ("timeline_diagnosis.png", "Diagnosis Time (TTL)", "TTL (seconds)", lambda e: e["ttl"]),
        ("timeline_mitigation.png", "Mitigation Time (TTM)", "TTM (seconds)", lambda e: e["ttm"]),
        (
            "timeline_resolution.png",
            "Resolution Time (TTM)",
            "TTM (seconds)",
            lambda e: (e["ttl"] + e["ttm"]) if (e["ttl"] is not None and e["ttm"] is not None) else None,
        ),
    ]

    for filename, title, ylabel, extract in metrics:
        data_per_dir = []
        for entries in all_entries:
            xs = [e["incident_idx"] for e in entries]
            ys = [extract(e) for e in entries]
            data_per_dir.append((xs, ys))
        plot_timeline(data_per_dir, names, title, ylabel, os.path.join(output_dir, filename), colors, window)

    # Print summary stats
    print("\n--- Timeline Summary ---")
    for name, entries in zip(names, all_entries, strict=True):
        ttls = [e["ttl"] for e in entries if e["ttl"] is not None]
        ttms = [e["ttm"] for e in entries if e["ttm"] is not None]
        n = len(entries)
        avg_ttl = sum(ttls) / len(ttls) if ttls else 0.0
        avg_ttm = sum(ttms) / len(ttms) if ttms else 0.0
        print(f"  {name}: {n} incidents, avg TTL={avg_ttl:.1f}s, avg TTM={avg_ttm:.1f}s")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Summarize SREGym benchmark results.")
    parser.add_argument("--diff", nargs="+", metavar="DIR", help="Compare results between 2 or more log directories.")
    parser.add_argument(
        "--names",
        nargs="+",
        metavar="NAME",
        help="Custom legend names for --diff directories (one per directory).",
    )
    parser.add_argument(
        "--limit-to-index",
        type=int,
        default=None,
        metavar="INDEX",
        help=(
            "Restrict --diff comparisons to problems present in the dataset at this 0-indexed "
            "position in --diff. All datasets are filtered to that subset of problem IDs before "
            "plotting."
        ),
    )
    parser.add_argument(
        "--sequence",
        metavar="DIR",
        help="Plot solving time vs sequence index for a sequence-mode run directory.",
    )
    parser.add_argument(
        "--sequence-window",
        type=int,
        default=5,
        metavar="W",
        help="Rolling-average window size for the sequence time plot (default: 5).",
    )
    parser.add_argument(
        "--timeline",
        nargs="+",
        metavar="DIR",
        help="Plot solving times over chronologically ordered incidents for one or more directories.",
    )
    parser.add_argument(
        "--timeline-window",
        type=int,
        default=5,
        metavar="W",
        help="Rolling-average window size for the timeline plot (default: 5).",
    )
    parser.add_argument(
        "--timeline-end-time",
        action="store_true",
        default=False,
        help="Treat filename timestamps as end times and subtract solving time to derive start times for ordering.",
    )
    parser.add_argument(
        "path",
        nargs="?",
        help="Optional path to a log directory (searched recursively) or a specific glob pattern.",
    )
    args = parser.parse_args()

    if args.diff:
        if args.names and len(args.names) != len(args.diff):
            parser.error(
                f"--names requires exactly {len(args.diff)} values (one per --diff directory), got {len(args.names)}"
            )
        diff_results(args.diff, names=args.names, limit_to_index=args.limit_to_index)
    elif args.timeline:
        if args.names and len(args.names) != len(args.timeline):
            n_expected = len(args.timeline)
            parser.error(
                f"--names requires exactly {n_expected} values (one per --timeline directory), got {len(args.names)}"
            )
        timeline_results(
            args.timeline,
            names=args.names,
            window=args.timeline_window,
            end_time=args.timeline_end_time,
        )
    else:
        summarize_results(args.sequence or args.path)
        if args.sequence:
            plot_sequence_time(args.sequence, window=args.sequence_window)
            plot_sequence_success_rate(args.sequence, window=args.sequence_window)
