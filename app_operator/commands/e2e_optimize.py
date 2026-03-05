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
import os
import time
from pathlib import Path
from typing import Any

try:
    import tomllib
except ImportError:
    import tomli as tomllib

from app_operator.logger import logger
from app_operator.dspy_integration.eval_execute import EvalExecuteOptimizer
from app_operator.config import load_config as load_app_config
from app_operator.experiment_naming import normalize_experiment_token
from app_operator.rate_limit_handler import run_subprocess_with_rate_limit_handling


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


def load_config(config_path: Path) -> dict[str, Any]:
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

    # Set defaults for optional fields
    if "inter_run_delay" not in config:
        config["inter_run_delay"] = 30  # Default 30 seconds between runs
    if "max_retries" not in config:
        config["max_retries"] = 3  # Default 3 retries
    if "rate_limit_backoff" not in config:
        config["rate_limit_backoff"] = 60  # Default 60 seconds for rate limits
    if "output_prefix" not in config:
        config["output_prefix"] = None  # Default: no prefix (write to optimized/)

    return config


def _infer_llm_provider(model: str | None, agent_provider: str) -> str:
    """Infer the LLM API provider from the model string for rate limit detection.

    The experiment config ``provider`` field (rlm, subagent, hybrid) identifies
    the agent architecture, not the LLM API.  Rate limit detection needs the
    actual LLM API provider (gemini, openai, anthropic, etc.).

    Args:
        model: litellm model string, e.g. ``"vertex_ai/gemini-2.5-pro"``.
        agent_provider: The agent architecture provider (may also be an LLM
            provider for non-RLM agents like ``"gemini"``).

    Returns:
        LLM provider string suitable for ``detect_rate_limit_error()``.
    """
    # Agent architecture names that are NOT LLM providers
    _architecture_names = {"rlm", "subagent", "hybrid"}

    if model:
        model_lower = model.lower()
        if "gemini" in model_lower or "vertex" in model_lower:
            return "gemini"
        if "claude" in model_lower or "anthropic" in model_lower:
            return "anthropic"
        if "gpt" in model_lower or "openai" in model_lower or "o1" in model_lower:
            return "openai"

    # agent_provider is already an LLM provider (e.g. "gemini", "openai")
    if agent_provider.lower() not in _architecture_names:
        return agent_provider

    return "gemini"  # conservative default


def _validate_app_paths(apps: list[Path], role: str) -> bool:
    """Validate that configured app paths exist."""
    missing = [app for app in apps if not app.exists()]
    if not missing:
        return True
    for app in missing:
        logger.error(f"{role} app path does not exist: {app}")
    return False


def _replace_in_agent_section(
    content: str,
    provider_override: str | None,
    model_override: str | None,
) -> str:
    """Replace provider/model keys only within the [agent] TOML section."""
    lines = content.splitlines()
    new_lines = []
    in_agent = False
    provider_replaced = False
    model_replaced = False

    for line in lines:
        stripped = line.strip()
        if re.match(r'^\[agent\]$', stripped):
            in_agent = True
        elif stripped.startswith("[") and in_agent:
            in_agent = False

        if in_agent and provider_override and not provider_replaced:
            m = re.match(r'^(\s*provider\s*=\s*).*$', line)
            if m:
                line = f'{m.group(1)}"{provider_override}"'
                provider_replaced = True

        if in_agent and model_override and not model_replaced:
            m = re.match(r'^(\s*model\s*=\s*).*$', line)
            if m:
                line = f'{m.group(1)}"{model_override}"'
                model_replaced = True

        new_lines.append(line)

    return "\n".join(new_lines)


def _update_sds_toml(
    app_dir: Path,
    use_seeds: bool,
    use_optimized: bool,
    optimized_version: str | None,
    project_root: Path | None = None,
    provider_override: str | None = None,
    model_override: str | None = None,
):
    """Update sds.toml in the app directory.

    When *provider_override* or *model_override* is given, the corresponding
    value inside the ``[agent]`` section is replaced so the experiment uses
    the specified setting regardless of what the root ``sds.toml`` says.
    """
    import re

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

    # Override provider/model only within the [agent] section.
    # We locate the [agent] block and do targeted substitution within it.
    if provider_override or model_override:
        content = _replace_in_agent_section(
            content, provider_override, model_override
        )

    final_content = content + "\n" + new_section_content
    sds_toml.write_text(final_content)


