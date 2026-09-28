"""Analyze the phase-1 live assurance matrix (``experiments/assurance/PLAN.md`` (a)).

Phase 1 has no stock (no-verify) Codex arm (user decision, 2026-09-28,
PLAN.md decisions log): its sole Codex arm is the default, concise-verify
baseline. Every claim below compares SDO against that one arm. Where the
paper's own comparisons were against a stock baseline, the claim's summary
notes that phase 1 has no stock arm to compare against.

One command, after the matrix finishes:

1. classifies every run with :mod:`benchmarks.sregym.analysis.run_validity`;
2. runs :mod:`benchmarks.sregym.analysis.incident_cost` per SDO pipeline
   against the Codex arm, over the valid runs only;
3. computes each PLAN.md claim (C1-C11) against its pre-registered pass
   criterion, with the CI method the plan names for it (a stratified
   bootstrap for ratios, Wilson for a proportion, Newcombe for a difference
   of proportions);
4. writes a report skeleton with a mandatory Takeaways section (meaning,
   confidence, implication, next step) for every claim, including the ones
   this run's data cannot answer (C6, C9's no-LLM fastloop replays, C7 and
   C10's phase-2-only scope, and the false-closure and decoy-citation checks
   that need controller/trace evidence beyond what
   :mod:`benchmarks.sregym.analysis.incident_cost` exposes) -- those are
   reported as ``insufficient_data`` or ``deferred``, never silently
   dropped.

Usage::

    uv run python -m benchmarks.sregym.assurance.phase1_analyze \\
        --sdo third_party/sregym/logs/<pipeline_a> ... \\
        --codex third_party/sregym/logs/<w4> third_party/sregym/logs/<w5> \\
                third_party/sregym/logs/<w6> third_party/sregym/logs/<w7> \\
        --out report.md [--json report.json] [--legacy-runs]
"""

from __future__ import annotations

import argparse
import io
import sys
from contextlib import redirect_stdout
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal, TypeVar

from agentshim.core.usage import TokenWeights

