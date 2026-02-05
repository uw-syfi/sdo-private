"""Tests for MetricsAggregator comparison metrics.

Covers the three comparison additions: duration, fallback rate, and per-phase
breakdown in compare_versions / _calculate_improvements.
"""

import json
import pytest
from pathlib import Path

from app_operator.dspy_integration.metrics_aggregator import MetricsAggregator


# ---------------------------------------------------------------------------
# Helpers for writing minimal trajectory files
# ---------------------------------------------------------------------------

def _write_trajectory(path: Path, conversations: list, phase: str = "deployment",
                      status: str = "completed") -> None:
    """Write a single trajectory JSON with the given phase conversations."""
    trajectory = {
        "metadata": {
            "repo_path": str(path.parent),
            "start_time": "2024-01-01 00:00:00",
            "end_time": "2024-01-01 00:01:00",
            "agent_name": "test",
            "status": status,
            "run_id": path.stem,
        },
        phase: conversations,
    }
    path.write_text(json.dumps(trajectory))


def _conv(*, exit_code=0, duration=10.0, fallback=False, content_len=400):
    """Return a minimal conversation dict."""
    return {
        "call_id": 1,
        "fallback_occurred": fallback,
        "messages": [
            {"role": "system", "content": "x" * content_len},
            {"role": "user", "content": "deploy"},
            {
                "role": "tool_call",
                "tool": "bash",
                "args": {"cmd": "echo hi"},
                "exit_code": exit_code,
                "stdout": "success" if exit_code == 0 else "error",
                "stderr": "",
            },
            {"role": "assistant", "content": "done", "duration_seconds": duration},
        ],
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def baseline_dir(tmp_path):
    """Baseline trajectories: 2 successful runs, no fallbacks, 10 s each."""
    d = tmp_path / "baseline"
    d.mkdir()
    _write_trajectory(d / "trajectory_1.json", [_conv(exit_code=0, duration=10.0, fallback=False)])
    _write_trajectory(d / "trajectory_2.json", [_conv(exit_code=0, duration=10.0, fallback=False)])
    return d


@pytest.fixture
def optimized_dir(tmp_path):
    """Optimized trajectories: 2 successful runs, one fallback, 6 s each."""
    d = tmp_path / "optimized"
    d.mkdir()
    _write_trajectory(d / "trajectory_3.json", [_conv(exit_code=0, duration=6.0, fallback=False)])
    _write_trajectory(d / "trajectory_4.json", [_conv(exit_code=0, duration=6.0, fallback=True)])
    return d


# ---------------------------------------------------------------------------
# Duration comparison
# ---------------------------------------------------------------------------

class TestDurationComparison:
    """Duration reduction appears in improvements when avg duration differs."""

    def test_duration_reduction_computed(self, baseline_dir, optimized_dir):
        agg = MetricsAggregator(baseline_dir)
        comparison = agg.compare_versions(baseline_dir, optimized_dir)
        impr = comparison["improvements"]

        assert "duration_reduction_pct" in impr
        # baseline avg=10 s, optimized avg=6 s → 40 % reduction
        assert impr["duration_reduction_pct"] == 40.0

    def test_duration_reduction_negative_on_regression(self, tmp_path):
        """Optimized runs slower → negative reduction (regression)."""
        slow = tmp_path / "slow"
        slow.mkdir()
        _write_trajectory(slow / "trajectory_s.json", [_conv(duration=20.0)])

        fast = tmp_path / "fast"
        fast.mkdir()
        _write_trajectory(fast / "trajectory_f.json", [_conv(duration=5.0)])

        agg = MetricsAggregator(fast)
        comparison = agg.compare_versions(fast, slow)  # fast=baseline, slow=optimized
        assert comparison["improvements"]["duration_reduction_pct"] == -300.0

    def test_duration_zero_baseline_omits_key(self, tmp_path):
        """When baseline avg duration is 0, the key is not included."""
        zero = tmp_path / "zero"
        zero.mkdir()
        _write_trajectory(zero / "trajectory_z.json", [_conv(duration=0.0)])

        nonzero = tmp_path / "nz"
        nonzero.mkdir()
        _write_trajectory(nonzero / "trajectory_nz.json", [_conv(duration=5.0)])

        agg = MetricsAggregator(zero)
        comparison = agg.compare_versions(zero, nonzero)
        assert "duration_reduction_pct" not in comparison["improvements"]


# ---------------------------------------------------------------------------
# Fallback-rate comparison
# ---------------------------------------------------------------------------

class TestFallbackRateComparison:
    """Fallback rate and its reduction percentage appear in improvements."""

    def test_fallback_rate_in_phase_metrics(self, optimized_dir):
        agg = MetricsAggregator(optimized_dir)
        metrics = agg.aggregate_metrics()
        overall = metrics["overall"]

        # 1 of 2 examples has fallback_occurred=True
        assert overall["fallback_rate"] == 0.5
        assert overall["fallback_count"] == 1

    def test_fallback_rate_zero_when_no_fallbacks(self, baseline_dir):
        agg = MetricsAggregator(baseline_dir)
        metrics = agg.aggregate_metrics()
        assert metrics["overall"]["fallback_rate"] == 0.0
        assert metrics["overall"]["fallback_count"] == 0

    def test_fallback_reduction_computed(self, tmp_path):
        """Baseline 50 % fallback, optimized 10 % → 80 % reduction."""
        b = tmp_path / "b"
        b.mkdir()
        _write_trajectory(b / "trajectory_b1.json", [_conv(fallback=True)])
        _write_trajectory(b / "trajectory_b2.json", [_conv(fallback=False)])

        o = tmp_path / "o"
        o.mkdir()
        # 10 trajectories, 1 fallback → 10 %
        for i in range(10):
            _write_trajectory(
                o / f"trajectory_o{i}.json",
                [_conv(fallback=(i == 0))],
            )

        agg = MetricsAggregator(b)
        impr = agg.compare_versions(b, o)["improvements"]
        assert impr["fallback_rate_reduction_pct"] == 80.0

    def test_fallback_regression_from_zero_is_none(self, tmp_path):
        """Baseline 0 % fallback, optimized > 0 % → reduction is None."""
        b = tmp_path / "b"
        b.mkdir()
        _write_trajectory(b / "trajectory_b.json", [_conv(fallback=False)])

        o = tmp_path / "o"
        o.mkdir()
        _write_trajectory(o / "trajectory_o.json", [_conv(fallback=True)])

        agg = MetricsAggregator(b)
        impr = agg.compare_versions(b, o)["improvements"]
        assert impr["fallback_rate_reduction_pct"] is None

    def test_both_zero_fallback_rate_is_zero(self, baseline_dir, tmp_path):
        """Both sides 0 % fallback → reduction is 0.0."""
        o = tmp_path / "o_zero"
        o.mkdir()
        _write_trajectory(o / "trajectory_oz.json", [_conv(fallback=False)])

        agg = MetricsAggregator(baseline_dir)
        impr = agg.compare_versions(baseline_dir, o)["improvements"]
        assert impr["fallback_rate_reduction_pct"] == 0.0


# ---------------------------------------------------------------------------
# Per-phase comparison
# ---------------------------------------------------------------------------

class TestPerPhaseComparison:
    """compare_versions populates by_phase with per-phase improvements."""

    def _multi_phase_dir(self, base: Path, name: str, deploy_dur: float,
                         monitor_dur: float) -> Path:
        d = base / name
        d.mkdir()
        # Single trajectory with both phases
        trajectory = {
            "metadata": {
                "repo_path": str(d),
                "start_time": "2024-01-01 00:00:00",
                "end_time": "2024-01-01 00:05:00",
                "agent_name": "test",
                "status": "completed",
                "run_id": name,
            },
            "deployment": [_conv(exit_code=0, duration=deploy_dur)],
            "monitoring": [
                {
                    "call_id": 2,
                    "fallback_occurred": False,
                    "messages": [
                        {"role": "system", "content": "monitor"},
                        {"role": "user", "content": "check health"},
                        {"role": "assistant",
                         "content": "<exec_summary>System is fully operational</exec_summary>",
                         "duration_seconds": monitor_dur},
                    ],
                }
            ],
        }
        (d / "trajectory_mp.json").write_text(json.dumps(trajectory))
        return d

    def test_shared_phases_are_compared(self, tmp_path):
        b = self._multi_phase_dir(tmp_path, "b", deploy_dur=20.0, monitor_dur=10.0)
        o = self._multi_phase_dir(tmp_path, "o", deploy_dur=10.0, monitor_dur=5.0)

        agg = MetricsAggregator(b)
        comparison = agg.compare_versions(b, o)

        assert "deployment" in comparison["by_phase"]
        assert "monitoring" in comparison["by_phase"]

    def test_phase_improvements_contain_expected_keys(self, tmp_path):
        b = self._multi_phase_dir(tmp_path, "b", deploy_dur=20.0, monitor_dur=10.0)
        o = self._multi_phase_dir(tmp_path, "o", deploy_dur=10.0, monitor_dur=5.0)

        agg = MetricsAggregator(b)
        deploy_impr = agg.compare_versions(b, o)["by_phase"]["deployment"]["improvements"]

        assert "success_rate_improvement" in deploy_impr
        assert "duration_reduction_pct" in deploy_impr
        assert "fallback_rate_reduction_pct" in deploy_impr

    def test_phase_duration_values_are_correct(self, tmp_path):
        b = self._multi_phase_dir(tmp_path, "b", deploy_dur=20.0, monitor_dur=10.0)
        o = self._multi_phase_dir(tmp_path, "o", deploy_dur=10.0, monitor_dur=5.0)

        agg = MetricsAggregator(b)
        comparison = agg.compare_versions(b, o)

        # deployment: 20 → 10 = 50 % reduction
        assert comparison["by_phase"]["deployment"]["improvements"]["duration_reduction_pct"] == 50.0
        # monitoring: 10 → 5 = 50 % reduction
        assert comparison["by_phase"]["monitoring"]["improvements"]["duration_reduction_pct"] == 50.0

    def test_unshared_phases_are_excluded(self, tmp_path):
        """A phase present only in one version must not appear in by_phase."""
        b = tmp_path / "b_uni"
        b.mkdir()
        trajectory = {
            "metadata": {"repo_path": str(b), "start_time": "2024-01-01 00:00:00",
                         "end_time": "2024-01-01 00:01:00", "agent_name": "t",
                         "status": "completed", "run_id": "b_uni"},
            "deployment": [_conv()],
            "monitoring": [
                {"call_id": 2, "fallback_occurred": False, "messages": [
                    {"role": "system", "content": "m"},
                    {"role": "user", "content": "check"},
                    {"role": "assistant",
                     "content": "<exec_summary>fully operational</exec_summary>",
                     "duration_seconds": 5.0},
                ]}
            ],
        }
        (b / "trajectory_bu.json").write_text(json.dumps(trajectory))

        # Optimized has only deployment
        o = tmp_path / "o_uni"
        o.mkdir()
        _write_trajectory(o / "trajectory_ou.json", [_conv()])

        agg = MetricsAggregator(b)
        comparison = agg.compare_versions(b, o)

        assert "deployment" in comparison["by_phase"]
        assert "monitoring" not in comparison["by_phase"]

    def test_empty_by_phase_when_no_shared_phases(self, tmp_path):
        """No shared phases → by_phase is empty dict."""
        b = tmp_path / "b_dep"
        b.mkdir()
        _write_trajectory(b / "trajectory_bd.json", [_conv()], phase="deployment")

        o = tmp_path / "o_mon"
        o.mkdir()
        trajectory = {
            "metadata": {"repo_path": str(o), "start_time": "2024-01-01 00:00:00",
                         "end_time": "2024-01-01 00:01:00", "agent_name": "t",
                         "status": "completed", "run_id": "o_mon"},
            "monitoring": [
                {"call_id": 1, "fallback_occurred": False, "messages": [
                    {"role": "system", "content": "m"},
                    {"role": "user", "content": "check"},
                    {"role": "assistant",
                     "content": "<exec_summary>fully operational</exec_summary>",
                     "duration_seconds": 5.0},
                ]}
            ],
        }
        (o / "trajectory_om.json").write_text(json.dumps(trajectory))

        agg = MetricsAggregator(b)
        comparison = agg.compare_versions(b, o)
        assert comparison["by_phase"] == {}
