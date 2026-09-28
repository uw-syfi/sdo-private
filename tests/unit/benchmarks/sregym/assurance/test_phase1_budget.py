from __future__ import annotations

import pytest

from benchmarks.sregym.assurance.phase1_budget import (
    CODEX_ATTEMPT_TOKENS,
    FULL_MATRIX,
    PHASE1_PROBLEMS,
    POINTS_PER_TOKEN,
    SDO_LIFECYCLE_BOOTSTRAP_TOKENS,
    SDO_STAGE_TOKENS,
    TokenBudgetEstimate,
    autoshrink_to_fit,
    codex_arm_tokens,
    full_matrix_budget,
    matrix_budget,
    reduced_matrix_budget,
    sdo_pipeline_tokens,
    smoke_budget,
)

S1, S2, S3, K1, K2 = PHASE1_PROBLEMS


def test_sdo_pipeline_tokens_weights_composites_at_one_point_five_times_and_adds_bootstrap_once() -> None:
    singles_only = sdo_pipeline_tokens([S1, S2, S3], rounds=1, include_bootstrap=False)
    assert singles_only == pytest.approx(3 * SDO_STAGE_TOKENS)
    with_composite = sdo_pipeline_tokens([S1, K1], rounds=1, include_bootstrap=False)
    assert with_composite == pytest.approx(SDO_STAGE_TOKENS + SDO_STAGE_TOKENS * 1.5)
    with_bootstrap = sdo_pipeline_tokens([S1], rounds=1, include_bootstrap=True)
    assert with_bootstrap == pytest.approx(SDO_STAGE_TOKENS + SDO_LIFECYCLE_BOOTSTRAP_TOKENS)


def test_sdo_pipeline_tokens_multiplies_stages_by_the_round_count_but_not_the_bootstrap() -> None:
    one_round = sdo_pipeline_tokens(PHASE1_PROBLEMS, rounds=1, include_bootstrap=True)
    two_rounds = sdo_pipeline_tokens(PHASE1_PROBLEMS, rounds=2, include_bootstrap=True)
    stage_only = two_rounds - one_round
    assert stage_only == pytest.approx(one_round - SDO_LIFECYCLE_BOOTSTRAP_TOKENS)


def test_codex_arm_tokens_applies_the_verify_multiplier() -> None:
    # Phase 1 has no stock arm (2026-09-28): codex_arm_tokens is always the
    # concise-verify estimate, built from the measured stock constant x 1.3.
    two_attempts = codex_arm_tokens(PHASE1_PROBLEMS, 2)
    one_attempt = codex_arm_tokens(PHASE1_PROBLEMS, 1)
    assert two_attempts == pytest.approx(one_attempt * 2)
    assert one_attempt == pytest.approx(
        sum((CODEX_ATTEMPT_TOKENS * 1.5 if p in (K1, K2) else CODEX_ATTEMPT_TOKENS) * 1.3 for p in PHASE1_PROBLEMS)
    )


def test_codex_arm_tokens_of_zero_attempts_is_zero_not_an_error() -> None:
    assert codex_arm_tokens(PHASE1_PROBLEMS, 0) == 0.0


def test_codex_arm_tokens_rejects_negative_attempts() -> None:
    with pytest.raises(ValueError, match="attempts_per_problem"):
        codex_arm_tokens(PHASE1_PROBLEMS, -1)


def test_token_budget_estimate_converts_tokens_to_points_at_the_fallback_rate() -> None:
    estimate = TokenBudgetEstimate("x", {"a": 10_000_000.0})
    assert estimate.nominal_percent == pytest.approx(10_000_000.0 * POINTS_PER_TOKEN)
    assert estimate.worst_case_tokens == pytest.approx(20_000_000.0)
    assert estimate.worst_case_percent == pytest.approx(20_000_000.0 * POINTS_PER_TOKEN)


def test_token_budget_estimate_rejects_an_empty_or_negative_breakdown() -> None:
    with pytest.raises(ValueError, match="at least one component"):
        TokenBudgetEstimate("x", {})
    with pytest.raises(ValueError, match="non-negative"):
        TokenBudgetEstimate("x", {"a": -1.0})


def test_token_budget_estimate_fits_checks_current_plus_nominal_against_the_stop_line() -> None:
    # 2026-10 gate change: fits() gates on EXPECTED (nominal) cost, not the 2x
    # worst case. nominal = 30_000_000 * 1e-7 = 3.0 points.
    estimate = TokenBudgetEstimate("x", {"a": 30_000_000.0})
    assert estimate.fits(current_used_percent=93.0, stop_percent=96.0)
    assert not estimate.fits(current_used_percent=93.01, stop_percent=96.0)


