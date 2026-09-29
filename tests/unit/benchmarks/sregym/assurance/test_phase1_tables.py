from __future__ import annotations

from benchmarks.sregym.assurance.phase1_tables import DECOY_PATTERN, Row, cell


def _row(arm: str, *, diagnosis: bool | None, mitigation: bool | None, ttm: float | None = None) -> Row:
    return Row(arm, "p", diagnosis, mitigation, ttm, ttm, ttm, 1, 2, 3, 1, 10.0, 10.0, False, None, "")


def test_decoy_pattern_matches_revoked_privilege_diagnoses_only() -> None:
    assert DECOY_PATTERN.search("the admin account had readWrite privileges revoked")
    assert not DECOY_PATTERN.search("mongo-geo-script ConfigMap is missing")


def test_cell_counts_end_to_end_and_uses_passing_runs_for_time() -> None:
    rows = [
        _row("codex", diagnosis=True, mitigation=True, ttm=10.0),
        _row("codex", diagnosis=True, mitigation=False, ttm=500.0),
        _row("codex", diagnosis=None, mitigation=None),
    ]
    result = cell(rows)
    assert (result["n"], result["diag"], result["mit"], result["e2e"], result["no_verdict"]) == (3, 2, 1, 1, 1)
    assert result["ttm"] == 10.0
