"""Crucible dual-agent judge-loop client for SREGym."""

from pathlib import Path

from libs.sregym_lib import ExperimentConfig, ExpStageLifecycle
from sregym_agents.crucible.kb_update_queue import (
    KbQueueSnapshot,
    snapshot_kb_queue,
    wait_for_kb_queue_drain,
)
from sregym_agents.crucible.knowledge_base import seed_kb

_KB_QUEUE_DRAIN_TIMEOUT_S = 1800.0
_KB_QUEUE_DRAIN_POLL_INTERVAL_S = 2.0


def _before_stage(exp_dir: Path, config: ExperimentConfig) -> None:
    seed_kb(exp_dir / "kb", config.env.crucible_seed_kb_dir or None)


def _snapshot_before_drain(exp_dir: Path, config: ExperimentConfig) -> object:
    del config
    return snapshot_kb_queue(exp_dir / "kb")


def _wait_for_drain(exp_dir: Path, baseline: object) -> None:
    assert isinstance(baseline, KbQueueSnapshot)
    wait_for_kb_queue_drain(
        exp_dir / "kb",
        baseline=baseline,
        timeout_s=_KB_QUEUE_DRAIN_TIMEOUT_S,
        poll_interval_s=_KB_QUEUE_DRAIN_POLL_INTERVAL_S,
    )


def get_exp_stage_lifecycle() -> ExpStageLifecycle:
    return ExpStageLifecycle(
        before_stage=_before_stage,
        snapshot_before_drain=_snapshot_before_drain,
        wait_for_drain=_wait_for_drain,
    )


__all__ = ["get_exp_stage_lifecycle"]
