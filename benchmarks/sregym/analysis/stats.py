"""Confidence-interval methods pre-registered in ``experiments/assurance/PLAN.md`` (a).

- **Proportions:** Wilson score 95% CI (:func:`wilson_interval`).
- **Differences of proportions:** Newcombe hybrid-score 95% CI, method 10
  (:func:`newcombe_interval`), built from each side's Wilson interval.
- **Time and token ratios:** ratio of medians, with a stratified bootstrap.
  Resampling is within each ``(problem, arm)`` cell, 10,000 replicates, seed
  20261003, percentile 95% CI. A pooled ratio pools the per-cell resamples, so
  each problem keeps its weight (:func:`stratified_bootstrap_ratio`).

Every function is pure and takes plain numbers or sequences, so the claims
analysis (:mod:`benchmarks.sregym.assurance.phase1_analyze`) can be tested
without any run directory.
"""

from __future__ import annotations

import random
import statistics
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

#: PLAN.md (a): "10,000 replicates, seed 20261003, percentile 95% CI".
DEFAULT_BOOTSTRAP_REPLICATES = 10_000
DEFAULT_BOOTSTRAP_SEED = 20261003
Z_95 = 1.959963984540054


@dataclass(frozen=True)
class WilsonInterval:
    point: float
    low: float
    high: float

    def __post_init__(self) -> None:
        if not 0.0 <= self.low <= self.point <= self.high <= 1.0:
            raise ValueError(f"WilsonInterval must satisfy 0 <= low <= point <= high <= 1, got {self}")


def wilson_interval(successes: int, n: int, *, z: float = Z_95) -> WilsonInterval:
    """Wilson score 95% CI for a proportion of *successes* out of *n* trials."""

    if n <= 0:
        raise ValueError(f"n must be positive, got {n}")
    if not 0 <= successes <= n:
        raise ValueError(f"successes must be in [0, n], got {successes} of {n}")
    p = successes / n
    z2 = z * z
    denom = 1 + z2 / n
    center = (p + z2 / (2 * n)) / denom
    half = (z * ((p * (1 - p) / n + z2 / (4 * n * n)) ** 0.5)) / denom
    # Clamp against p too: floating-point rounding can otherwise put the
    # computed bound a hair on the wrong side of p at n's boundaries (p=0 or 1).
    low = max(0.0, min(center - half, p))
    high = min(1.0, max(center + half, p))
    return WilsonInterval(point=p, low=low, high=high)


@dataclass(frozen=True)
class NewcombeInterval:
    """Newcombe (1998) method 10 hybrid-score 95% CI for ``p1 - p2``."""

    point: float
    low: float
    high: float

    def __post_init__(self) -> None:
        if not -1.0 <= self.low <= self.point <= self.high <= 1.0:
            raise ValueError(f"NewcombeInterval must satisfy -1 <= low <= point <= high <= 1, got {self}")


def newcombe_interval(successes1: int, n1: int, successes2: int, n2: int, *, z: float = Z_95) -> NewcombeInterval:
    """95% CI for the difference of two independent proportions, ``p1 - p2``."""

    w1 = wilson_interval(successes1, n1, z=z)
    w2 = wilson_interval(successes2, n2, z=z)
    diff = w1.point - w2.point
    low = diff - ((w1.point - w1.low) ** 2 + (w2.high - w2.point) ** 2) ** 0.5
    high = diff + ((w1.high - w1.point) ** 2 + (w2.point - w2.low) ** 2) ** 0.5
    return NewcombeInterval(point=diff, low=max(-1.0, low), high=min(1.0, high))


@dataclass(frozen=True)
class BootstrapRatioResult:
    """A ratio of medians (numerator arm / denominator arm) with a stratified-bootstrap CI."""

    point: float | None
    ci_low: float | None
    ci_high: float | None
    replicates: int
    #: Per-cell (problem) ratio of medians, when every cell has data in both arms.
    per_cell: dict[str, float | None]

    @property
    def known(self) -> bool:
        return self.point is not None


def _median_or_none(values: Sequence[float]) -> float | None:
    return statistics.median(values) if values else None


def _ratio(numerator: Sequence[float], denominator: Sequence[float]) -> float | None:
    num_med = _median_or_none(numerator)
    den_med = _median_or_none(denominator)
    if num_med is None or den_med is None or den_med == 0:
        return None
    return num_med / den_med


def stratified_bootstrap_ratio(
    cells: Mapping[str, tuple[Sequence[float], Sequence[float]]],
    *,
    n_boot: int = DEFAULT_BOOTSTRAP_REPLICATES,
    seed: int = DEFAULT_BOOTSTRAP_SEED,
    percentile: float = 95.0,
) -> BootstrapRatioResult:
    """Pooled ratio of medians (numerator / denominator) across ``(problem -> (numerator, denominator))`` cells.

    Resampling is within each cell (with replacement, same size as the
    original cell); a replicate's pooled ratio takes the median over every
    cell's resampled values concatenated together, so a cell with more
    original samples keeps proportionally more weight in the pool. The CI is
    the empirical percentile interval over *n_boot* replicates.
    """

    if not 0 < percentile < 100:
        raise ValueError(f"percentile must be in (0, 100), got {percentile}")
    usable = {problem: (num, den) for problem, (num, den) in cells.items() if len(num) > 0 and len(den) > 0}
    per_cell = {problem: _ratio(num, den) for problem, (num, den) in cells.items()}
    if not usable:
        return BootstrapRatioResult(point=None, ci_low=None, ci_high=None, replicates=0, per_cell=per_cell)

    pooled_num = [value for num, _ in usable.values() for value in num]
    pooled_den = [value for _, den in usable.values() for value in den]
    point = _ratio(pooled_num, pooled_den)

    rng = random.Random(seed)
    replicate_ratios: list[float] = []
    for _ in range(n_boot):
        rep_num: list[float] = []
        rep_den: list[float] = []
        for num, den in usable.values():
            rep_num.extend(rng.choices(num, k=len(num)))
            rep_den.extend(rng.choices(den, k=len(den)))
        ratio = _ratio(rep_num, rep_den)
        if ratio is not None:
            replicate_ratios.append(ratio)

    if not replicate_ratios:
        return BootstrapRatioResult(point=point, ci_low=None, ci_high=None, replicates=0, per_cell=per_cell)

    replicate_ratios.sort()
    lower_tail = (100 - percentile) / 2 / 100
    lo_index = min(len(replicate_ratios) - 1, max(0, round(lower_tail * (len(replicate_ratios) - 1))))
    hi_index = min(len(replicate_ratios) - 1, max(0, round((1 - lower_tail) * (len(replicate_ratios) - 1))))
    return BootstrapRatioResult(
        point=point,
        ci_low=replicate_ratios[lo_index],
        ci_high=replicate_ratios[hi_index],
        replicates=len(replicate_ratios),
        per_cell=per_cell,
    )
