"""Stream learning-curve aggregation: excluded incidents drop out of both arms."""

from __future__ import annotations

from benchmarks.sregym.analysis.stream_curve import Row, exclude_incidents


def _row(arm: str, index: int) -> Row:
    return Row(
        arm=arm,
        index=index,
        problem_id="p",
        kind="exact",
        family="f",
        passed=True,
        ttd_s=1.0,
        ttm_s=2.0,
        raw_incl_judge_s=3.0,
        tokens_raw=10,
        tokens_weighted=5.0,
        usd=0.0,
        requests=1,
    )


def test_excluded_incident_indices_are_removed_from_every_arm() -> None:
    rows = [_row(arm, i) for arm in ("sdo", "codex") for i in range(4)]
    kept = exclude_incidents(rows, {1, 3})
    assert sorted((r.arm, r.index) for r in kept) == [("codex", 0), ("codex", 2), ("sdo", 0), ("sdo", 2)]