def test_full_matrix_budget_matches_plan_md_composition() -> None:
    estimate = full_matrix_budget()
    assert set(estimate.breakdown) == {"sdo_4_pipelines", "codex"}
    assert estimate.breakdown["sdo_4_pipelines"] == pytest.approx(sdo_pipeline_tokens(PHASE1_PROBLEMS) * 4)
    assert estimate.breakdown["codex"] == pytest.approx(codex_arm_tokens(PHASE1_PROBLEMS, 5))


def test_reduced_matrix_budget_is_the_2026_10_pivot_one_preset() -> None:
    estimate = reduced_matrix_budget()
    assert estimate.breakdown["sdo_2_pipelines"] == pytest.approx(sdo_pipeline_tokens(PHASE1_PROBLEMS) * 2)
    assert estimate.breakdown["codex"] == pytest.approx(codex_arm_tokens(PHASE1_PROBLEMS, 3))
    assert estimate.nominal_tokens < full_matrix_budget().nominal_tokens


def test_smoke_budget_is_small_and_one_problem_only() -> None:
    estimate = smoke_budget()
    assert set(estimate.breakdown) == {"sdo_1_pipelines", "codex"}
    assert estimate.nominal_tokens < reduced_matrix_budget().nominal_tokens


def test_matrix_budget_rejects_negative_components() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        matrix_budget("x", sdo_pipelines=-1, codex_attempts=1)


def test_matrix_budget_rejects_an_all_zero_plan() -> None:
    with pytest.raises(ValueError, match="empty plan"):
        matrix_budget("x", sdo_pipelines=0, codex_attempts=0)


def test_the_full_matrix_nominal_cost_fits_80_percent_used_with_a_96_percent_stop() -> None:
    # 2026-10 gate change: gating on nominal (not 2x worst case) cost. The
    # no-stock full matrix's nominal cost (~6.1 pt) comfortably fits 80% used
    # under a 96% stop -- unlike the old two-arm worst-case gate, which did
    # not fit even at 90% and forced an autoshrink down to one SDO pipeline.
    estimate = full_matrix_budget()
    assert estimate.fits(current_used_percent=80.0, stop_percent=96.0)


def test_autoshrink_returns_the_full_matrix_unchanged_when_it_already_fits() -> None:
    result = autoshrink_to_fit(current_used_percent=10.0, stop_percent=96.0)
    assert result.shrunk is False
    assert result.fits is True
    assert result.plan == FULL_MATRIX


def test_autoshrink_does_not_need_to_shrink_at_80_percent_used_with_a_96_percent_stop() -> None:
    # With the no-stock matrix and the nominal (not worst-case) gate, 80%
    # used still leaves enough room for the full matrix.
    result = autoshrink_to_fit(current_used_percent=80.0, stop_percent=96.0)
    assert result.shrunk is False
    assert result.plan == FULL_MATRIX
    assert result.fits is True


def test_autoshrink_drains_the_most_expensive_component_first() -> None:
    # sdo pipelines are the single most expensive component (bootstrap + all
    # 5 problems x2 rounds), so it is drained toward its floor of 1 before
    # codex_attempts moves.
    result = autoshrink_to_fit(current_used_percent=92.0, stop_percent=96.0)
    assert result.shrunk is True
    assert result.plan["sdo_pipelines"] < FULL_MATRIX["sdo_pipelines"]
    assert result.plan["codex_attempts"] == FULL_MATRIX["codex_attempts"]
    assert result.fits is True


def test_autoshrink_reports_infeasible_when_even_the_floor_plan_does_not_fit() -> None:
    result = autoshrink_to_fit(current_used_percent=95.9999, stop_percent=96.0)
    assert result.fits is False
    assert result.plan == {"sdo_pipelines": 1, "codex_attempts": 1}


def test_autoshrink_explain_names_every_step_and_the_final_estimate() -> None:
    result = autoshrink_to_fit(current_used_percent=92.0, stop_percent=96.0)
    explanation = result.explain()
    assert "auto-shrunk" in explanation
    assert "reduced sdo_pipelines to" in explanation


def test_measured_constants_are_the_2026_09_28_evidence_not_plan_md_guesses() -> None:
    # A regression guard: these are cited by RUNBOOK.md/HARNESS_DECISIONS.md;
    # changing them silently would desync the documented calibration.
    assert pytest.approx(666_758.0) == SDO_STAGE_TOKENS
    assert pytest.approx(2_086_905.0) == SDO_LIFECYCLE_BOOTSTRAP_TOKENS
    assert pytest.approx(529_706.6666666666) == CODEX_ATTEMPT_TOKENS
    assert pytest.approx(1e-7) == POINTS_PER_TOKEN
