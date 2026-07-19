#!/usr/bin/env python3
"""SREGym experiment launcher.

Supports both single experiments and multi-stage pipelines with
automatic knowledge base chaining.

Usage:
    # New single experiment:
    uv run python -m benchmarks.sregym.run benchmarks/sregym/experiments/default.toml

    # New pipeline (auto-detected by [[stages]] in TOML):
    uv run python -m benchmarks.sregym.run benchmarks/sregym/experiments/example_pipeline.toml

    # Resume experiment or pipeline:
    uv run python -m benchmarks.sregym.run third_party/sregym/logs/<exp_or_pipeline_dir>/

    # Rerun a specific pipeline stage:
    uv run python -m benchmarks.sregym.run third_party/sregym/logs/<pipeline_dir>/ --stage 1

This script is a thin integration layer: launcher orchestration lives in
:mod:`benchmarks.sregym.runner`; optional participant-specific lifecycle
behavior is loaded from :mod:`benchmarks.sregym.participants`.
"""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

from benchmarks.sregym.runner import (
    NOOP_EXP_STAGE_LIFECYCLE,
    ExpStageLifecycle,
    PipelineConfig,
    has_pipeline_state,
    is_pipeline_config,
    load_pipeline_config,
    merge_stage_config,
    read_pipeline_snapshot,
    read_pipeline_state,
    read_snapshot,
    reset_stages_for_rerun,
    run_pipeline,
    run_single_experiment,
    write_pipeline_state,
)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]

_SREGYM_DIR = Path(os.environ.get("SDO_SREGYM_DIR", _PROJECT_ROOT / "third_party" / "sregym")).resolve()


# ---------------------------------------------------------------------------
# Agent lifecycle loading
# ---------------------------------------------------------------------------


def _load_exp_stage_lifecycle(agent_name: str) -> ExpStageLifecycle:
    module_name = f"benchmarks.sregym.participants.{agent_name}"
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        if exc.name == module_name:
            return NOOP_EXP_STAGE_LIFECYCLE
        raise

    getter = getattr(module, "get_exp_stage_lifecycle", None)
    if getter is None:
        return NOOP_EXP_STAGE_LIFECYCLE

    lifecycle = getter()
    if lifecycle is None:
        return NOOP_EXP_STAGE_LIFECYCLE
    if not isinstance(lifecycle, ExpStageLifecycle):
        raise TypeError(
            f"{module_name}.get_exp_stage_lifecycle() must return ExpStageLifecycle or None, "
            f"got {type(lifecycle).__name__}"
        )
    return lifecycle


def _pipeline_agent_name(config: PipelineConfig) -> str:
    return merge_stage_config(config.defaults, {}).agent


# ---------------------------------------------------------------------------
# CLI argument parsing
# ---------------------------------------------------------------------------


def _parse_stage_arg(args: list[str]) -> tuple[int | None, list[str]]:
    """Extract --stage N from args. Returns (stage_index, remaining_args)."""
    remaining: list[str] = []
    stage_index: int | None = None
    i = 0
    while i < len(args):
        if args[i] == "--stage" and i + 1 < len(args):
            stage_index = int(args[i + 1])
            i += 2
        else:
            remaining.append(args[i])
            i += 1
    return stage_index, remaining


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    if len(sys.argv) < 2:
        print(
            "Usage: run_sregym.py <config.toml | experiment_dir> [--stage N]",
            file=sys.stderr,
        )
        sys.exit(1)

    target = Path(sys.argv[1])
    extra_args = sys.argv[2:]
    stage_index, extra_args = _parse_stage_arg(extra_args)

    if target.is_dir():
        target = target.resolve()
        if has_pipeline_state(target):
            config = read_pipeline_snapshot(target)
            lifecycle = _load_exp_stage_lifecycle(_pipeline_agent_name(config))
            state = read_pipeline_state(target)
            if stage_index is not None:
                reset_stages_for_rerun(config, state, stage_index, target)
                write_pipeline_state(state, target)
                print(f"Resetting from stage {stage_index} for rerun.")
            sys.exit(
                run_pipeline(
                    config,
                    project_root=_PROJECT_ROOT,
                    sregym_dir=_SREGYM_DIR,
                    pipeline_dir=target,
                    state=state,
                    lifecycle=lifecycle,
                )
            )
        else:
            if stage_index is not None:
                print(
                    "Error: --stage is only supported for pipeline directories.",
                    file=sys.stderr,
                )
                sys.exit(1)
            config = read_snapshot(target)
            run_single_experiment(
                target,
                extra_args,
                project_root=_PROJECT_ROOT,
                sregym_dir=_SREGYM_DIR,
                lifecycle=_load_exp_stage_lifecycle(config.agent),
            )

    elif target.is_file() and target.suffix == ".toml":
        if stage_index is not None:
            print(
                "Error: --stage is only supported when resuming a pipeline directory.",
                file=sys.stderr,
            )
            sys.exit(1)
        if is_pipeline_config(target):
            config = load_pipeline_config(target)
            lifecycle = _load_exp_stage_lifecycle(_pipeline_agent_name(config))
            sys.exit(
                run_pipeline(
                    config,
                    project_root=_PROJECT_ROOT,
                    sregym_dir=_SREGYM_DIR,
                    lifecycle=lifecycle,
                )
            )
        else:
            from benchmarks.sregym.runner.runner import load_experiment_config_or_resolve

            config = load_experiment_config_or_resolve(target)
            run_single_experiment(
                target,
                extra_args,
                project_root=_PROJECT_ROOT,
                sregym_dir=_SREGYM_DIR,
                lifecycle=_load_exp_stage_lifecycle(config.agent),
            )

    else:
        print(
            f"Error: '{target}' is neither a .toml config file nor an existing experiment directory.",
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
