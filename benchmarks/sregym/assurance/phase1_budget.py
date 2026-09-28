"""Quota budget estimates for the phase-1 matrix, calibrated from measured tokens.

2026-10 correction (see ``RUNBOOK.md`` and ``HARNESS_DECISIONS.md``): this
module originally converted PLAN.md (d)'s per-unit costs, which are stated in
*weekly-percent* directly (SDO stage 0.13%, Codex attempt 0.07%, "good to
about x2"). That scale is wrong by several times over. The evidence:

- A live ``network_policy_block`` eval (SDO lifecycle + 3 SDO warm attempts +
  3 Codex-stock attempts + 3 Codex+verify attempts, run 2026-09-28 05:04-08:30
  UTC) consumed on the order of 8-13M raw tokens total (measured directly from
  the run's own ``incident_cost`` outputs: ``lifecycle_tokens=2,086,905`` for
  the one cold lifecycle bootstrap, ``sdo_tokens_with_learning`` of 521,453 /
  789,458 / 705,392 for three warm single-stage repeats, and a Codex-stock
  mean of 529,706.67 tokens/attempt x3 runs = 1,589,120; the Codex+verify
  arm's own tokens were not separately captured, only PLAN.md's 1.3x
  relative multiplier).
- Across every ``QUOTA-READ`` in that window's ``queue.events`` log, the
  primary window's ``used_percent`` read exactly ``90.0`` at every single
  checkpoint (start of every lane's launch), unmoved from the run before it
  to the run after it.

That means the true points-per-token rate cannot be measured directly here:
every observed delta is exactly zero even at multi-million-token scale, so
``(delta used_percent) / (delta tokens)`` is 0/large, not a usable positive
rate. Per the correction's own instruction, this module falls back to a
conservative rate instead of inventing precision the data does not support:
``POINTS_PER_TOKEN = 1e-7`` (1 point per 10,000,000 tokens). That fallback
rate is *higher* (more cautious: it assumes more quota cost per token) than
either observed upper bound (under 1 point per ~12.9M tokens per the
correction's own citation; under 1 point per ~8.4M measured raw tokens summed
across every run in the cited session scratchpad), so it will not
under-budget relative to what was actually observed.

The relative *shape* of PLAN.md (d)'s per-unit costs (composite stages/
attempts at 1.5x, verify at 1.3x, "worst case" at 2x) is not disputed by this
correction and is kept unchanged; only the absolute weekly-% conversion was
wrong.

2026-09-28 (later the same day) user decision: phase 1 drops its stock
(no-verify) Codex arm entirely. The sole Codex arm is now the default,
concise-verify baseline; ``FULL_MATRIX`` and ``REDUCED_MATRIX`` therefore
carry one Codex component (``codex_attempts``), not two. The measured
``CODEX_ATTEMPT_TOKENS`` constant (a stock attempt) times ``VERIFY_MULTIPLIER``
is kept as the estimate for a concise-verify attempt: no concise-specific
token measurement exists yet, and this is the conservative (higher) of the
two verify variants measured so far.

2026-10 gate change (user decision, logged in ``HARNESS_DECISIONS.md``): the
start gate now checks *expected* (nominal) cost against the stop line, not
the 2x worst case -- the 2x figure was designed for a per-unit rate an order
of magnitude more expensive than what was actually observed, and kept
shrinking the full matrix down to a single SDO pipeline for no measured
reason. The live global stop (``QuotaGate.must_stop_matrix``) and the
per-lane 1.5x abort (``QuotaGate.lane_over_budget``) remain the run's safety
nets. The worst case is still computed and printed for information.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

#: Measured 2026-09-28, ``network_policy_block``, warm single-stage SDO
#: attempts (``sdo1``/``sdo2``/``sdo3``): mean of 521453, 789458, 705392.
SDO_STAGE_TOKENS = 666_758.0
#: Measured the same day: the one-time cold lifecycle bootstrap (controller
#: install + detector generation) a pipeline pays once, before its first
#: stage, from a fresh workspace (``lifecycle2``'s ``lifecycle_tokens``).
SDO_LIFECYCLE_BOOTSTRAP_TOKENS = 2_086_905.0
#: Measured the same day: Codex-stock mean tokens/attempt on
#: ``network_policy_block`` (``codex_x3b``, 3 runs, mean_tokens). Phase 1 no
#: longer schedules a stock arm; this constant is still the base a
#: concise-verify attempt's estimate is built from (see module docstring).
CODEX_ATTEMPT_TOKENS = 529_706.6666666666

#: PLAN.md (d)'s relative multipliers, unchanged by this correction.
SDO_COMPOSITE_STAGE_MULTIPLIER = 1.5
CODEX_COMPOSITE_ATTEMPT_MULTIPLIER = 1.5
VERIFY_MULTIPLIER = 1.3
WORST_CASE_MULTIPLIER = 2.0

#: The fallback rate this correction adopts (see module docstring).
POINTS_PER_TOKEN = 1e-7

#: PLAN.md (b): the five phase-1 problems.
PHASE1_PROBLEMS = (
    "missing_configmap_hotel_reservation",  # S1
    "wrong_service_selector_hotel_reservation",  # S2
    "network_policy_block",  # S3
    "composite_policy_and_rate_configmap_hotel_reservation",  # K1
    "composite_frontend_selector_and_readiness_hotel_reservation",  # K2
)
COMPOSITE_PROBLEMS = (PHASE1_PROBLEMS[3], PHASE1_PROBLEMS[4])
#: The smoke run's one problem: S1, PLAN.md (b)'s anchor ("Anchors Step 3").
SMOKE_PROBLEM = PHASE1_PROBLEMS[0]

#: PLAN.md (d)'s full phase-1 matrix, as amended by the 2026-09-28 no-stock-arm
#: decision: 4 SDO pipelines (rotations A-D), 5 concise-verify Codex attempts
#: per problem, 0 stock.
FULL_MATRIX: dict[str, int] = {"sdo_pipelines": 4, "codex_attempts": 5}
#: The 2026-10 reduced preset (pivot #1): 2 SDO pipelines (rotations A, C), 3
#: concise-verify Codex attempts per problem. Kept as an explicit ``--matrix``
#: option; the default is :data:`FULL_MATRIX`.
REDUCED_MATRIX: dict[str, int] = {"sdo_pipelines": 2, "codex_attempts": 3}
#: A component may not shrink below this floor: at least one SDO pipeline and
#: at least one Codex attempt per problem, so phase 1 never launches with no
#: baseline comparison at all.
MATRIX_FLOORS: dict[str, int] = {"sdo_pipelines": 1, "codex_attempts": 1}


def sdo_stage_tokens(problem: str) -> float:
    if problem in COMPOSITE_PROBLEMS:
        return SDO_STAGE_TOKENS * SDO_COMPOSITE_STAGE_MULTIPLIER
    return SDO_STAGE_TOKENS


def codex_attempt_tokens(problem: str) -> float:
    """One concise-verify Codex attempt's tokens (phase 1's sole Codex arm)."""

    base = (
        CODEX_ATTEMPT_TOKENS * CODEX_COMPOSITE_ATTEMPT_MULTIPLIER
        if problem in COMPOSITE_PROBLEMS
        else CODEX_ATTEMPT_TOKENS
    )
    return base * VERIFY_MULTIPLIER


def sdo_pipeline_tokens(problems: Sequence[str], *, rounds: int = 2, include_bootstrap: bool = True) -> float:
    """One SDO pipeline's tokens: *rounds* passes over *problems*, plus its one-time bootstrap."""

    if rounds < 1:
        raise ValueError(f"rounds must be >= 1, got {rounds}")
    stage_total = sum(sdo_stage_tokens(problem) for problem in problems) * rounds
    return stage_total + (SDO_LIFECYCLE_BOOTSTRAP_TOKENS if include_bootstrap else 0.0)


