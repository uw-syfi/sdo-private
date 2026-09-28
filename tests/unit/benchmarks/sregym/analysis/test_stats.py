from __future__ import annotations

import pytest

from benchmarks.sregym.analysis.stats import (
    newcombe_interval,
    stratified_bootstrap_ratio,
    wilson_interval,
)


def test_wilson_interval_centers_below_the_naive_proportion_for_a_small_sample() -> None:
    result = wilson_interval(9, 10)
    assert result.point == pytest.approx(0.9)
    assert 0.0 < result.low < result.point < result.high < 1.0
    # Small-n Wilson intervals are asymmetric and pull away from the boundary.
    assert result.high < 1.0


def test_wilson_interval_is_exact_at_the_boundaries() -> None:
    zero = wilson_interval(0, 20)
    assert zero.point == 0.0
    assert zero.low == 0.0
    full = wilson_interval(20, 20)
    assert full.point == 1.0
    assert full.high == 1.0


@pytest.mark.parametrize(("successes", "n"), [(-1, 5), (6, 5)])
def test_wilson_interval_rejects_impossible_counts(successes: int, n: int) -> None:
    with pytest.raises(ValueError, match="successes must be"):
        wilson_interval(successes, n)


def test_wilson_interval_rejects_nonpositive_n() -> None:
    with pytest.raises(ValueError, match="n must be positive"):
        wilson_interval(1, 0)


def test_newcombe_interval_is_zero_width_for_identical_arms() -> None:
    result = newcombe_interval(18, 20, 18, 20)
    assert result.point == 0.0
    assert result.low < 0.0 < result.high


def test_newcombe_interval_is_asymmetric_and_excludes_zero_for_well_separated_arms() -> None:
    result = newcombe_interval(40, 40, 10, 40)
    assert result.point == pytest.approx(0.75)
    assert result.low > 0.0
    assert result.high <= 1.0


def test_stratified_bootstrap_ratio_recovers_a_known_ratio_with_tight_ci_at_large_n() -> None:
    # Two "problems", Codex TTM around 100s, SDO warm TTM around 20s: true ratio 5.
    cells = {
        "S1": ([100.0] * 30, [20.0] * 30),
        "S2": ([100.0] * 30, [20.0] * 30),
    }
    result = stratified_bootstrap_ratio(cells, n_boot=500, seed=1)
    assert result.point == pytest.approx(5.0)
    assert result.ci_low == pytest.approx(5.0)
    assert result.ci_high == pytest.approx(5.0)
    assert result.per_cell["S1"] == pytest.approx(5.0)
    assert result.per_cell["S2"] == pytest.approx(5.0)


def test_stratified_bootstrap_ratio_is_deterministic_for_a_fixed_seed() -> None:
    cells = {"S1": ([90.0, 110.0, 70.0, 130.0, 100.0], [15.0, 25.0, 20.0, 18.0])}
    first = stratified_bootstrap_ratio(cells, n_boot=2000, seed=20261003)
    second = stratified_bootstrap_ratio(cells, n_boot=2000, seed=20261003)
    assert first == second


def test_stratified_bootstrap_ratio_is_unknown_when_no_cell_has_both_arms() -> None:
    result = stratified_bootstrap_ratio({"S1": ([], [1.0, 2.0]), "S2": ([3.0], [])})
    assert not result.known
    assert result.ci_low is None
    assert result.ci_high is None


def test_stratified_bootstrap_ratio_pools_cells_by_their_own_sample_size() -> None:
    # A big cell at ratio 2 and a tiny cell at ratio 10: the pooled ratio should sit
    # much closer to 2 than to the unweighted mean of (2, 10), because pooling
    # concatenates raw values rather than averaging per-cell ratios.
    cells = {
        "big": ([20.0] * 100, [10.0] * 100),
        "small": ([10.0] * 2, [1.0] * 2),
    }
    result = stratified_bootstrap_ratio(cells, n_boot=500, seed=7)
    assert result.point is not None
    assert abs(result.point - 2.0) < abs(result.point - 6.0)
