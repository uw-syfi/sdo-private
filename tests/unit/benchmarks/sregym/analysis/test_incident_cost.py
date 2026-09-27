from __future__ import annotations

import csv
import json
from pathlib import Path  # noqa: TC003 -- fixtures build real paths at runtime

import pytest
import yaml

from benchmarks.sregym.analysis.incident_cost import (
    WARM_PROMPT_MARKER,
    IncidentCostError,
    TokenUsage,
    build_report,
    load_codex_runs,
    load_sdo_pipeline,
    main,
)

PROBLEM_A = "missing_configmap_hotel_reservation"
PROBLEM_B = "missing_configmap_mongodb_rate_hotel_reservation"


def _usage(input_tokens: int, output_tokens: int, cached: int = 0) -> dict[str, int]:
    return {"input_tokens": input_tokens, "cached_input_tokens": cached, "output_tokens": output_tokens}


def _results_dir(experiment: Path, sequence: int, problem: str, agent: str) -> Path:
    results = experiment / "runs" / f"{sequence:06d}_{problem}" / "worker_0" / "results"
    (results / agent / problem / "run_1").mkdir(parents=True)
    return results


def _write_csv(results: Path, agent: str, *, diagnosis: bool, mitigation: bool, injected: float, submitted: float):
    with (results / f"{agent}_ALL_results.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=["Diagnosis.success", "Mitigation.success", "fault_injected_at", "mitigation_submitted_at"],
        )
        writer.writeheader()
        writer.writerow(
            {
                "Diagnosis.success": str(diagnosis),
                "Mitigation.success": str(mitigation),
                "fault_injected_at": injected,
                "mitigation_submitted_at": submitted,
            }
        )


def _rollout(path: Path, prompt: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    records = [
        {"type": "session_meta", "payload": {"id": "s"}},
        {
            "type": "response_item",
            "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": prompt}]},
        },
    ]
    path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")


def _sdo_stage(
    pipeline: Path,
    index: int,
    name: str,
    problem: str,
    *,
    responder: dict[str, int],
    reflection: dict[str, int],
    warm: bool,
    incident_detectors: int,
    passed: bool = True,
    lifecycle: dict[str, int] | None = None,
) -> Path:
    stage = pipeline / f"stage_{index}_{name}"
    results = _results_dir(stage, 0, problem, "sdo_codex")
    _write_csv(
        results, "sdo_codex", diagnosis=True, mitigation=passed, injected=1000.0, submitted=1000.0 + 50 * (index + 1)
    )
    run = results / "sdo_codex" / problem / "run_1"
    session = f"session-{index}"
    receipt = {
        "incident_resolution_seconds": 100.0 + index,
        "usage": responder,
        "reflection_usage": reflection,
        "reflection_attempts": 0 if warm else 1,
        "reflection_skipped_reason": "repeated exact-match success" if warm else None,
        "phase_timings_seconds": {"post_recovery_learning_and_receipt": 5.0 if warm else 300.0},
        "memory_reuse": {"warm_path": warm, "match_reasons": ["exact-fingerprint"] if warm else []},
        "responder_session_id": session,
    }
    (run / "sdo_production_receipt_strict.json").write_text(json.dumps(receipt), encoding="utf-8")
    prompt = f"You are the SDO incident responder. {WARM_PROMPT_MARKER} Playbook..." if warm else "Cold path."
    _rollout(run / "sdo_runtime" / "codex" / "sessions" / "2026" / "09" / "27" / f"rollout-x-{session}.jsonl", prompt)
    if not warm:
        usage_dir = run / "sdo_runtime" / "usage"
        usage_dir.mkdir(parents=True)
        (usage_dir / "controller-turns.jsonl").write_text(
            json.dumps({"duration_seconds": 240.0, "usage": reflection}) + "\n", encoding="utf-8"
        )
    if lifecycle is not None:
        (run / "sdo_turn_usage.jsonl").write_text(
            json.dumps({"duration_seconds": 900.0, "usage": lifecycle}) + "\n", encoding="utf-8"
        )
    sdo = stage / "application_workspace" / ".sdo"
    (sdo / "diagnostics").mkdir(parents=True)
    detectors = [{"id": "health-objective", "class": "health"}] + [
        {"id": f"incident-{n}", "class": "incident"} for n in range(incident_detectors)
    ]
    (sdo / "diagnostics" / "manifest.yaml").write_text(yaml.safe_dump({"detectors": detectors}), encoding="utf-8")
    for playbook in ["health-objective"] + [f"incident-{n}" for n in range(incident_detectors)]:
        (sdo / "playbooks" / playbook).mkdir(parents=True)
        (sdo / "playbooks" / playbook / "README.md").write_text("# playbook\n", encoding="utf-8")
    (sdo / "playbooks" / "README.md").write_text("# index\n", encoding="utf-8")
    return stage