def codex_arm_tokens(problems: Sequence[str], attempts_per_problem: int) -> float:
    if attempts_per_problem < 0:
        raise ValueError(f"attempts_per_problem must be >= 0, got {attempts_per_problem}")
    if attempts_per_problem == 0:
        return 0.0
    return sum(codex_attempt_tokens(problem) for problem in problems) * attempts_per_problem


@dataclass(frozen=True)
class TokenBudgetEstimate:
    label: str
    breakdown: dict[str, float]
    worst_case_multiplier: float = WORST_CASE_MULTIPLIER
    points_per_token: float = POINTS_PER_TOKEN

    def __post_init__(self) -> None:
        if not self.breakdown:
            raise ValueError("breakdown must have at least one component")
        if any(value < 0 for value in self.breakdown.values()):
            raise ValueError(f"breakdown values must be non-negative, got {self.breakdown}")
        if self.worst_case_multiplier < 1.0:
            raise ValueError(f"worst_case_multiplier must be >= 1.0, got {self.worst_case_multiplier}")
        if self.points_per_token <= 0:
            raise ValueError(f"points_per_token must be positive, got {self.points_per_token}")

    @property
    def nominal_tokens(self) -> float:
        return sum(self.breakdown.values())

    @property
    def worst_case_tokens(self) -> float:
        return self.nominal_tokens * self.worst_case_multiplier

    @property
    def nominal_percent(self) -> float:
        return self.nominal_tokens * self.points_per_token

    @property
    def worst_case_percent(self) -> float:
        return self.worst_case_tokens * self.points_per_token

    def fits(self, *, current_used_percent: float, stop_percent: float) -> bool:
        """Start gate: current + this plan's EXPECTED (nominal) cost must clear the stop line.

        2026-10 gate change (``HARNESS_DECISIONS.md``): gating on the 2x
        worst case auto-shrunk the full matrix to a single SDO pipeline for
        no measured reason. The live global stop and the per-lane 1.5x abort
        are the run's actual safety nets; the worst case is still computed
        (``worst_case_percent``) and printed for information, not gated on.
        """

        return current_used_percent + self.nominal_percent <= stop_percent

    def render(self) -> str:
        lines = [
            f"{self.label}: nominal {self.nominal_tokens:,.0f} tok ({self.nominal_percent:.3f} pt, "
            "gates the start decision), "
            f"worst case (x{self.worst_case_multiplier:.0f}) {self.worst_case_tokens:,.0f} tok "
            f"({self.worst_case_percent:.3f} pt, informational only)"
        ]
        lines.extend(f"  - {name}: {value:,.0f} tok" for name, value in self.breakdown.items())
        return "\n".join(lines)