def _init_experiment(source_app: Path, work_dir: Path, name_suffix: str) -> Path:
    """Initialize a fresh experiment directory."""
    app_name = normalize_experiment_token(source_app.name)
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

    # Init git (use -b only on git >= 2.28; fall back to symbolic-ref)
    init_result = subprocess.run(
        ["git", "init", "-b", "main"],
        cwd=target_path,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if init_result.returncode != 0:
        subprocess.run(
            ["git", "init"],
            cwd=target_path,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        subprocess.run(
            ["git", "symbolic-ref", "HEAD", "refs/heads/main"],
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

    def _load_state(self) -> dict[str, Any]:
        default_state = {
            "current_iteration": 1,
            "optimization_done": False,
            "current_version": None,
            "completed_train_apps": [],
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
        tmp = self.state_file.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.state, indent=2))
        os.replace(tmp, self.state_file)

    def is_optimization_done(self, iter_num: int) -> bool:
        if self.state["current_iteration"] > iter_num:
            return True
        return self.state["optimization_done"]

    def mark_optimization_done(self, version: str):
        self.state["optimization_done"] = True
        self.state["current_version"] = version
        self.save()

    def get_current_version(self) -> str | None:
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

    work_dir = Path(config.get("work_dir", args.work_dir)).resolve()
    iterations = config["iterations"]
    prompts = config["prompts"]
    train_apps = [Path(p).resolve() for p in config["training"]["apps"]]
    val_apps = [Path(p).resolve() for p in config.get("validation", {}).get("apps", [])]
    inter_run_delay = config["inter_run_delay"]
    max_retries = config["max_retries"]
    rate_limit_backoff = config["rate_limit_backoff"]
    output_prefix = config["output_prefix"]
    provider_override = config.get("provider")  # Optional per-experiment provider
    model_override = config.get("model")  # Optional per-experiment model

    if iterations < 1:
        logger.error(f"iterations must be >= 1, got {iterations}")
        return 1
    if not prompts:
        logger.error("prompts must contain at least one prompt name")
        return 1
    if not _validate_app_paths(train_apps, "Training"):
        return 1
    if val_apps and not _validate_app_paths(val_apps, "Validation"):
        return 1

    # We need to access the prompts directory for the optimizer
    # Assuming standard layout
    base_dir = Path(__file__).parent.parent.parent
    prompts_dir = base_dir / "app_operator" / "prompts"
    if not prompts_dir.exists():
        logger.error(f"Prompts directory not found: {prompts_dir}")
        return 1

    if args.dry_run:
        logger.info("Dry-run mode enabled: validating config only, no runs will be executed.")
        logger.info(f"Iterations: {iterations}")
        logger.info(f"Prompts: {prompts}")
        logger.info(f"Training apps: {[a.name for a in train_apps]}")
        logger.info(f"Validation apps: {[a.name for a in val_apps]}")
        logger.info(f"Work dir: {work_dir}")
        if output_prefix:
            logger.info(f"Output prefix: {output_prefix}")
        if provider_override:
            logger.info(f"Provider override: {provider_override}")
        if model_override:
            logger.info(f"Model override: {model_override}")
        logger.info("Dry run validation successful.")
        return 0

    work_dir.mkdir(parents=True, exist_ok=True)
    state_manager = StateManager(work_dir)

    logger.info(f"Starting E2E optimization for {iterations} iterations")
    logger.info(f"Prompts: {prompts}")
    logger.info(f"Training apps: {[a.name for a in train_apps]}")
    if output_prefix:
        logger.info(f"Output prefix: {output_prefix} (will write to optimized/{output_prefix}/)")
    if provider_override:
        logger.info(f"Provider override: {provider_override}")
    if model_override:
        logger.info(f"Model override: {model_override}")
    logger.info(
        f"Rate limit handling: max_retries={max_retries}, "
        f"backoff={rate_limit_backoff}s, inter_run_delay={inter_run_delay}s"
    )

    # Load app config to get provider for rate limit detection.
    # provider_override is the *agent architecture* (rlm, subagent, hybrid)
    # which is NOT the LLM API provider.  Derive the LLM provider from the
    # model string so rate limit detection works correctly.
    app_location = None
    provider_model = model_override
    try:
        app_config = load_app_config(str(base_dir))
        agent_provider = provider_override or app_config.agent.provider
        app_location = app_config.agent.location
        provider_model = model_override or app_config.agent.model
    except Exception as e:
        logger.warning(f"Failed to load app config, assuming 'gemini' provider: {e}")
        agent_provider = provider_override or "gemini"
    provider = _infer_llm_provider(provider_model, agent_provider)

    current_version = state_manager.get_current_version()

    for i in range(1, iterations + 1):
        if state_manager.state["current_iteration"] > i:
            logger.info(f"Skipping Iteration {i} (already completed)")
            continue

        logger.info(f"\n=== Iteration {i}/{iterations} ===")

        # 1. + 2. Generate candidates, evaluate by running operator, keep best (EvalExecute)
        logger.info("Running eval-execute optimization...")

        if state_manager.is_optimization_done(i):
            logger.info("Skipping optimization (already done)")
            current_version = state_manager.get_current_version()
        else:
            # Load DSPy config from project root
            try:
                app_config = load_app_config(str(base_dir))
                dspy_config = app_config.dspy
                logger.info(
                    f"Loaded DSPy config: teacher={dspy_config.optimization.teacher_model}, "
                    f"n_candidates={dspy_config.optimization.n_candidates}"
                )
                app_location = app_config.agent.location
            except Exception as e:
                logger.warning(f"Failed to load project config, using defaults: {e}")
                from app_operator.dspy_integration.config import DSPyConfig

                dspy_config = DSPyConfig()

            try:
                next_version = f"v{i}"
                if output_prefix:
                    output_dir = prompts_dir / "optimized" / output_prefix / next_version
                else:
                    output_dir = prompts_dir / "optimized" / next_version

                eval_optimizer = EvalExecuteOptimizer(
                    config=dspy_config,
                    prompts_dir=prompts_dir,
                    project_root=base_dir,
                    n_candidates=dspy_config.optimization.n_candidates,
                    vertex_location=app_location,
                )
                result = eval_optimizer.optimize(
                    prompt_names=prompts,
                    train_apps=train_apps,
                    work_dir=work_dir,
                    output_dir=output_dir,
                    iteration=i,
                    current_version=current_version,
                    provider=provider,
                    max_retries=max_retries,
                    rate_limit_backoff=rate_limit_backoff,
                    inter_run_delay=inter_run_delay,
                    provider_override=provider_override,
                    model_override=model_override,
                    output_prefix=output_prefix,
                )

                if result["success"]:
                    if output_prefix:
                        current_version = f"{output_prefix}/{next_version}"
                    else:
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
                    provider_override=provider_override,
                    model_override=model_override,
                )

                logger.info(f"Running validation on {exp_path.name}...")
                cmd = [sys.executable, "-m", "app_operator", "run", str(exp_path)]

                _result, success, error_msg = run_subprocess_with_rate_limit_handling(
                    cmd=cmd,
                    provider=provider,
                    max_retries=max_retries,
                    base_delay=5,
                    rate_limit_backoff=rate_limit_backoff,
                    operation_name=f"Validation run: {exp_path.name}",
                )
                if not success:
                    logger.warning(
                        f"Validation failed for {app_name}: "
                        f"{error_msg or 'unknown error'}. Continuing."
                    )
                    continue
                state_manager.mark_val_app_completed(app_name)

                # Add delay before next validation run
                if app_name != val_apps[-1].name:  # Don't delay after last app
                    logger.info(f"Waiting {inter_run_delay}s before next run...")
                    time.sleep(inter_run_delay)

        state_manager.advance_iteration()

    logger.info("End-to-end optimization complete.")
    return 0
