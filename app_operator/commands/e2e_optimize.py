"""End-to-end prompt optimization loop.

This command orchestrates a full optimization cycle:
1. Generate trajectories using current prompts (seeds or optimized).
2. Optimize prompts using collected trajectories.
3. Validate using fresh trajectories.
4. Repeat.
"""

import argparse
import re
import sys
import shutil
import subprocess
import json
from pathlib import Path
from typing import Dict, Any, Optional

try:
    import tomllib
except ImportError:
    import tomli as tomllib

from app_operator.logger import logger
from app_operator.dspy_integration.optimizer import PromptOptimizer
from app_operator.config import load_config as load_app_config


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Add arguments for e2e-optimize command."""
    parser.add_argument(
        "--config",
        required=True,
        help="Path to E2E configuration TOML file",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate inputs and configuration without running",
    )
    parser.add_argument(
        "--work-dir",
        default="e2e_optimization",
        help="Working directory for experiments (default: e2e_optimization)",
    )


def load_config(config_path: Path) -> Dict[str, Any]:
    """Load and validate configuration."""
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with open(config_path, "rb") as f:
        config = tomllib.load(f)

    # Basic validation
    required_keys = ["iterations", "prompts", "training"]
    for key in required_keys:
        if key not in config:
            raise ValueError(f"Missing required config key: {key}")

    if "apps" not in config["training"]:
        raise ValueError("Missing 'apps' in [training] section")

    return config


def _update_sds_toml(
    app_dir: Path,
    use_seeds: bool,
    use_optimized: bool,
    optimized_version: Optional[str],
    project_root: Optional[Path] = None,
):
    """Update sds.toml in the app directory."""
    sds_toml = app_dir / "sds.toml"
    config_toml = app_dir / "config.toml"

    # Read existing content
    content = ""
    if sds_toml.exists():
        content = sds_toml.read_text()
    elif config_toml.exists():
        content = config_toml.read_text()
    elif project_root:
        # Fallback to global config if local config doesn't exist
        # This prevents masking the global config when we create a partial sds.toml
        global_sds = project_root / "sds.toml"
        global_config = project_root / "config.toml"
        if global_sds.exists():
            content = global_sds.read_text()
        elif global_config.exists():
            content = global_config.read_text()

    # We'll use a simple string manipulation approach to ensure we don't mess up formatting
    # or require a TOML writer. We'll append or replace the [dspy] section.

    dspy_section = "\n[dspy]\n"
    dspy_settings = []

    if use_seeds:
        dspy_settings.append("use_seeds = true")
    else:
        dspy_settings.append("use_seeds = false")

    if use_optimized:
        dspy_settings.append("use_optimized = true")
        if optimized_version:
            dspy_settings.append(f'optimized_version = "{optimized_version}"')
    else:
        dspy_settings.append("use_optimized = false")

    # Construct the new section
    new_section_content = dspy_section + "\n".join(dspy_settings) + "\n"

    if "[dspy]" in content:
        # Remove existing [dspy] section (and subsections) and append new one
        lines = content.splitlines()
        new_lines = []
        skip = False
        for line in lines:
            if re.match(r'^\[dspy(\..*)?\]$', line.strip()):
                skip = True
                continue
            if skip and line.strip().startswith("["):
                # Only stop skipping if this is NOT a dspy subsection
                if not re.match(r'^\[dspy(\..*)?\]$', line.strip()):
                    skip = False

            if not skip:
                new_lines.append(line)
        content = "\n".join(new_lines)

    final_content = content + "\n" + new_section_content
    sds_toml.write_text(final_content)


def _init_experiment(source_app: Path, work_dir: Path, name_suffix: str) -> Path:
    """Initialize a fresh experiment directory."""
    app_name = source_app.name
    exp_name = f"{app_name}_{name_suffix}"
    target_path = work_dir / exp_name

    if target_path.exists():
        shutil.rmtree(target_path)

    # Copy app
    shutil.copytree(source_app, target_path)

    # Clean up
    git_dir = target_path / ".git"
    if git_dir.exists():
        if git_dir.is_dir():
            shutil.rmtree(git_dir)
        else:
            git_dir.unlink()

    sds_dir = target_path / ".sds"
    if sds_dir.exists():
        shutil.rmtree(sds_dir)

    # Init git
    subprocess.run(
        ["git", "init", "-b", "main"],
        cwd=target_path,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    return target_path


class StateManager:
    """Manages the state of the E2E optimization loop."""

    def __init__(self, work_dir: Path):
        self.state_file = work_dir / "state.json"
        self.state = self._load_state()

    def _load_state(self) -> Dict[str, Any]:
        default_state = {
            "current_iteration": 1,
            "completed_train_apps": [],
            "optimization_done": False,
            "current_version": None,
            "completed_val_apps": [],
        }
        if self.state_file.exists():
            try:
                data = json.loads(self.state_file.read_text())
            except json.JSONDecodeError:
                logger.warning("Corrupted state file, starting fresh.")
                return default_state

            # Validate required keys and types
            schema = {
                "current_iteration": int,
                "completed_train_apps": list,
                "optimization_done": bool,
                "current_version": (str, type(None)),
                "completed_val_apps": list,
            }
            try:
                for key, expected_type in schema.items():
                    if key not in data:
                        raise ValueError(
                            f"Missing required key: {key}")
                    if not isinstance(data[key], expected_type):
                        raise TypeError(
                            f"Key '{key}' has wrong type: "
                            f"expected {expected_type}, "
                            f"got {type(data[key])}"
                        )
            except (ValueError, TypeError) as e:
                logger.warning(
                    f"Invalid state file schema ({e}), starting fresh.")
                return default_state

            return data

        return default_state

    def save(self):
        self.state_file.write_text(json.dumps(self.state, indent=2))

    def is_train_app_completed(self, iter_num: int, app_name: str) -> bool:
        if self.state["current_iteration"] > iter_num:
            return True
        return app_name in self.state["completed_train_apps"]

    def mark_train_app_completed(self, app_name: str):
        if app_name not in self.state["completed_train_apps"]:
            self.state["completed_train_apps"].append(app_name)
            self.save()

    def is_optimization_done(self, iter_num: int) -> bool:
        if self.state["current_iteration"] > iter_num:
            return True
        return self.state["optimization_done"]

    def mark_optimization_done(self, version: str):
        self.state["optimization_done"] = True
        self.state["current_version"] = version
        self.save()

    def get_current_version(self) -> Optional[str]:
        return self.state["current_version"]

    def is_val_app_completed(self, iter_num: int, app_name: str) -> bool:
        if self.state["current_iteration"] > iter_num:
            return True
        return app_name in self.state["completed_val_apps"]

    def mark_val_app_completed(self, app_name: str):
        if app_name not in self.state["completed_val_apps"]:
            self.state["completed_val_apps"].append(app_name)
            self.save()

    def advance_iteration(self):
        self.state["current_iteration"] += 1
        self.state["completed_train_apps"] = []
        self.state["optimization_done"] = False
        # Keep current_version as the starting point for next iteration
        self.state["completed_val_apps"] = []
        self.save()


def run_command(args: argparse.Namespace) -> int:
    """Execute e2e-optimize command."""
    try:
        config_path = Path(args.config)
        config = load_config(config_path)
    except Exception as e:
        logger.error(f"Failed to load config: {e}")
        return 1

    work_dir = Path(args.work_dir).resolve()
    work_dir.mkdir(parents=True, exist_ok=True)

    state_manager = StateManager(work_dir)

    iterations = config["iterations"]
    prompts = config["prompts"]
    train_apps = [Path(p).resolve() for p in config["training"]["apps"]]
    val_apps = [Path(p).resolve() for p in config.get("validation", {}).get("apps", [])]

    logger.info(f"Starting E2E optimization for {iterations} iterations")
    logger.info(f"Prompts: {prompts}")
    logger.info(f"Training apps: {[a.name for a in train_apps]}")

    # We need to access the prompts directory for the optimizer
    # Assuming standard layout
    base_dir = Path(__file__).parent.parent.parent
    prompts_dir = base_dir / "app_operator" / "prompts"

    current_version = state_manager.get_current_version()

    for i in range(1, iterations + 1):
        if state_manager.state["current_iteration"] > i:
            logger.info(f"Skipping Iteration {i} (already completed)")
            continue

        logger.info(f"\n=== Iteration {i}/{iterations} ===")

        # 1. Generate Training Trajectories
        logger.info("Generating training trajectories...")
        train_trajectories_dirs = []

        for app_path in train_apps:
            app_name = app_path.name
            exp_name = f"{app_name}_iter{i}_train"
            exp_path = work_dir / exp_name

            if state_manager.is_train_app_completed(i, app_name):
                logger.info(f"Skipping {app_name} (already trained)")
                # Even if skipped, we need the trajectory dir for optimization
                traj_dir = exp_path / ".sds" / "trajectories"
                if traj_dir.exists():
                    train_trajectories_dirs.append(traj_dir)
                else:
                    logger.warning(
                        f"Expected trajectories at {traj_dir} but not found!"
                    )
                continue

            # Check if experiment already has trajectories from a partial run
            traj_dir = exp_path / ".sds" / "trajectories"
            if traj_dir.exists() and any(traj_dir.iterdir()):
                logger.info(
                    f"Reusing existing trajectories for {app_name}")
                train_trajectories_dirs.append(traj_dir)
                state_manager.mark_train_app_completed(app_name)
                continue

            exp_path = _init_experiment(app_path, work_dir, f"iter{i}_train")

            # Configure sds.toml
            if i == 1:
                # First iteration: Use Seeds
                _update_sds_toml(
                    exp_path,
                    use_seeds=True,
                    use_optimized=False,
                    optimized_version=None,
                    project_root=base_dir,
                )
            else:
                # Subsequent iterations: Use Optimized
                _update_sds_toml(
                    exp_path,
                    use_seeds=False,
                    use_optimized=True,
                    optimized_version=current_version,
                    project_root=base_dir,
                )

            logger.info(f"Running operator on {exp_path.name}...")
            # We run the operator as a subprocess
            cmd = [sys.executable, "-m", "app_operator", "run", str(exp_path)]

            try:
                subprocess.run(
                    cmd, check=False, timeout=3600
                )  # Don't fail the whole loop if one run fails
            except subprocess.TimeoutExpired:
                logger.error(
                    f"Run timed out after 3600s for {app_name}")
            except Exception as e:
                logger.error(f"Run failed for {exp_path.name}: {e}")

            traj_dir = exp_path / ".sds" / "trajectories"
            if traj_dir.exists():
                train_trajectories_dirs.append(traj_dir)
                state_manager.mark_train_app_completed(app_name)
            else:
                logger.warning(f"No trajectories found for {exp_path.name}")

        if not train_trajectories_dirs:
            logger.error("No training trajectories generated. Aborting.")
            return 1

        # 2. Optimize
        logger.info("Optimizing prompts...")

        if state_manager.is_optimization_done(i):
            logger.info("Skipping optimization (already done)")
            current_version = state_manager.get_current_version()
        else:
            # Load DSPy config from project root to respect sds.toml settings (e.g. COPRO)
            try:
                # We need to find the project root. base_dir is app_operator/.., which is sds/
                # So base_dir is the repo root.
                app_config = load_app_config(str(base_dir))
                dspy_config = app_config.dspy
                logger.info(
                    f"Loaded DSPy config: optimizer={dspy_config.optimization.optimizer}, teacher={dspy_config.optimization.teacher_model}"
                )
            except Exception as e:
                logger.warning(f"Failed to load project config, using defaults: {e}")
                # Fallback to defaults if loading fails, but we should import DSPyConfig
                # for this fallback
                from app_operator.dspy_integration.config import DSPyConfig

                dspy_config = DSPyConfig()

            optimizer = PromptOptimizer(
                config=dspy_config,
                prompts_dir=prompts_dir,
                use_seeds=True,  # Always optimize starting from seeds + new demos
            )

            try:
                next_version = f"v{i}"
                output_dir = prompts_dir / "optimized" / next_version

                result = optimizer.optimize(
                    prompt_names=prompts,
                    trajectories_dirs=train_trajectories_dirs,
                    output_dir=output_dir,
                )

                if result["success"]:
                    current_version = next_version
                    logger.info(
                        f"Optimization successful. New version: {current_version}"
                    )
                    state_manager.mark_optimization_done(current_version)
                else:
                    logger.error("Optimization failed.")
                    return 1

            except Exception as e:
                logger.error(f"Optimization error: {e}")
                return 1

        # 3. Validation (Optional)
        if val_apps:
            logger.info("Running validation...")
            for app_path in val_apps:
                app_name = app_path.name
                if state_manager.is_val_app_completed(i, app_name):
                    logger.info(f"Skipping validation for {app_name} (already done)")
                    continue

                exp_path = _init_experiment(app_path, work_dir, f"iter{i}_val")
                _update_sds_toml(
                    exp_path,
                    use_seeds=False,
                    use_optimized=True,
                    optimized_version=current_version,
                    project_root=base_dir,
                )

                logger.info(f"Running validation on {exp_path.name}...")
                try:
                    subprocess.run(
                        [sys.executable, "-m", "app_operator",
                            "run", str(exp_path)],
                        check=False,
                        timeout=3600,
                    )
                except subprocess.TimeoutExpired:
                    logger.error(
                        f"Run timed out after 3600s for {app_name}")
                state_manager.mark_val_app_completed(app_name)

        state_manager.advance_iteration()

    logger.info("End-to-end optimization complete.")
    return 0
