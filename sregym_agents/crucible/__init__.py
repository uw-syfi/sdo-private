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


class _CrucibleExpStageLifecycle:
    def before_stage(self, exp_dir: Path, config: ExperimentConfig) -> None:
        crucible_cfg = config.agent_config.get("crucible", {})
        seed_kb(
            exp_dir / "kb",
            crucible_cfg.get("seed_kb_dir")
            or crucible_cfg.get("crucible_seed_kb_dir")
            or config.env.crucible_seed_kb_dir
            or None,
        )

    def snapshot_before_drain(self, exp_dir: Path, config: ExperimentConfig) -> object | None:
        del config
        return snapshot_kb_queue(exp_dir / "kb")

    def wait_for_drain(self, exp_dir: Path, baseline: object | None) -> None:
        assert isinstance(baseline, KbQueueSnapshot)
        wait_for_kb_queue_drain(
            exp_dir / "kb",
            baseline=baseline,
            timeout_s=_KB_QUEUE_DRAIN_TIMEOUT_S,
            poll_interval_s=_KB_QUEUE_DRAIN_POLL_INTERVAL_S,
        )


_EXP_STAGE_LIFECYCLE: ExpStageLifecycle = _CrucibleExpStageLifecycle()


def get_exp_stage_lifecycle() -> ExpStageLifecycle:
    return _EXP_STAGE_LIFECYCLE


__all__ = ["get_exp_stage_lifecycle"]
