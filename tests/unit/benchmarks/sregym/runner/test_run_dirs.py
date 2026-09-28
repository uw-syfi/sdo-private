"""Concurrent launches in the same second must never share an experiment or pipeline directory.

Two phase-1 Codex lanes started in the same second were both given
``logs/<timestamp>_codex`` and interleaved their ``tasklist.yml`` writes, so
both runs crashed on a corrupt tasklist before doing any work.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from unittest.mock import patch

from benchmarks.sregym.runner import runner as runner_mod
from benchmarks.sregym.runner.experiment import ExperimentConfig
from benchmarks.sregym.runner.pipeline import PipelineConfig, StageConfig

if TYPE_CHECKING:
    from pathlib import Path

FROZEN = datetime(2026, 9, 28, 21, 47, 7)


class _FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz=None) -> datetime:
        return FROZEN


def test_two_experiments_started_in_the_same_second_get_distinct_directories(tmp_path: Path) -> None:
    config = ExperimentConfig(agent="codex")
    with patch.object(runner_mod, "datetime", _FrozenDatetime):
        first = runner_mod._create_experiment_dir(config, tmp_path)
        second = runner_mod._create_experiment_dir(config, tmp_path)

    assert first != second
    assert first.name == "20260928_214707_codex"
    assert second.name.startswith("20260928_214707_codex")
    assert first.is_dir()
    assert second.is_dir()


def test_two_pipelines_started_in_the_same_second_get_distinct_directories(tmp_path: Path) -> None:
    config = PipelineConfig(name="p", stages=[StageConfig(name="s")])
    with patch.object(runner_mod, "datetime", _FrozenDatetime):
        first = runner_mod._create_pipeline_dir(config, tmp_path)
        second = runner_mod._create_pipeline_dir(config, tmp_path)

    assert first != second
    assert first.name == "20260928_214707_pipeline_p"
