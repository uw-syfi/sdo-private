"""incident_cost reports only runs the validity checker accepts, and says which it left out and why."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from sregym_run_builder import Run

from benchmarks.sregym.analysis.incident_cost import main

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

WAIVED = {"preflight": {"ok": False, "waived": True, "checks": [{"name": "codex-quota", "status": "fail"}]}}


def _pipeline(root: Path, stages: list[Run]) -> Path:
    root.mkdir(parents=True)
    state = []
    for index, run in enumerate(stages):
        stage = root / f"stage_{index}_s{index}"
        run.write(stage)
        state.append({"index": index, "name": f"s{index}", "status": "completed", "experiment_dir": str(stage)})
    (root / "pipeline_state.json").write_text(json.dumps({"stages": state}), encoding="utf-8")
    return root


def _receipt() -> dict[str, object]:
    return {
        "responder_session_id": "resp1",
        "usage": {"input_tokens": 1000, "cached_input_tokens": 800, "output_tokens": 50},
    }


def test_invalid_runs_are_excluded_with_their_reasons_and_agent_failures_stay(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pipeline = _pipeline(
        tmp_path / "pipeline",
        [Run(agent="sdo_codex", receipt=_receipt()), Run(agent="sdo_codex", receipt=_receipt(), manifest=WAIVED)],
    )
    good = Run().write(tmp_path / "codex_good")
    broken = Run(install_rc="1").write(tmp_path / "codex_broken")
    out = tmp_path / "report.json"

    assert main([str(pipeline), "--codex", str(good), str(broken), "--json", str(out)]) == 0

    captured = capsys.readouterr()
    assert "Excluded runs (invalid_infra, 2)" in captured.out
    assert "SDO stage 1 s1" in captured.out
    assert "preflight waived" in captured.out
    assert "agent CLI install exited 1" in captured.out
    report = json.loads(out.read_text(encoding="utf-8"))
    assert [stage["index"] for stage in report["stages"]] == [0]  # the agent_failure stage is kept
    assert report["codex"]["missing_configmap_hotel_reservation"]["runs"] == 1
    assert len(report["excluded"]) == 2


def test_include_invalid_keeps_every_run(tmp_path: Path) -> None:
    pipeline = _pipeline(tmp_path / "pipeline", [Run(agent="sdo_codex", receipt=_receipt(), manifest=WAIVED)])
    out = tmp_path / "report.json"

    assert main([str(pipeline), "--json", str(out), "--include-invalid"]) == 0

    assert len(json.loads(out.read_text(encoding="utf-8"))["stages"]) == 1


def test_a_pipeline_with_no_valid_stage_is_an_error_not_an_empty_report(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pipeline = _pipeline(tmp_path / "pipeline", [Run(agent="sdo_codex", receipt=_receipt(), manifest=None)])

    assert main([str(pipeline)]) == 2
    assert "--legacy-runs" in capsys.readouterr().err
    assert main([str(pipeline), "--legacy-runs"]) == 0