def matrix_budget(
    label: str,
    *,
    sdo_pipelines: int,
    codex_attempts: int,
    problems: Sequence[str] = PHASE1_PROBLEMS,
    sdo_rounds: int = 2,
) -> TokenBudgetEstimate:
    """One SDO / Codex (concise verify) matrix cell selection, in tokens.

    Phase 1 has no stock (no-verify) Codex arm (user decision, 2026-09-28):
    *codex_attempts* is the sole Codex arm, the default concise-verify
    baseline.
    """

    if sdo_pipelines < 0 or codex_attempts < 0:
        raise ValueError("sdo_pipelines and codex_attempts must be non-negative")
    breakdown: dict[str, float] = {}
    if sdo_pipelines:
        breakdown[f"sdo_{sdo_pipelines}_pipelines"] = sdo_pipeline_tokens(problems, rounds=sdo_rounds) * sdo_pipelines
    if codex_attempts:
        breakdown["codex"] = codex_arm_tokens(problems, codex_attempts)
    if not breakdown:
        raise ValueError(f"{label!r} matrix produced an empty plan; widen at least one component")
    return TokenBudgetEstimate(label, breakdown)


def full_matrix_budget(
    *,
    sdo_pipelines: int | None = None,
    codex_attempts: int | None = None,
) -> TokenBudgetEstimate:
    plan = dict(FULL_MATRIX)
    if sdo_pipelines is not None:
        plan["sdo_pipelines"] = sdo_pipelines
    if codex_attempts is not None:
        plan["codex_attempts"] = codex_attempts
    return matrix_budget("full-matrix", sdo_pipelines=plan["sdo_pipelines"], codex_attempts=plan["codex_attempts"])