def _codex_experiment(root: Path, runs: list[tuple[str, bool, int]]) -> Path:
    for sequence, (problem, passed, tokens) in enumerate(runs):
        results = _results_dir(root, sequence, problem, "codex")
        _write_csv(results, "codex", diagnosis=True, mitigation=passed, injected=0.0, submitted=200.0)
        (results / "codex" / problem / "run_1" / f"codex_results_{problem}_1.json").write_text(
            json.dumps({"usage_metrics": _usage(tokens, 0, cached=tokens // 2)}), encoding="utf-8"
        )
    return root


@pytest.fixture
def pipeline(tmp_path: Path) -> Path:
    root = tmp_path / "20260927_000000_pipeline_sdo-codex-luna-variants"
    _sdo_stage(
        root,
        0,
        "first",
        PROBLEM_A,
        responder=_usage(600, 100),
        reflection=_usage(1_500, 100),
        warm=False,
        incident_detectors=1,
        lifecycle=_usage(10_000, 0),
    )
    _sdo_stage(root, 1, "repeat", PROBLEM_A, responder=_usage(200, 0), reflection={}, warm=True, incident_detectors=1)
    _sdo_stage(root, 2, "variant", PROBLEM_B, responder=_usage(900, 0), reflection={}, warm=False, incident_detectors=2)
    aborted = root / "stage_1_repeat.20260927_141722"
    aborted.mkdir()
    state = {
        "stages": [
            {"index": index, "name": name, "experiment_dir": f"/elsewhere/{root.name}/stage_{index}_{name}"}
            for index, name in enumerate(["first", "repeat", "variant"])
        ]
    }
    (root / "pipeline_state.json").write_text(json.dumps(state), encoding="utf-8")
    return root


@pytest.fixture
def codex_dirs(tmp_path: Path) -> list[Path]:
    return [
        _codex_experiment(tmp_path / "codex_a1", [(PROBLEM_A, True, 800), (PROBLEM_B, True, 1_000)]),
        _codex_experiment(tmp_path / "codex_a2", [(PROBLEM_A, False, 1_200)]),
    ]


def test_loads_stage_metrics_from_receipts_rollouts_and_memory(pipeline: Path) -> None:
    stages = load_sdo_pipeline(pipeline)

    assert [(stage.index, stage.problem_id) for stage in stages] == [(0, PROBLEM_A), (1, PROBLEM_A), (2, PROBLEM_B)]
    first, repeat, variant = stages
    assert first.verdict.primary_seconds == 50.0
    assert first.verdict.passed
    assert first.incident_resolution_seconds == 100.0
    assert first.responder.total() == 700
    assert first.reflection.total() == 1_600
    assert first.reflection_turn_seconds == 240.0
    assert first.learning_seconds == 300.0
    assert first.warm_path is False
    assert first.warm_prompt is False
    assert first.lifecycle.total() == 10_000
    assert repeat.warm_path is True
    assert repeat.warm_prompt is True
    assert repeat.reflection_skipped_reason is not None
    assert repeat.reflection.total() == 0
    assert variant.memory is not None
    assert (variant.memory.detectors, variant.memory.incident_detectors, variant.memory.playbooks) == (3, 2, 3)


def test_cumulative_totals_and_break_even_against_codex(pipeline: Path, codex_dirs: list[Path]) -> None:
    codex_runs = [run for directory in codex_dirs for run in load_codex_runs(directory)]
    report = build_report(load_sdo_pipeline(pipeline), codex_runs)

    cost_a = report.codex[PROBLEM_A]
    assert (cost_a.runs, cost_a.passed, cost_a.mean_tokens, cost_a.mean_primary_seconds) == (2, 1, 1_000, 200.0)
    assert [row.sdo_incident_tokens for row in report.cumulative] == [700, 900, 1_800]
    assert [row.sdo_tokens_with_learning for row in report.cumulative] == [2_300, 2_500, 3_400]
    assert [row.codex_tokens for row in report.cumulative] == [1_000, 2_000, 3_000]
    assert [row.sdo_primary_seconds for row in report.cumulative] == [50.0, 150.0, 300.0]
    assert report.lifecycle_tokens == 10_000

    by_key = {(item.measure, item.includes_lifecycle): item for item in report.break_even}
    assert by_key[("incident tokens (responder)", False)].stage == 0
    learning = by_key[("total tokens incl. learning", False)]
    assert learning.stage is None
    assert learning.final_gap == 400
    # The only repeat stage saved 1,000 - 200 = 800 tokens, so one more repeat closes the gap.
    assert learning.projected_extra_repeats == 1
    with_lifecycle = by_key[("incident tokens (responder)", True)]
    assert with_lifecycle.stage is None
    assert with_lifecycle.final_gap == 10_000 + 1_800 - 3_000


def test_uncached_measure_subtracts_cached_input(pipeline: Path, codex_dirs: list[Path]) -> None:
    codex_runs = [run for directory in codex_dirs for run in load_codex_runs(directory)]

    report = build_report(load_sdo_pipeline(pipeline), codex_runs, uncached=True)

    assert report.codex[PROBLEM_A].mean_tokens == 500


def test_lifecycle_override_replaces_discovered_lifecycle(pipeline: Path) -> None:
    report = build_report(load_sdo_pipeline(pipeline), [], lifecycle_override=TokenUsage(input_tokens=5))

    assert report.lifecycle_tokens == 5
    assert all(row.codex_tokens is None for row in report.cumulative)


def test_cli_prints_tables_and_writes_json(pipeline: Path, codex_dirs: list[Path], tmp_path: Path, capsys) -> None:
    output = tmp_path / "report.json"

    assert main([str(pipeline), "--codex", *map(str, codex_dirs), "--json", str(output)]) == 0

    printed = capsys.readouterr().out
    assert "SDO stages" in printed
    assert "Break-even" in printed
    assert "PASS D+M+" in printed
    assert json.loads(output.read_text(encoding="utf-8"))["lifecycle_tokens"] == 10_000


def test_rejects_directory_without_stages(tmp_path: Path) -> None:
    with pytest.raises(IncidentCostError):
        load_sdo_pipeline(tmp_path)


def test_token_usage_validates_counts() -> None:
    with pytest.raises(ValueError, match="must not be negative"):
        TokenUsage(input_tokens=-1)
    with pytest.raises(TypeError):
        TokenUsage(output_tokens=1.5)  # type: ignore[arg-type]


def test_reflection_turn_seconds_count_only_the_stage_incident_in_a_shared_usage_log(tmp_path: Path) -> None:
    """A persistent controller's usage log accumulates every incident's reflection turns."""

    from sdo.operational_memory import incident_worktree_dirname

    root = tmp_path / "20260927_000000_pipeline_sdo-codex-luna-persistent"
    stage = _sdo_stage(
        root, 0, "first", PROBLEM_A, responder=_usage(1, 0), reflection=_usage(1, 0), warm=False, incident_detectors=1
    )
    run = stage / "runs" / f"000000_{PROBLEM_A}" / "worker_0" / "results" / "sdo_codex" / PROBLEM_A / "run_1"
    receipt_path = run / "sdo_production_receipt_strict.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["incident_id"] = "incident-2"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    records = [
        {"cwd": f"/workspace/wt/{incident_worktree_dirname('incident-1')}", "duration_seconds": 240.0, "usage": {}},
        {"cwd": f"/workspace/wt/{incident_worktree_dirname('incident-2')}", "duration_seconds": 30.0, "usage": {}},
        {"cwd": f"/workspace/wt/{incident_worktree_dirname('incident-2')}", "duration_seconds": 12.0, "usage": {}},
    ]
    (run / "sdo_runtime" / "usage" / "controller-turns.jsonl").write_text(
        "".join(json.dumps(record) + "\n" for record in records), encoding="utf-8"
    )

    (only,) = load_sdo_pipeline(root)

    assert only.reflection_turn_seconds == 42.0
