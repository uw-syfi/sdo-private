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
    first_mutation_at,
    last_mutation_done_at,
    load_codex_runs,
    load_sdo_pipeline,
    main,
    read_verdict,
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
    assert first.verdict.raw_incl_judge_seconds == 50.0
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
    assert (cost_a.runs, cost_a.passed, cost_a.mean_tokens, cost_a.mean_raw_incl_judge_seconds) == (2, 1, 1_000, 200.0)
    assert [row.sdo_incident_tokens for row in report.cumulative] == [700, 900, 1_800]
    assert [row.sdo_tokens_with_learning for row in report.cumulative] == [2_300, 2_500, 3_400]
    assert [row.codex_tokens for row in report.cumulative] == [1_000, 2_000, 3_000]
    # Without stage timestamps the judge-free TTM is unknown, so no cumulative time is claimed.
    assert [row.sdo_ttm_seconds for row in report.cumulative] == [None, None, None]
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


def _timing_csv(results: Path, **row: object) -> None:
    results.mkdir(parents=True, exist_ok=True)
    with (results / "codex_ALL_results.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(row))
        writer.writeheader()
        writer.writerow(row)


def test_judge_excluded_time_subtracts_the_diagnosis_grading_wait(tmp_path: Path) -> None:
    # Diagnosis POSTed 20 s after injection; the conductor finished grading it and opened the
    # mitigation stage at TTL = 37 s; the mitigation POST came at 96 s.
    _timing_csv(
        tmp_path,
        **{
            "Diagnosis.success": "True",
            "Mitigation.success": "True",
            "fault_injected_at": 1000.0,
            "diagnosis_submitted_at": 1020.0,
            "TTL": 37.0,
            "mitigation_submitted_at": 1096.0,
        },
    )

    verdict = read_verdict(tmp_path)

    assert verdict is not None
    assert verdict.raw_incl_judge_seconds == pytest.approx(96.0)
    assert verdict.diagnosis_seconds == pytest.approx(20.0)
    assert verdict.grading_wait_seconds == pytest.approx(17.0)
    assert verdict.judge_excluded_seconds == pytest.approx(79.0)


def test_judge_excluded_time_is_unknown_without_stage_timestamps(tmp_path: Path) -> None:
    _timing_csv(
        tmp_path,
        **{
            "Diagnosis.success": "True",
            "Mitigation.success": "True",
            "fault_injected_at": 1000.0,
            "mitigation_submitted_at": 1096.0,
        },
    )

    verdict = read_verdict(tmp_path)

    assert verdict is not None
    assert verdict.grading_wait_seconds is None
    assert verdict.judge_excluded_seconds is None


def _tool_rollout(path: Path, calls: list[tuple[str, dict[str, object]]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(
            json.dumps({"timestamp": ts, "type": "response_item", "payload": payload}) + "\n" for ts, payload in calls
        ),
        encoding="utf-8",
    )


def _code_mode(cmd: str) -> dict[str, object]:
    return {
        "type": "custom_tool_call",
        "name": "exec",
        "input": f"const r = await tools.exec_command({{cmd:{json.dumps(cmd)}}});",
    }


def test_first_mutation_is_the_first_state_changing_command_after_injection(tmp_path: Path) -> None:
    rollout = tmp_path / "rollout-a.jsonl"
    _tool_rollout(
        rollout,
        [
            (
                "2026-09-27T12:00:05.000Z",
                _code_mode("kubectl -n hotel-reservation delete pod stale"),
            ),  # before injection
            ("2026-09-27T12:00:20.000Z", _code_mode("kubectl -n hotel-reservation get cm && kubectl logs deploy/geo")),
            (
                "2026-09-27T12:00:30.000Z",
                _code_mode("bash -n .sdo/playbooks/x/scripts/repair.sh; cat scripts/repair.sh"),
            ),
            ("2026-09-27T12:00:40.000Z", _code_mode("python3 -m benchmarks.sregym.adapter.submission diagnosis 'x'")),
            ("2026-09-27T12:00:51.500Z", _code_mode("kubectl create configmap mongo-geo-script -n hotel-reservation")),
            ("2026-09-27T12:01:10.000Z", _code_mode("kubectl -n hotel-reservation rollout restart deploy/geo")),
        ],
    )
    injected = 1790510410.0  # 2026-09-27T12:00:10Z

    assert first_mutation_at([rollout], after=injected) == pytest.approx(injected + 41.5)


@pytest.mark.parametrize(
    "call",
    [
        {
            "type": "function_call",
            "name": "exec_command",
            "arguments": json.dumps({"cmd": "bash .sdo/playbooks/m/scripts/repair.sh hotel-reservation geo"}),
        },
        _code_mode("cd /app && .sdo/playbooks/m/scripts/repair.sh hotel-reservation"),
        _code_mode("kubectl --namespace=hotel-reservation patch deployment geo -p '{}'"),
        _code_mode("cat <<'EOF' | kubectl apply -f -\nkind: ConfigMap\nEOF"),
    ],
)
def test_first_mutation_recognizes_playbook_repairs_and_kubectl_writes(tmp_path: Path, call: dict[str, object]) -> None:
    rollout = tmp_path / "rollout-b.jsonl"
    _tool_rollout(rollout, [("2026-09-27T12:00:30.000Z", call)])

    assert first_mutation_at([rollout], after=1790510410.0) == pytest.approx(1790510430.0)


def test_codex_runs_report_when_the_mitigation_was_applied(tmp_path: Path) -> None:
    results = tmp_path / "exp" / "runs" / f"000000_{PROBLEM_A}" / "worker_0" / "results"
    _timing_csv(
        results,
        **{
            "Diagnosis.success": "True",
            "Mitigation.success": "True",
            "fault_injected_at": 1790510410.0,
            "diagnosis_submitted_at": 1790510440.0,
            "TTL": 45.0,
            "mitigation_submitted_at": 1790510500.0,
        },
    )
    _tool_rollout(
        results / "codex" / PROBLEM_A / "run_1" / "sessions" / "rollout-c.jsonl",
        [("2026-09-27T12:01:00.000Z", _code_mode("kubectl apply -f cm.yaml"))],
    )

    (run,) = load_codex_runs(tmp_path / "exp")

    assert run.verdict.mitigation_applied_seconds == pytest.approx(50.0)
    assert run.verdict.judge_excluded_seconds == pytest.approx(90.0 - 15.0)


def test_json_report_carries_judge_excluded_stage_time(pipeline: Path, tmp_path: Path) -> None:
    out = tmp_path / "report.json"
    main([str(pipeline), "--json", str(out)])

    for stage in json.loads(out.read_text(encoding="utf-8"))["stages"]:
        assert "judge_excluded_seconds" in stage["verdict"]
        assert "mitigation_applied_seconds" in stage["verdict"]


def test_headline_ttm_is_the_judge_excluded_time_when_nothing_mitigating_ran_during_grading(tmp_path: Path) -> None:
    results = tmp_path / "exp" / "runs" / f"000000_{PROBLEM_A}" / "worker_0" / "results"
    _timing_csv(
        results,
        **{
            "Diagnosis.success": "True",
            "Mitigation.success": "True",
            "fault_injected_at": 1790510410.0,
            "diagnosis_submitted_at": 1790510440.0,
            "TTL": 45.0,
            "mitigation_submitted_at": 1790510500.0,
        },
    )
    rollout = results / "codex" / PROBLEM_A / "run_1" / "sessions" / "rollout-c.jsonl"
    _tool_rollout(
        rollout,
        [
            ("2026-09-27T12:00:20.000Z", {**_code_mode("kubectl apply -f cm.yaml"), "call_id": "a"}),
            ("2026-09-27T12:00:25.000Z", {"type": "custom_tool_call_output", "call_id": "a", "output": "ok"}),
        ],
    )

    (run,) = load_codex_runs(tmp_path / "exp")

    assert run.verdict.diagnosis_seconds == pytest.approx(30.0)
    assert run.verdict.last_mitigation_seconds == pytest.approx(15.0)
    assert run.verdict.ttm_seconds == pytest.approx(90.0 - 15.0)


def test_headline_ttm_keeps_mitigation_work_finished_during_diagnosis_grading(tmp_path: Path) -> None:
    """Subtracting the whole grading wait would undercount an agent that kept repairing meanwhile."""

    results = tmp_path / "exp" / "runs" / f"000000_{PROBLEM_A}" / "worker_0" / "results"
    _timing_csv(
        results,
        **{
            "Diagnosis.success": "True",
            "Mitigation.success": "True",
            "fault_injected_at": 1790510410.0,  # 12:00:10Z
            "diagnosis_submitted_at": 1790510420.0,  # grading until 12:01:00Z (TTL 50)
            "TTL": 50.0,
            "mitigation_submitted_at": 1790510470.0,
        },
    )
    rollout = results / "codex" / PROBLEM_A / "run_1" / "sessions" / "rollout-c.jsonl"
    _tool_rollout(
        rollout,
        [
            ("2026-09-27T12:00:22.000Z", {**_code_mode("kubectl create cm x"), "call_id": "a"}),
            ("2026-09-27T12:00:23.000Z", {"type": "custom_tool_call_output", "call_id": "a", "output": ""}),
            ("2026-09-27T12:00:30.000Z", {**_code_mode("kubectl rollout restart deploy/geo"), "call_id": "b"}),
            ("2026-09-27T12:00:48.000Z", {"type": "custom_tool_call_output", "call_id": "b", "output": ""}),
        ],
    )

    (run,) = load_codex_runs(tmp_path / "exp")

    assert run.verdict.raw_incl_judge_seconds == pytest.approx(60.0)
    assert run.verdict.judge_excluded_seconds == pytest.approx(20.0)
    assert run.verdict.last_mitigation_seconds == pytest.approx(38.0)
    assert run.verdict.ttm_seconds == pytest.approx(38.0)


def test_last_mutation_ignores_reads_and_work_after_the_mitigation_submission(tmp_path: Path) -> None:
    rollout = tmp_path / "rollout-d.jsonl"
    _tool_rollout(
        rollout,
        [
            ("2026-09-27T12:00:05.000Z", {**_code_mode("kubectl delete pod stale"), "call_id": "pre"}),
            ("2026-09-27T12:00:06.000Z", {"type": "custom_tool_call_output", "call_id": "pre", "output": ""}),
            ("2026-09-27T12:00:20.000Z", {**_code_mode("kubectl patch deploy geo -p '{}'"), "call_id": "fix"}),
            ("2026-09-27T12:00:24.000Z", {"type": "custom_tool_call_output", "call_id": "fix", "output": ""}),
            ("2026-09-27T12:00:30.000Z", {**_code_mode("kubectl get pods"), "call_id": "read"}),
            ("2026-09-27T12:00:31.000Z", {"type": "custom_tool_call_output", "call_id": "read", "output": ""}),
            ("2026-09-27T12:01:30.000Z", {**_code_mode("kubectl delete pod later"), "call_id": "post"}),
            ("2026-09-27T12:01:31.000Z", {"type": "custom_tool_call_output", "call_id": "post", "output": ""}),
        ],
    )
    injected = 1790510410.0  # 12:00:10Z

    assert last_mutation_done_at([rollout], after=injected, before=injected + 60) == pytest.approx(injected + 14.0)
    # A mutating call still running at the submission is counted up to the submission.
    assert last_mutation_done_at([rollout], after=injected, before=injected + 12) == pytest.approx(injected + 12.0)


def test_headline_ttm_is_unknown_without_the_grading_wait(tmp_path: Path) -> None:
    _timing_csv(
        tmp_path,
        **{
            "Diagnosis.success": "True",
            "Mitigation.success": "True",
            "fault_injected_at": 0.0,
            "mitigation_submitted_at": 9.0,
        },
    )

    verdict = read_verdict(tmp_path)

    assert verdict is not None
    assert verdict.ttm_seconds is None


def test_cumulative_time_and_tables_use_the_judge_free_ttm(tmp_path: Path, capsys) -> None:
    root = tmp_path / "20260927_000000_pipeline_sdo-codex-luna-persistent"
    stage = _sdo_stage(
        root, 0, "first", PROBLEM_A, responder=_usage(1, 0), reflection=_usage(1, 0), warm=False, incident_detectors=1
    )
    results = stage / "runs" / f"000000_{PROBLEM_A}" / "worker_0" / "results"
    (results / "sdo_codex_ALL_results.csv").unlink()
    _timing_csv(
        results,
        **{
            "Diagnosis.success": "True",
            "Mitigation.success": "True",
            "fault_injected_at": 1000.0,
            "diagnosis_submitted_at": 1010.0,
            "TTL": 40.0,
            "mitigation_submitted_at": 1070.0,
        },
    )
    codex = tmp_path / "codex"
    codex_results = _results_dir(codex, 0, PROBLEM_A, "codex")
    _timing_csv(
        codex_results,
        **{
            "Diagnosis.success": "True",
            "Mitigation.success": "True",
            "fault_injected_at": 0.0,
            "diagnosis_submitted_at": 20.0,
            "TTL": 50.0,
            "mitigation_submitted_at": 100.0,
        },
    )

    report = build_report(load_sdo_pipeline(root), load_codex_runs(codex))

    assert report.stages[0].verdict.diagnosis_seconds == pytest.approx(10.0)
    assert [row.sdo_ttm_seconds for row in report.cumulative] == [pytest.approx(40.0)]
    assert [row.codex_ttm_seconds for row in report.cumulative] == [pytest.approx(70.0)]
    assert report.codex[PROBLEM_A].mean_ttd_seconds == pytest.approx(20.0)
    assert report.codex[PROBLEM_A].mean_ttm_seconds == pytest.approx(70.0)

    main([str(root), "--codex", str(codex)])
    printed = capsys.readouterr().out
    for column in ("ttd_s", "ttm_s", "raw_incl_judge_s", "last_mut_s", "mean_ttd_s", "mean_ttm_s", "sdo_ttm_s"):
        assert column in printed
    assert "primary" not in printed


@pytest.mark.parametrize(
    "command",
    [
        "kubectl -n hotel-reservation rollout status deployment/mongodb-geo --timeout=30s",
        "kubectl create configmap x --from-file=a.sh --dry-run=client -o yaml > cm.yaml",
        "kubectl apply --dry-run=server -f cm.yaml",
        "kubectl rollout history deployment/geo",
    ],
)
def test_read_only_kubectl_forms_are_not_mutations(tmp_path: Path, command: str) -> None:
    rollout = tmp_path / "rollout-e.jsonl"
    _tool_rollout(rollout, [("2026-09-27T12:00:30.000Z", _code_mode(command))])

    assert first_mutation_at([rollout], after=1790510410.0) is None