def reduced_matrix_budget(
    *,
    sdo_pipelines: int | None = None,
    codex_attempts: int | None = None,
) -> TokenBudgetEstimate:
    plan = dict(REDUCED_MATRIX)
    if sdo_pipelines is not None:
        plan["sdo_pipelines"] = sdo_pipelines
    if codex_attempts is not None:
        plan["codex_attempts"] = codex_attempts
    return matrix_budget("reduced-matrix", sdo_pipelines=plan["sdo_pipelines"], codex_attempts=plan["codex_attempts"])


def smoke_budget() -> TokenBudgetEstimate:
    """1 SDO 2-stage (cold+warm) pipeline + 1 Codex (concise verify) attempt, one problem (S1)."""

    return matrix_budget(
        "smoke",
        sdo_pipelines=1,
        codex_attempts=1,
        problems=(SMOKE_PROBLEM,),
    )


@dataclass(frozen=True)
class ShrunkPlan:
    """The result of :func:`autoshrink_to_fit`: the plan actually selected, and why."""

    plan: dict[str, int]
    estimate: TokenBudgetEstimate
    fits: bool
    shrunk: bool
    steps: list[str] = field(default_factory=list)

    def explain(self) -> str:
        header = "no shrink needed" if not self.shrunk else "auto-shrunk to fit quota headroom:"
        lines = [header, *self.steps, self.estimate.render()]
        if not self.fits:
            lines.append("WARNING: even the floor plan does not fit; matrix cannot start under this gate")
        return "\n".join(lines)


def autoshrink_to_fit(
    *,
    current_used_percent: float,
    stop_percent: float,
    start: dict[str, int] | None = None,
    problems: Sequence[str] = PHASE1_PROBLEMS,
) -> ShrunkPlan:
    """Start from *start* (default :data:`FULL_MATRIX`) and drop attempts/pipelines until the plan fits.

    Every component's per-unit token cost is constant (linear in count), so
    the components are drained in a fixed priority order, most expensive
    first, one unit at a time, down to :data:`MATRIX_FLOORS`, checking
    ``fits`` after every single reduction and stopping as soon as it does.
    """

    plan = dict(start or FULL_MATRIX)
    label = "auto-shrunk-matrix"

    def estimate_of(candidate: dict[str, int]) -> TokenBudgetEstimate:
        return matrix_budget(
            label,
            sdo_pipelines=candidate["sdo_pipelines"],
            codex_attempts=candidate["codex_attempts"],
            problems=problems,
        )

    def marginal_tokens(component: str) -> float:
        one = {"sdo_pipelines": 0, "codex_attempts": 0}
        one[component] = 1
        return matrix_budget(
            "unit",
            sdo_pipelines=one["sdo_pipelines"],
            codex_attempts=one["codex_attempts"],
            problems=problems,
        ).nominal_tokens

    estimate = estimate_of(plan)
    if estimate.fits(current_used_percent=current_used_percent, stop_percent=stop_percent):
        return ShrunkPlan(plan=plan, estimate=estimate, fits=True, shrunk=False)

    priority = sorted(("sdo_pipelines", "codex_attempts"), key=marginal_tokens, reverse=True)
    steps: list[str] = []
    for component in priority:
        floor = MATRIX_FLOORS[component]
        while plan[component] > floor:
            plan[component] -= 1
            steps.append(f"reduced {component} to {plan[component]}")
            estimate = estimate_of(plan)
            if estimate.fits(current_used_percent=current_used_percent, stop_percent=stop_percent):
                return ShrunkPlan(plan=plan, estimate=estimate, fits=True, shrunk=True, steps=steps)

    # Every component is at its floor and it still does not fit.
    return ShrunkPlan(plan=plan, estimate=estimate, fits=False, shrunk=True, steps=steps)
