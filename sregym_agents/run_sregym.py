#!/usr/bin/env python3
"""SREGym experiment launcher.

Supports both single experiments and multi-stage pipelines with
automatic knowledge base chaining.

Usage:
    # New single experiment:
    uv run python -m sregym_agents.run_sregym sregym_agents/experiments/default.toml

    # New pipeline (auto-detected by [[stages]] in TOML):
    uv run python -m sregym_agents.run_sregym sregym_agents/experiments/example_pipeline.toml

    # Resume experiment or pipeline:
    uv run python -m sregym_agents.run_sregym bench/sregym/logs/<exp_or_pipeline_dir>/

    # Rerun a specific pipeline stage:
    uv run python -m sregym_agents.run_sregym bench/sregym/logs/<pipeline_dir>/ --stage 1

This script is a thin integration layer: the launcher orchestration lives
in :mod:`libs.sregym_lib`; the crucible-specific knowledge-base hooks are
wired in here.
"""

from __future__ import annotations

import sys
from pathlib import Path

from libs.sregym_lib import (
    ExperimentConfig,
    StageHooks,
    has_pipeline_state,
    is_pipeline_config,
    load_pipeline_config,
    read_pipeline_snapshot,
    read_pipeline_state,
    reset_stages_for_rerun,
    run_pipeline,
    run_single_experiment,
    write_pipeline_state,
)
from sregym_agents.crucible.kb_update_queue import (
    KbQueueSnapshot,
    snapshot_kb_queue,
    wait_for_kb_queue_drain,
)
from sregym_agents.crucible.knowledge_base import seed_kb

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

_SREGYM_DIR = _PROJECT_ROOT / "bench" / "sregym"
_KB_QUEUE_DRAIN_TIMEOUT_S = 1800.0
_KB_QUEUE_DRAIN_POLL_INTERVAL_S = 2.0


# ---------------------------------------------------------------------------
# Crucible-specific stage hooks
# ---------------------------------------------------------------------------


def _crucible_before_stage(exp_dir: Path, config: ExperimentConfig) -> None:
    seed_kb(exp_dir / "kb", config.env.crucible_seed_kb_dir or None)


def _crucible_snapshot(exp_dir: Path, config: ExperimentConfig) -> object:
    del config
    return snapshot_kb_queue(exp_dir / "kb")


def _crucible_wait_for_drain(exp_dir: Path, baseline: object) -> None:
    assert isinstance(baseline, KbQueueSnapshot)
    wait_for_kb_queue_drain(
        exp_dir / "kb",
        baseline=baseline,
        timeout_s=_KB_QUEUE_DRAIN_TIMEOUT_S,
        poll_interval_s=_KB_QUEUE_DRAIN_POLL_INTERVAL_S,
    )


_CRUCIBLE_HOOKS = StageHooks(
    before_stage=_crucible_before_stage,
    snapshot_before_drain=_crucible_snapshot,
    wait_for_drain=_crucible_wait_for_drain,
)


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
                    hooks=_CRUCIBLE_HOOKS,
                )
            )
        else:
            if stage_index is not None:
                print(
                    "Error: --stage is only supported for pipeline directories.",
                    file=sys.stderr,
                )
                sys.exit(1)
            run_single_experiment(
                target,
                extra_args,
                project_root=_PROJECT_ROOT,
                sregym_dir=_SREGYM_DIR,
                hooks=_CRUCIBLE_HOOKS,
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
            sys.exit(
                run_pipeline(
                    config,
                    project_root=_PROJECT_ROOT,
                    sregym_dir=_SREGYM_DIR,
                    hooks=_CRUCIBLE_HOOKS,
                )
            )
        else:
            run_single_experiment(
                target,
                extra_args,
                project_root=_PROJECT_ROOT,
                sregym_dir=_SREGYM_DIR,
                hooks=_CRUCIBLE_HOOKS,
            )

    else:
        print(
            f"Error: '{target}' is neither a .toml config file nor an existing experiment directory.",
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