from benchmarks.sregym.analysis.incident_cost import (
    CodexRun,
    SdoStage,
    build_report,
    load_codex_runs,
    load_sdo_pipeline,
    render,
)
from benchmarks.sregym.analysis.run_validity import ValidityPolicy, validity_by_results_dir
from benchmarks.sregym.analysis.stats import (
    stratified_bootstrap_ratio,
    wilson_interval,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

#: PLAN.md (a) "Tokens ... cache_read = 0.1, output = 8, in base-input units".
PLAN_TOKEN_WEIGHTS = TokenWeights(uncached_input=1.0, cache_read_input=0.1, cache_write_input=1.0, output=8.0)

ClaimVerdict = Literal["pass", "directional", "fail", "insufficient_data", "deferred"]

#: PLAN.md (b) phase-1 problem set (K3 and V1 and S4 are phase 2 only, D10/D-table).
PHASE1_PROBLEMS = (
    "missing_configmap_hotel_reservation",
    "wrong_service_selector_hotel_reservation",
    "network_policy_block",
    "composite_policy_and_rate_configmap_hotel_reservation",
    "composite_frontend_selector_and_readiness_hotel_reservation",
)
COMPOSITE_PROBLEMS = (
    "composite_policy_and_rate_configmap_hotel_reservation",
    "composite_frontend_selector_and_readiness_hotel_reservation",
)

#: User decision (2026-09-28, PLAN.md decisions log): phase 1 has no stock
#: (no-verify) Codex arm. The paper's own C1-C4 and C8 comparisons were
#: against a stock baseline; every claim here compares against the default,
#: concise-verify arm instead, and says so.
NO_STOCK_ARM_NOTE = (
    "Phase 1 has no stock (no-verify) Codex arm to compare against for paper comparability: the default "
    "baseline verifies its own work, so this comparison already shows whether SDO's win reflects memory and "
    "learned detectors rather than a missing verification instruction."
)


@dataclass(frozen=True)
class ClaimReport:
    claim: str
    title: str
    pass_criterion: str
    summary: str
    verdict: ClaimVerdict
    takeaways: str

    def render(self) -> str:
        return (
            f"### {self.claim}: {self.title}\n\n"
            f"**Pre-registered pass criterion:** {self.pass_criterion}\n\n"
            f"**Result:** {self.summary}\n\n"
            f"**Verdict:** `{self.verdict}`\n\n"
            f"**Takeaways**\n\n{self.takeaways}\n"
        )


# --------------------------------------------------------------------------- grouping helpers


def split_rounds(stages: Sequence[SdoStage]) -> tuple[list[SdoStage], list[SdoStage]]:
    """PLAN.md (d): stage 0-4 are round 1 (cold), stage 5-9 repeat them (warm).

    An empty *stages* is a legitimate degenerate case (every stage of this
    pipeline was excluded by run_validity) and returns two empty lists; an
    odd count is a real corruption signal and raises.
    """

    if len(stages) % 2 != 0:
        raise ValueError(f"expected an even number of stages (5+5 rounds), got {len(stages)}")
    half = len(stages) // 2
    return list(stages[:half]), list(stages[half:])


_Run = TypeVar("_Run", SdoStage, CodexRun)


def by_problem(items: Iterable[_Run]) -> dict[str, list[_Run]]:
    groups: dict[str, list[_Run]] = {}
    for item in items:
        groups.setdefault(item.problem_id, []).append(item)
    return groups


def ttm_values(items: Iterable[SdoStage | CodexRun]) -> list[float]:
    return [item.verdict.ttm_seconds for item in items if item.verdict.ttm_seconds is not None]


def ttd_values(items: Iterable[SdoStage | CodexRun]) -> list[float]:
    return [item.verdict.diagnosis_seconds for item in items if item.verdict.diagnosis_seconds is not None]


def success_counts(items: Iterable[SdoStage | CodexRun]) -> tuple[int, int]:
    items = list(items)
    return sum(1 for item in items if item.verdict.passed), len(items)


def weighted_incident_tokens(stage: SdoStage) -> float:
    """A warm incident's cost including any reflection it triggers (PLAN.md C2)."""

    return stage.responder.weighted(PLAN_TOKEN_WEIGHTS) + stage.reflection.weighted(PLAN_TOKEN_WEIGHTS)


def _verdict_from_threshold(
    point: float | None, ci_bound: float | None, *, point_ok: bool, ci_ok: bool | None
) -> ClaimVerdict:
    """PLAN.md (a): pass needs point and CI bound both meeting the threshold; else directional; else fail."""

    if point is None:
        return "insufficient_data"
    if not point_ok:
        return "fail"
    if ci_bound is not None and ci_ok:
        return "pass"
    return "directional"


def _ratio_cells(
    numerator_by_problem: Mapping[str, Sequence[SdoStage | CodexRun]],
    denominator_by_problem: Mapping[str, Sequence[SdoStage | CodexRun]],
    problems: Sequence[str],
) -> dict[str, tuple[list[float], list[float]]]:
    return {
        problem: (
            ttm_values(numerator_by_problem.get(problem, [])),
            ttm_values(denominator_by_problem.get(problem, [])),
        )
        for problem in problems
    }


# --------------------------------------------------------------------------- claims


def compute_c1(
    sdo_pipelines: Sequence[Sequence[SdoStage]],
    codex_runs: Sequence[CodexRun],
    *,
    arm_label: str = "Codex (concise verify)",
) -> ClaimReport:
    """C1: recurring faults resolve faster (TTM ratio, Codex-median / SDO-warm-median)."""

    warm_stages = [
        stage for stages in sdo_pipelines for stage in split_rounds(stages)[1] if stage.problem_id in PHASE1_PROBLEMS
    ]
    warm_by_problem = by_problem(warm_stages)
    codex_by_problem = by_problem([run for run in codex_runs if run.problem_id in PHASE1_PROBLEMS])
    cells = _ratio_cells(codex_by_problem, warm_by_problem, PHASE1_PROBLEMS)
    result = stratified_bootstrap_ratio(cells)

    if not result.known:
        return ClaimReport(
            "C1",
            f"recurring faults resolve faster than {arm_label}",
            "pooled ratio >= 2.0, bootstrap lower bound >= 1.5; per-problem ratio >= 1.5 on >= 4 of 5 problems",
            "insufficient data: no phase-1 problem has both a warm SDO repeat and a matching baseline run",
            "insufficient_data",
            "Meaning: cannot be assessed without both arms' TTM for at least one problem. Confidence: n/a. "
            "Implication: the live matrix has not produced usable data yet. Next step: rerun this analysis "
            "once the matrix has completed at least one SDO round-2 stage and the matching Codex arm.",
        )

    assert result.point is not None  # narrows for the type checker; `result.known` already guaranteed this above

    per_problem_pass = sum(1 for ratio in result.per_cell.values() if ratio is not None and ratio >= 1.5)
    per_problem_total = sum(1 for ratio in result.per_cell.values() if ratio is not None)
    per_problem_ok = per_problem_total > 0 and per_problem_pass >= 4
    pooled_point_ok = result.point >= 2.0
    pooled_ci_ok = result.ci_low is not None and result.ci_low >= 1.5
    verdict = _verdict_from_threshold(result.point, result.ci_low, point_ok=pooled_point_ok, ci_ok=pooled_ci_ok)
    if verdict == "pass" and not per_problem_ok:
        verdict = "directional"

    summary = (
        f"pooled TTM ratio ({arm_label} / SDO warm) = {result.point:.2f} "
        f"(95% CI [{result.ci_low:.2f}, {result.ci_high:.2f}], n={result.replicates} replicates); "
        f"per-problem ratio >= 1.5 on {per_problem_pass}/{per_problem_total} problems "
        f"({ {p: round(v, 2) if v is not None else None for p, v in result.per_cell.items()} })"
    )
    return ClaimReport(
        "C1",
        f"recurring faults resolve faster than {arm_label}",
        "pooled ratio >= 2.0, bootstrap lower bound >= 1.5; per-problem ratio >= 1.5 on >= 4 of 5 problems",
        summary,
        verdict,
        _takeaways_for_ratio(verdict, "SDO's warm repeats are faster than " + arm_label, "TTM"),
    )


def compute_c2(sdo_pipelines: Sequence[Sequence[SdoStage]], codex_runs: Sequence[CodexRun]) -> ClaimReport:
    """C2: recurring faults use fewer tokens (weighted, SDO-warm / Codex (concise verify))."""

    warm_by_problem: dict[str, list[float]] = {}
    for stages in sdo_pipelines:
        for stage in split_rounds(stages)[1]:
            if stage.problem_id in PHASE1_PROBLEMS:
                warm_by_problem.setdefault(stage.problem_id, []).append(weighted_incident_tokens(stage))
    codex_by_problem: dict[str, list[float]] = {}
    for run in codex_runs:
        if run.problem_id in PHASE1_PROBLEMS:
            codex_by_problem.setdefault(run.problem_id, []).append(run.tokens.weighted(PLAN_TOKEN_WEIGHTS))

    cells = {
        problem: (warm_by_problem.get(problem, []), codex_by_problem.get(problem, [])) for problem in PHASE1_PROBLEMS
    }
    result = stratified_bootstrap_ratio(cells)
    if not result.known:
        return ClaimReport(
            "C2",
            "recurring faults use fewer weighted tokens than Codex (concise verify)",
            "direction: ratio <= 0.8, upper bound < 1.0; paper magnitude: ratio <= 0.4",
            "insufficient data",
            "insufficient_data",
            "Meaning: no problem has both a warm SDO token total and a Codex token total. Confidence: n/a. "
            "Implication: token accounting cannot be checked yet. Next step: rerun once both arms have data.",
        )
    assert result.point is not None  # narrows for the type checker; `result.known` already guaranteed this above
    direction_ok = _verdict_from_threshold(
        result.point,
        result.ci_high,
        point_ok=result.point <= 0.8,
        ci_ok=result.ci_high is not None and result.ci_high < 1.0,
    )
    magnitude_pass = result.point <= 0.4
    verdict: ClaimVerdict = direction_ok
    summary = (
        f"pooled weighted-token ratio (SDO warm / Codex (concise verify)) = {result.point:.3f} "
        f"(95% CI [{result.ci_low:.3f}, {result.ci_high:.3f}]); paper magnitude (<=0.4) "
        f"{'met' if magnitude_pass else 'not met'}. {NO_STOCK_ARM_NOTE}"
    )
    return ClaimReport(
        "C2",
        "recurring faults use fewer weighted tokens than Codex (concise verify)",
        "direction: ratio <= 0.8, upper bound < 1.0; paper magnitude: ratio <= 0.4 (expected to fail per Step 3)",
        summary,
        verdict,
        _takeaways_for_ratio(verdict, "SDO's warm repeats use fewer weighted tokens", "token"),
    )


def compute_c3(sdo_pipelines: Sequence[Sequence[SdoStage]], codex_runs: Sequence[CodexRun]) -> ClaimReport:
    """C3: novel faults are not degraded (round-1/cold SDO vs Codex (concise verify))."""

    cold_stages = [
        stage for stages in sdo_pipelines for stage in split_rounds(stages)[0] if stage.problem_id in PHASE1_PROBLEMS
    ]
    cold_by_problem = by_problem(cold_stages)
    codex_by_problem = by_problem([run for run in codex_runs if run.problem_id in PHASE1_PROBLEMS])

    time_cells = _ratio_cells(cold_by_problem, codex_by_problem, PHASE1_PROBLEMS)
    time_result = stratified_bootstrap_ratio(time_cells)

    sdo_passed, sdo_total = success_counts(cold_stages)
    codex_passed, codex_total = success_counts(run for run in codex_runs if run.problem_id in PHASE1_PROBLEMS)

    token_cells = {
        problem: (
            [stage.responder.weighted(PLAN_TOKEN_WEIGHTS) for stage in cold_by_problem.get(problem, [])],
            [run.tokens.weighted(PLAN_TOKEN_WEIGHTS) for run in codex_by_problem.get(problem, [])],
        )
        for problem in PHASE1_PROBLEMS
    }
    token_result = stratified_bootstrap_ratio(token_cells)

    if not time_result.known or sdo_total == 0 or codex_total == 0:
        return ClaimReport(
            "C3",
            "novel faults are not degraded",
            "time ratio <= 1.25 (upper <= 1.6); accuracy diff >= -0.10 (Newcombe lower > -0.30); "
            "responder-token ratio <= 1.25",
            "insufficient data",
            "insufficient_data",
            "Meaning: cannot assess cold-path parity without both arms present. Confidence: n/a. "
            "Implication: none yet. Next step: rerun once round-1 SDO stages and Codex attempts exist.",
        )

    assert time_result.point is not None  # narrows for the type checker; the `time_result.known` guard above did this
    time_verdict = _verdict_from_threshold(
        time_result.point,
        time_result.ci_high,
        point_ok=time_result.point <= 1.25,
        ci_ok=time_result.ci_high is not None and time_result.ci_high <= 1.6,
    )
    from benchmarks.sregym.analysis.stats import newcombe_interval

    accuracy = newcombe_interval(sdo_passed, sdo_total, codex_passed, codex_total)
    accuracy_verdict = _verdict_from_threshold(
        accuracy.point, accuracy.low, point_ok=accuracy.point >= -0.10, ci_ok=accuracy.low > -0.30
    )
    token_verdict: ClaimVerdict = "insufficient_data"
    if token_result.known:
        assert token_result.point is not None  # narrows for the type checker; the `known` check above guaranteed this
        token_verdict = "pass" if token_result.point <= 1.25 else "fail"

    verdicts = [time_verdict, accuracy_verdict, token_verdict]
    if all(v == "pass" for v in verdicts):
        overall: ClaimVerdict = "pass"
    elif any(v == "fail" for v in verdicts):
        overall = "fail" if "insufficient_data" not in verdicts else "insufficient_data"
    elif "insufficient_data" in verdicts:
        overall = "insufficient_data"
    else:
        overall = "directional"

    summary = (
        f"time ratio (SDO cold / Codex (concise verify)) = {time_result.point:.2f} "
        f"(CI [{time_result.ci_low:.2f}, {time_result.ci_high:.2f}]) -> {time_verdict}; "
        f"e2e success diff (SDO - Codex) = {accuracy.point:.2f} "
        f"(Newcombe CI [{accuracy.low:.2f}, {accuracy.high:.2f}]) -> {accuracy_verdict}; "
        f"responder-token ratio = {token_result.point if token_result.known else 'n/a'} -> {token_verdict} "
        f"(a token fail here reproduces Step 3's ~1.9x and is a known, expected gap). {NO_STOCK_ARM_NOTE}"
    )
    return ClaimReport(
        "C3",
        "novel faults are not degraded",
        "time ratio <= 1.25 (upper <= 1.6); accuracy diff >= -0.10 (Newcombe lower > -0.30); "
        "responder-token ratio <= 1.25 (expected to fail, per Step 3)",
        summary,
        overall,
        _takeaways_for_ratio(overall, "SDO's cold-path performance matches memoryless Codex", "time/accuracy/token"),
    )


def compute_c4(sdo_stages: Sequence[SdoStage], codex_runs: Sequence[CodexRun]) -> ClaimReport:
    """C4: SDO solve rate >= memoryless (end-to-end success, Wilson; false closures deferred).

    Phase 1 has no stock arm, so this compares SDO against the default,
    concise-verify Codex arm only, at the verify tolerance (SDO can trail by
    up to 0.05 on the point estimate): the paper's stricter stock comparison
    (SDO - Codex-stock >= 0) is not available here (``NO_STOCK_ARM_NOTE``).
    """

    sdo_passed, sdo_total = success_counts(sdo_stages)
    if sdo_total == 0:
        return ClaimReport(
            "C4",
            "SDO solve rate >= memoryless Codex",
            "SDO e2e >= 0.90 (Wilson lower >= 0.75); SDO >= Codex (concise verify) - 0.05; "
            "zero SDO false closures (hard)",
            "insufficient data: no SDO stages",
            "insufficient_data",
            "Meaning: no data. Confidence: n/a. Implication: none yet. Next step: rerun after the matrix runs.",
        )
    sdo_wilson = wilson_interval(sdo_passed, sdo_total)
    sdo_verdict = _verdict_from_threshold(
        sdo_wilson.point, sdo_wilson.low, point_ok=sdo_wilson.point >= 0.90, ci_ok=sdo_wilson.low >= 0.75
    )

    codex_passed, codex_total = success_counts(codex_runs)
    codex_diff = None
    if codex_total:
        from benchmarks.sregym.analysis.stats import newcombe_interval

        codex_diff = newcombe_interval(sdo_passed, sdo_total, codex_passed, codex_total)

    codex_ok = codex_diff is not None and codex_diff.point >= -0.05

    overall: ClaimVerdict = sdo_verdict
    if codex_total and not codex_ok:
        overall = "fail"

    summary = (
        f"SDO e2e = {sdo_wilson.point:.2f} (Wilson [{sdo_wilson.low:.2f}, {sdo_wilson.high:.2f}], n={sdo_total}); "
        f"SDO - Codex (concise verify) = {codex_diff.point if codex_diff else 'n/a'}. {NO_STOCK_ARM_NOTE} "
        "False-closure count is NOT computed here: it needs the controller's closure/receipt evidence "
        "(sdo.dev/responder-helper, BrokerClosure), which is outside incident_cost's SdoStage/Verdict model; "
        "check it separately against run_validity's own checks before reporting this claim as a pass."
    )
    return ClaimReport(
        "C4",
        "SDO solve rate >= memoryless Codex",
        "SDO e2e >= 0.90 (Wilson lower >= 0.75); SDO >= Codex (concise verify) - 0.05; "
        "zero SDO false closures (hard, not computed by this tool)",
        summary,
        overall,
        _takeaways_for_ratio(overall, "SDO's end-to-end solve rate", "success"),
    )


def compute_c5(sdo_pipelines: Sequence[Sequence[SdoStage]]) -> ClaimReport:
    """C5: learning curve (round-2/round-1 TTM ratio pooled; memory growth after round 1)."""

    round1_by_problem: dict[str, list[float]] = {}
    round2_by_problem: dict[str, list[float]] = {}
    memory_checks: list[bool] = []
    for stages in sdo_pipelines:
        round1, round2 = split_rounds(stages)
        for stage in round1:
            if stage.problem_id in PHASE1_PROBLEMS and stage.verdict.ttm_seconds is not None:
                round1_by_problem.setdefault(stage.problem_id, []).append(stage.verdict.ttm_seconds)
        for stage in round2:
            if stage.problem_id in PHASE1_PROBLEMS and stage.verdict.ttm_seconds is not None:
                round2_by_problem.setdefault(stage.problem_id, []).append(stage.verdict.ttm_seconds)
        last_round1_memory = round1[-1].memory if round1 else None
        if last_round1_memory is not None:
            memory_checks.append(
                last_round1_memory.incident_detectors >= 1
                and last_round1_memory.incident_detectors <= last_round1_memory.playbooks
            )

    # Ratio numerator=round2 (warm), denominator=round1 (cold): "pooled ratio <= 0.5".
    cells = {
        problem: (round2_by_problem.get(problem, []), round1_by_problem.get(problem, [])) for problem in PHASE1_PROBLEMS
    }
    result = stratified_bootstrap_ratio(cells)
    if not result.known:
        return ClaimReport(
            "C5",
            "learning curve: rolling TTM falls as memory accumulates",
            "SDO pooled round2/round1 ratio <= 0.5 (upper <= 0.7); every lane has >= 1 playbook and "
            ">= 1 incident detector per root-cause class after round 1, detectors <= playbooks",
            "insufficient data",
            "insufficient_data",
            "Meaning: no data. Confidence: n/a. Implication: none yet. Next step: rerun after round 1 and 2 complete.",
        )
    assert result.point is not None  # narrows for the type checker; `result.known` already guaranteed this above
    ratio_verdict = _verdict_from_threshold(
        result.point,
        result.ci_high,
        point_ok=result.point <= 0.5,
        ci_ok=result.ci_high is not None and result.ci_high <= 0.7,
    )
    memory_ok = bool(memory_checks) and all(memory_checks)
    overall: ClaimVerdict = ratio_verdict if memory_ok else "fail" if ratio_verdict == "pass" else ratio_verdict
    summary = (
        f"pooled round2/round1 TTM ratio = {result.point:.2f} (CI [{result.ci_low:.2f}, {result.ci_high:.2f}]); "
        f"memory-growth check ({len(memory_checks)} lanes) = {'ok' if memory_ok else 'not satisfied or not recorded'}"
    )
    return ClaimReport(
        "C5",
        "learning curve: rolling TTM falls as memory accumulates",
        "SDO pooled round2/round1 ratio <= 0.5 (upper <= 0.7); every lane has >= 1 playbook and "
        ">= 1 incident detector per root-cause class after round 1, detectors <= playbooks",
        summary,
        overall,
        _takeaways_for_ratio(overall, "SDO's round-2 repeats are faster than its own round-1 attempts", "TTM"),
    )


def compute_c8(sdo_stages: Sequence[SdoStage], codex_runs: Sequence[CodexRun]) -> ClaimReport:
    """C8: composite full-mitigation rate (K1, K2 stages; false closures and the gate check deferred)."""

    composite_stages = [stage for stage in sdo_stages if stage.problem_id in COMPOSITE_PROBLEMS]
    composite_codex = [run for run in codex_runs if run.problem_id in COMPOSITE_PROBLEMS]
    sdo_passed, sdo_total = success_counts(composite_stages)
    if sdo_total == 0:
        return ClaimReport(
            "C8",
            "composite incidents are fully mitigated",
            "SDO full-mitigation >= 0.75 (Wilson) and >= Codex (concise verify) point estimate; SDO false "
            "closures = 0; scripted partial-fix gate 3/3 per composite",
            "insufficient data: no composite SDO stages",
            "insufficient_data",
            "Meaning: no data. Confidence: n/a. Implication: none yet. Next step: rerun after K1/K2 stages complete.",
        )
    sdo_wilson = wilson_interval(sdo_passed, sdo_total)
    codex_passed, codex_total = success_counts(composite_codex)
    codex_rate = codex_passed / codex_total if codex_total else None
    rate_verdict = _verdict_from_threshold(
        sdo_wilson.point, sdo_wilson.low, point_ok=sdo_wilson.point >= 0.75, ci_ok=sdo_wilson.low >= 0.75
    )
    beats_codex = codex_rate is None or sdo_wilson.point >= codex_rate
    overall: ClaimVerdict = rate_verdict if beats_codex else "fail"
    summary = (
        f"SDO full-mitigation rate = {sdo_wilson.point:.2f} (Wilson [{sdo_wilson.low:.2f}, {sdo_wilson.high:.2f}], "
        f"n={sdo_total}); Codex (concise verify) rate = {codex_rate if codex_rate is not None else 'n/a'}. "
        f"{NO_STOCK_ARM_NOTE} "
        "False closures and the scripted partial-fix gate (3/3 unhealthy, then 3/3 cleared) are NOT computed here: "
        "the gate is a no-LLM fastloop check (PLAN.md (e)), already exercised in QUALIFICATION.md/RC1.md before "
        "this live matrix; false closures need controller closure evidence outside SdoStage/Verdict."
    )
    return ClaimReport(
        "C8",
        "composite incidents are fully mitigated",
        "SDO full-mitigation >= 0.75 (Wilson) and >= Codex (concise verify) point estimate; SDO false closures = 0 "
        "(not computed here); scripted partial-fix gate 3/3 per composite (fastloop, not computed here)",
        summary,
        overall,
        _takeaways_for_ratio(overall, "SDO fully mitigates composite incidents", "success"),
    )


def compute_c11_verify_ratio(
    sdo_pipelines: Sequence[Sequence[SdoStage]], codex_runs: Sequence[CodexRun]
) -> ClaimReport:
    """C11 (partial): SDO's warm advantage over Codex (concise verify) (decoy-citation scan deferred).

    Phase 1's sole Codex arm already is the concise-verify baseline (user
    decision, 2026-09-28), so this claim's comparison is exactly C1's; C11 is
    the claim that reads it as a memory-safety result: a high ratio here
    shows SDO's speedup comes from memory and learned detectors, not from
    verification alone (the baseline already verifies its own work).
    """

    report = compute_c1(sdo_pipelines, codex_runs, arm_label="Codex (concise verify)")
    summary = (
        f"{report.summary} Decoy-driven-failure counting (a trace showing a decoy acted on as the cause) is NOT "
        "computed here: it needs a diagnosis-text scan against the known decoy markers "
        "(failure-admin-*, benign_env_drift/LOG_LEVEL), which is outside incident_cost's data model. "
        "K3 (the misleading-drift composite) is phase-2 only (D10), so it does not appear in this phase-1 analysis."
    )
    return ClaimReport(
        "C11",
        "memory is safe (partial: SDO vs Codex (concise verify))",
        "SDO decoy-driven failures = 0 (not computed here); C1-style ratio against Codex (concise verify) >= 2.0",
        summary,
        report.verdict,
        "Meaning: the ratio component shows whether SDO's speedup is memory, not only the verify loop -- and "
        "phase 1's baseline already verifies concisely by default, so a high ratio here directly supports that "
        "SDO's win is memory and learned detectors, not a missing verification instruction. "
        "Confidence: only as strong as C1's own bootstrap. Implication: a low ratio here would suggest verify "
        "alone explains most of the gain. Next step: add a decoy-citation scanner over diagnosis text before "
        "reporting the zero-false-positive half of this claim.",
    )


def _deferred(claim: str, title: str, pass_criterion: str, reason: str) -> ClaimReport:
    return ClaimReport(
        claim,
        title,
        pass_criterion,
        f"deferred: {reason}",
        "deferred",
        f"Meaning: {reason} Confidence: n/a (not attempted in phase 1). "
        "Implication: none for this run. Next step: run the dedicated instrument this claim needs and re-analyze.",
    )


def _takeaways_for_ratio(verdict: ClaimVerdict, subject: str, metric: str) -> str:
    if verdict == "pass":
        meaning = f"{subject}, and the effect clears its pre-registered bound."
        confidence = "high for this sample; single phase-1 run, so treat as one data point pending replication."
    elif verdict == "directional":
        meaning = f"{subject} on the point estimate, but the confidence interval does not clear the bound."
        confidence = "moderate: the direction is right, the magnitude is not yet nailed down at this n."
    elif verdict == "fail":
        meaning = f"the data does not show that {subject.lower()}."
        confidence = "the point estimate itself misses the pre-registered threshold."
    else:
        meaning = "not enough data to say."
        confidence = "n/a."
    return (
        f"Meaning: {meaning} Confidence: {confidence} "
        f"Implication: treat this as input to the phase-2 go/no-go, not a final claim about the paper's result. "
        f"Next step: replicate at phase-2 scale before reporting {metric} numbers externally."
    )


def compute_all_claims(
    sdo_pipelines: Sequence[Sequence[SdoStage]],
    codex_runs: Sequence[CodexRun],
) -> list[ClaimReport]:
    """Every claim compares SDO against ``codex_runs``: phase 1's sole Codex arm, the default concise-verify
    baseline (user decision, 2026-09-28; ``NO_STOCK_ARM_NOTE``).
    """

    sdo_stages_flat = [stage for stages in sdo_pipelines for stage in stages]
    return [
        compute_c1(sdo_pipelines, codex_runs),
        compute_c2(sdo_pipelines, codex_runs),
        compute_c3(sdo_pipelines, codex_runs),
        compute_c4(sdo_stages_flat, codex_runs),
        compute_c5(sdo_pipelines),
        _deferred(
            "C6",
            "detectors retrieve cheaply and accurately",
            "recall >= 0.73, precision >= 0.48, FP <= 0.30, median latency <= 20s, retrieval tokens = 0",
            "this is a no-LLM fastloop replay (PLAN.md (e)), a separate instrument from the live matrix this "
            "tool analyzes; run the frozen-memory replay and feed its output to a dedicated C6 scorer.",
        ),
        _deferred(
            "C7",
            "memory transfers to recurrences with different parameter bindings",
            "ratio >= 1.5 vs Codex; family detector fires in >= 3 of 4 pipelines",
            "the primary check (variant V1) is phase-2 only (PLAN.md (b)); phase 1 only has K1's partial "
            "rate-ConfigMap variant, which this tool does not isolate from the rest of K1's composite verdict.",
        ),
        compute_c8(sdo_stages_flat, codex_runs),
        _deferred(
            "C9",
            "token cost scales with incidents, not operating time",
            "0 tokens, 0 dispatches in each of 3 lanes over a 60-minute healthy soak",
            "this is a no-LLM fastloop soak (PLAN.md (e)), a separate instrument from the live matrix this "
            "tool analyzes; run the soak and feed its dispatch/token log to a dedicated C9 scorer.",
        ),
        _deferred(
            "C10",
            "deployment from source is reliable with the independent health judge",
            "3/3 fresh hotel-reservation lifecycles succeed",
            "phase-1's SDO lanes seed from an existing lifecycle workspace (PLAN.md (d) stage 0) and spend no "
            "lifecycle quota; C10 is phase-2 only (PLAN.md (b)).",
        ),
        compute_c11_verify_ratio(sdo_pipelines, codex_runs),
    ]


# --------------------------------------------------------------------------- CLI / report assembly


@dataclass(frozen=True)
class PhaseOneAnalysis:
    validity_summary: str
    incident_cost_reports: list[str]
    claims: list[ClaimReport]

    def render(self) -> str:
        parts = [
            "# Phase-1 assurance matrix: analysis report\n",
            "## Run validity\n",
            self.validity_summary,
            "\n## Per-pipeline incident_cost\n",
        ]
        parts.extend(f"```\n{report}\n```\n" for report in self.incident_cost_reports)
        parts.append("\n## Claims (C1-C11)\n")
        parts.extend(claim.render() for claim in self.claims)
        return "\n".join(parts)


def analyze(
    sdo_pipeline_dirs: Sequence[Path],
    codex_dirs: Sequence[Path],
    *,
    legacy: bool = False,
) -> PhaseOneAnalysis:
    """*codex_dirs* are phase-1's sole Codex arm's run directories: the default, concise-verify baseline."""

    policy = ValidityPolicy(legacy=legacy)
    all_dirs = [*sdo_pipeline_dirs, *codex_dirs]
    validity = validity_by_results_dir(all_dirs, policy=policy)
    excluded = [f"{source}: {'; '.join(result.reasons)}" for source, result in validity.items() if result.excluded]
    valid_count = sum(1 for result in validity.values() if not result.excluded)
    validity_summary = (
        f"{valid_count} valid problem run(s), {len(excluded)} excluded.\n\n"
        + "\n".join(f"- {line}" for line in excluded)
        if excluded
        else f"{valid_count} valid problem run(s); none excluded."
    )

    def keep_stage(stage: SdoStage) -> bool:
        result = validity.get(stage.source)
        return result is None or not result.excluded

    def keep_run(run: CodexRun) -> bool:
        result = validity.get(run.source)
        return result is None or not result.excluded

    sdo_pipelines = [
        [stage for stage in load_sdo_pipeline(pipeline_dir) if keep_stage(stage)] for pipeline_dir in sdo_pipeline_dirs
    ]
    codex_runs = [run for d in codex_dirs for run in load_codex_runs(d) if keep_run(run)]

    incident_cost_reports: list[str] = []
    for pipeline_dir, stages in zip(sdo_pipeline_dirs, sdo_pipelines, strict=True):
        if not stages:
            continue
        report = build_report(stages, codex_runs)
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            print(f"# {pipeline_dir.name}")
            print(render(report))
        incident_cost_reports.append(buffer.getvalue())

    claims = compute_all_claims(sdo_pipelines, codex_runs)
    return PhaseOneAnalysis(
        validity_summary=validity_summary, incident_cost_reports=incident_cost_reports, claims=claims
    )


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n", 1)[0])
    parser.add_argument("--sdo", type=Path, nargs="+", required=True, help="SDO pipeline directories (one per lane)")
    parser.add_argument(
        "--codex",
        type=Path,
        nargs="+",
        default=[],
        help="Codex (concise verify) run directories: phase 1's sole Codex arm",
    )
    parser.add_argument("--legacy-runs", action="store_true", help="run_validity --legacy (pre-manifest runs)")
    parser.add_argument("--out", type=Path, help="write the Markdown report here (also printed to stdout)")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    analysis = analyze(args.sdo, args.codex, legacy=args.legacy_runs)
    rendered = analysis.render()
    print(rendered)
    if args.out:
        args.out.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
