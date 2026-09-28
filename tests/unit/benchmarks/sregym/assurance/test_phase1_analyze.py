from __future__ import annotations

import csv
import json
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from benchmarks.sregym.analysis.incident_cost import CodexRun, MemorySize, SdoStage, TokenUsage, Verdict
from benchmarks.sregym.assurance.phase1_analyze import (
    PHASE1_PROBLEMS,
    analyze,
    compute_all_claims,
    compute_c1,
    compute_c4,
    compute_c5,
    compute_c8,
    compute_c11_verify_ratio,
    split_rounds,
    weighted_incident_tokens,
)

S1, S2, S3, K1, K2 = PHASE1_PROBLEMS


def _verdict(*, ttd: float | None = 10.0, ttm: float | None = 20.0, passed: bool = True) -> Verdict:
    return Verdict(
        diagnosis=passed,
        mitigation=passed,
        raw_incl_judge_seconds=ttm + 5 if ttm is not None else None,
        diagnosis_seconds=ttd,
        last_mitigation_seconds=ttm,
        grading_wait_seconds=0.0,
    )


def _stage(problem: str, *, index: int, ttm: float, passed: bool = True, memory: MemorySize | None = None) -> SdoStage:
    return SdoStage(
        index=index,
        name=f"stage-{index}",
        problem_id=problem,
        verdict=_verdict(ttm=ttm, passed=passed),
        incident_resolution_seconds=ttm,
        responder=TokenUsage(input_tokens=1000, cached_input_tokens=200, output_tokens=100),
        reflection=TokenUsage(),
        reflection_attempts=None,
        reflection_skipped_reason=None,
        learning_seconds=None,
        reflection_turn_seconds=None,
        warm_path=index >= 5,
        match_reasons=(),
        warm_prompt=None,
        memory=memory,
        lifecycle=TokenUsage(),
        source=f"pipeline/stage_{index}",
    )


def _pipeline(rotation: list[str], *, ttm_cold: float = 100.0, ttm_warm: float = 20.0) -> list[SdoStage]:
    memory = MemorySize(detectors=6, incident_detectors=5, playbooks=5)
    stages = [
        _stage(problem, index=i, ttm=ttm_cold, memory=memory if i == len(rotation) - 1 else None)
        for i, problem in enumerate(rotation)
    ]
    stages += [_stage(problem, index=i + len(rotation), ttm=ttm_warm) for i, problem in enumerate(rotation)]
    return stages


def _codex_run(
    problem: str, *, ttm: float, passed: bool = True, tokens: int = 300_000, source: str = "codex/run"
) -> CodexRun:
    return CodexRun(
        problem_id=problem,
        verdict=_verdict(ttm=ttm, passed=passed),
        tokens=TokenUsage(input_tokens=tokens, cached_input_tokens=tokens // 2, output_tokens=2000),
        source=source,
    )


ROTATIONS = {
    "a": [S1, S2, S3, K1, K2],
    "b": [S2, S3, K1, K2, S1],
    "c": [S3, K1, K2, S1, S2],
    "d": [K1, K2, S1, S2, S3],
}


def _all_pipelines(**kwargs) -> list[list[SdoStage]]:
    return [_pipeline(order, **kwargs) for order in ROTATIONS.values()]


def _codex_arm(problems: list[str], *, n: int = 5, ttm: float = 100.0, passed: bool = True) -> list[CodexRun]:
    return [
        _codex_run(problem, ttm=ttm, passed=passed, source=f"codex/{problem}-{i}")
        for problem in problems
        for i in range(n)
    ]


# --------------------------------------------------------------------------- unit tests


def test_split_rounds_divides_a_ten_stage_pipeline_evenly() -> None:
    pipeline = _pipeline(ROTATIONS["a"])
    cold, warm = split_rounds(pipeline)
    assert [s.problem_id for s in cold] == ROTATIONS["a"]
    assert [s.problem_id for s in warm] == ROTATIONS["a"]
    assert all(not s.warm_path for s in cold)
    assert all(s.warm_path for s in warm)


def test_split_rounds_rejects_an_odd_count_but_allows_empty() -> None:
    with pytest.raises(ValueError, match="even"):
        split_rounds(_pipeline(ROTATIONS["a"])[:-1])
    assert split_rounds([]) == ([], [])


def test_weighted_incident_tokens_includes_reflection() -> None:
    stage = _stage(S1, index=0, ttm=100.0)
    stage = SdoStage(**{**stage.__dict__, "reflection": TokenUsage(input_tokens=500, output_tokens=50)})
    weighted = weighted_incident_tokens(stage)
    # uncached input weight 1.0, output weight 8.0: responder (800 + 100*8) + reflection (500 + 50*8)
    assert weighted == pytest.approx((800 * 1.0 + 200 * 0.1 + 100 * 8.0) + (500 * 1.0 + 50 * 8.0))


# Phase 1 has no stock (no-verify) Codex arm (user decision, 2026-09-28): the
# sole Codex arm below stands in for the default, concise-verify baseline.


def test_compute_c1_passes_when_sdo_warm_is_much_faster_than_codex() -> None:
    pipelines = _all_pipelines(ttm_cold=100.0, ttm_warm=15.0)
    codex = _codex_arm(list(PHASE1_PROBLEMS), n=5, ttm=100.0)
    report = compute_c1(pipelines, codex)
    assert report.verdict == "pass"
    assert "assess" not in report.summary


def test_compute_c1_is_directional_when_the_point_estimate_passes_but_the_ci_does_not() -> None:
    # A wide-variance scenario (found by search) whose pooled ratio clears 2.0 on the
    # point estimate but whose bootstrap lower bound does not clear 1.5.
    per_problem_ttm = {
        S1: ([157.3, 350.0, 231.9, 320.6, 207.9], [135.0, 62.1, 155.2, 71.0]),
        S2: ([350.3, 503.9, 504.4, 152.2, 478.1], [176.8, 230.6, 136.0, 194.1]),
        S3: ([54.6, 120.3, 81.6, 97.5, 83.7], [40.1, 16.3, 28.5, 10.7]),
        K1: ([156.2, 151.8, 149.7, 115.0, 120.0], [43.0, 48.5, 62.5, 35.1]),
        K2: ([476.3, 211.8, 164.3, 128.1, 365.1], [181.7, 146.1, 65.9, 101.4]),
    }
    problem_order = list(per_problem_ttm)
    pipelines = [
        [_stage(problem, index=j, ttm=100.0) for j, problem in enumerate(problem_order)]
        + [
            _stage(problem, index=5 + j, ttm=per_problem_ttm[problem][1][pipeline_index])
            for j, problem in enumerate(problem_order)
        ]
        for pipeline_index in range(4)
    ]
    codex = [
        _codex_run(problem, ttm=ttm, source=f"codex/{problem}-{i}")
        for problem, (codex_vals, _) in per_problem_ttm.items()
        for i, ttm in enumerate(codex_vals)
    ]
    report = compute_c1(pipelines, codex)
    assert report.verdict == "directional"


def test_compute_c1_fails_when_sdo_warm_is_not_faster() -> None:
    pipelines = _all_pipelines(ttm_cold=100.0, ttm_warm=100.0)
    codex = _codex_arm(list(PHASE1_PROBLEMS), n=5, ttm=100.0)
    report = compute_c1(pipelines, codex)
    assert report.verdict == "fail"


def test_compute_c1_is_insufficient_without_any_matching_problem() -> None:
    report = compute_c1([], [])
    assert report.verdict == "insufficient_data"


def test_compute_c4_reports_e2e_success_and_notes_the_false_closure_gap() -> None:
    pipelines = _all_pipelines()
    stages = [stage for pipeline in pipelines for stage in pipeline]
    codex = _codex_arm(list(PHASE1_PROBLEMS), n=5, passed=True, ttm=100.0)
    report = compute_c4(stages, codex)
    assert report.verdict == "pass"
    assert "false-closure" in report.summary.lower()
    assert "false closure" in report.pass_criterion.lower()
    assert "no stock" in report.summary.lower()


def test_compute_c4_fails_when_sdo_underperforms_codex() -> None:
    pipelines = _all_pipelines()
    stages = [stage for pipeline in pipelines for stage in pipeline]
    # Make every SDO stage fail so SDO - Codex < 0.
    failing_stages = [
        SdoStage(**{**stage.__dict__, "verdict": _verdict(ttm=stage.verdict.ttm_seconds, passed=False)})
        for stage in stages
    ]
    codex = _codex_arm(list(PHASE1_PROBLEMS), n=5, passed=True, ttm=100.0)
    report = compute_c4(failing_stages, codex)
    assert report.verdict == "fail"


def test_compute_c4_is_insufficient_without_any_sdo_stages() -> None:
    report = compute_c4([], [])
    assert report.verdict == "insufficient_data"


def test_compute_c5_reports_the_learning_ratio_and_memory_growth() -> None:
    pipelines = _all_pipelines(ttm_cold=100.0, ttm_warm=20.0)
    report = compute_c5(pipelines)
    assert report.verdict in ("pass", "directional")
    assert "memory-growth" in report.summary


def test_compute_c5_fails_when_memory_growth_is_absent() -> None:
    pipelines = _all_pipelines(ttm_cold=100.0, ttm_warm=20.0)
    # Strip memory from every pipeline's last round-1 stage.
    stripped = [[SdoStage(**{**s.__dict__, "memory": None}) for s in pipeline] for pipeline in pipelines]
    report = compute_c5(stripped)
    assert report.verdict == "fail"


def test_compute_c8_scores_only_composite_problems() -> None:
    pipelines = _all_pipelines()
    stages = [stage for pipeline in pipelines for stage in pipeline]
    codex = _codex_arm([K1, K2], n=5, passed=True, ttm=100.0)
    report = compute_c8(stages, codex)
    assert report.verdict in ("pass", "directional", "fail")
    assert "gate" in report.summary.lower()


def test_compute_c8_is_insufficient_without_composite_stages() -> None:
    report = compute_c8([], [])
    assert report.verdict == "insufficient_data"


def test_compute_c11_verify_ratio_notes_the_decoy_scan_gap() -> None:
    pipelines = _all_pipelines(ttm_cold=100.0, ttm_warm=15.0)
    verify = _codex_arm(list(PHASE1_PROBLEMS), n=5, ttm=100.0)
    report = compute_c11_verify_ratio(pipelines, verify)
    assert "decoy" in report.summary.lower()
    assert "K3" in report.summary


def test_compute_all_claims_returns_exactly_the_eleven_plan_claims() -> None:
    pipelines = _all_pipelines()
    codex = _codex_arm(list(PHASE1_PROBLEMS), n=5)
    claims = compute_all_claims(pipelines, codex)
    assert [claim.claim for claim in claims] == [f"C{n}" for n in range(1, 12)]
    for claim in claims:
        assert claim.takeaways.strip()  # every claim has a mandatory takeaway
        assert claim.verdict in ("pass", "directional", "fail", "insufficient_data", "deferred")


def test_deferred_claims_are_marked_deferred_not_silently_dropped() -> None:
    pipelines = _all_pipelines()
    claims = {claim.claim: claim for claim in compute_all_claims(pipelines, [])}
    for claim_id in ("C6", "C7", "C9", "C10"):
        assert claims[claim_id].verdict == "deferred"


# --------------------------------------------------------------------------- integration: analyze() over synthetic dirs


def _write_sdo_pipeline_dir(root: Path, rotation: list[str]) -> Path:
    """A synthetic phase-1-shaped SDO pipeline directory, modeled on the network_policy_block stages in RC1.md."""

    for index, problem in enumerate(rotation * 2):
        stage = root / f"stage_{index}_{problem}"
        results = stage / "runs" / f"{index:06d}_{problem}" / "worker_0" / "results"
        run_dir = results / "sdo_codex" / problem / "run_1"
        run_dir.mkdir(parents=True)
        injected = 1_800_000_000.0
        submitted = injected + 40 + index
        with (results / "sdo_codex_ALL_results.csv").open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(
                stream,
                fieldnames=[
                    "Diagnosis.success",
                    "Mitigation.success",
                    "TTL",
                    "diagnosis_submitted_at",
                    "fault_injected_at",
                    "mitigation_submitted_at",
                    "problem_id",
                ],
            )
            writer.writeheader()
            writer.writerow(
                {
                    "Diagnosis.success": "True",
                    "Mitigation.success": "True",
                    "TTL": "30",
                    "diagnosis_submitted_at": injected + 10,
                    "fault_injected_at": injected,
                    "mitigation_submitted_at": submitted,
                    "problem_id": problem,
                }
            )
        (run_dir / "sdo_production_receipt_strict.json").write_text(
            json.dumps(
                {
                    "incident_resolution_seconds": 40.0 + index,
                    "usage": {"input_tokens": 600, "cached_input_tokens": 400, "output_tokens": 40},
                    "reflection_usage": {},
                    "reflection_attempts": 0,
                    "reflection_skipped_reason": "repeated exact-match success" if index >= 5 else None,
                    "memory_reuse": {"warm_path": index >= 5, "match_reasons": []},
                    "responder_session_id": f"s{index}",
                }
            ),
            encoding="utf-8",
        )
        sdo = stage / "application_workspace" / ".sdo"
        (sdo / "diagnostics").mkdir(parents=True)
        (sdo / "diagnostics" / "manifest.yaml").write_text(
            "detectors:\n- {id: health-objective, class: health}\n- {id: incident-0, class: incident}\n",
            encoding="utf-8",
        )
        (sdo / "playbooks" / "incident-0").mkdir(parents=True)
        (sdo / "playbooks" / "incident-0" / "README.md").write_text("# playbook\n", encoding="utf-8")
    state = {
        "stages": [
            {"index": i, "name": f"stage-{i}", "experiment_dir": str(root / f"stage_{i}_{p}")}
            for i, p in enumerate(rotation * 2)
        ]
    }
    (root / "pipeline_state.json").write_text(json.dumps(state), encoding="utf-8")
    return root


def _write_codex_experiment_dir(root: Path, problems: list[str]) -> Path:
    for sequence, problem in enumerate(problems):
        results = root / "runs" / f"{sequence:06d}_{problem}" / "worker_0" / "results"
        run_dir = results / "codex" / problem / "run_1"
        run_dir.mkdir(parents=True)
        injected = 0.0
        with (results / "codex_ALL_results.csv").open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(
                stream,
                fieldnames=["Diagnosis.success", "Mitigation.success", "fault_injected_at", "mitigation_submitted_at"],
            )
            writer.writeheader()
            writer.writerow(
                {
                    "Diagnosis.success": "True",
                    "Mitigation.success": "True",
                    "fault_injected_at": injected,
                    "mitigation_submitted_at": 150.0,
                }
            )
        (run_dir / f"codex_results_{problem}_1.json").write_text(
            json.dumps(
                {"usage_metrics": {"input_tokens": 300_000, "cached_input_tokens": 150_000, "output_tokens": 2_000}}
            ),
            encoding="utf-8",
        )
    return root


def test_analyze_wires_run_validity_incident_cost_and_claims_over_synthetic_directories(tmp_path: Path) -> None:
    sdo_dirs = [_write_sdo_pipeline_dir(tmp_path / f"sdo_{letter}", order) for letter, order in ROTATIONS.items()]
    codex_dir_1 = _write_codex_experiment_dir(tmp_path / "codex_1", list(PHASE1_PROBLEMS) * 3)
    codex_dir_2 = _write_codex_experiment_dir(tmp_path / "codex_2", list(PHASE1_PROBLEMS) * 2)

    analysis = analyze(sdo_dirs, [codex_dir_1, codex_dir_2], legacy=True)

    assert len(analysis.claims) == 11
    rendered = analysis.render()
    for n in range(1, 12):
        assert f"C{n}:" in rendered
        assert "Takeaways" in rendered
    assert "valid problem run" in analysis.validity_summary
