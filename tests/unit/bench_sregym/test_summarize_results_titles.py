from __future__ import annotations

from typing import TYPE_CHECKING

from bench.sregym_analysis import summarize_results as sr

if TYPE_CHECKING:
    from pathlib import Path


def test_diff_results_resolution_cdf_title_omits_ttm_suffix(tmp_path: Path, monkeypatch) -> None:
    run_map = {
        "prob-1": {
            "status": "Completed",
            "TTL": "10",
            "TTM": "25",
            "has_mitigation": True,
            "Diagnosis.success": "True",
            "Mitigation.success": "True",
            "autonomous": False,
        }
    }

    captured_titles: list[str] = []

    monkeypatch.setattr(sr, "load_results", lambda _path: (run_map, list(run_map.values())))
    monkeypatch.setattr(sr, "load_problem_type_mapping", dict)
    monkeypatch.setattr(sr, "load_crucible_turns", lambda _path: {})
    monkeypatch.setattr(sr, "load_cli_agent_turns", lambda _path: {})
    monkeypatch.setattr(sr, "load_crucible_turns_by_phase", lambda _path: ({}, {}))
    monkeypatch.setattr(sr, "load_stratus_tokens", lambda _path: {})
    monkeypatch.setattr(sr, "load_crucible_tokens", lambda _path: {})
    monkeypatch.setattr(sr, "load_cli_agent_tokens", lambda _path: {})
    monkeypatch.setattr(sr, "load_crucible_tokens_by_phase", lambda _path: ({}, {}))
    monkeypatch.setattr(sr, "load_gemini_tokens", lambda _path: {})
    monkeypatch.setattr(
        sr,
        "plot_cdfs",
        lambda _data_list, _labels, title_metric, _output_path, colors=None: captured_titles.append(title_metric),
    )
    monkeypatch.setattr(sr, "plot_comparison_by_problem", lambda *args, **kwargs: None)
    monkeypatch.setattr(sr, "plot_success_rates", lambda *args, **kwargs: None)
    monkeypatch.setattr(sr, "plot_turns_comparison_by_problem", lambda *args, **kwargs: None)
    monkeypatch.setattr(sr, "plot_token_comparison_by_problem", lambda *args, **kwargs: None)
    monkeypatch.setattr(sr, "plot_cdf_turns", lambda *args, **kwargs: None)
    monkeypatch.setattr(sr, "plot_cdf_tokens", lambda *args, **kwargs: None)
    monkeypatch.setattr(sr, "plot_tokens_vs_time", lambda *args, **kwargs: None)

    dir1 = tmp_path / "run_a"
    dir2 = tmp_path / "run_b"
    dir1.mkdir()
    dir2.mkdir()

    sr.diff_results([str(dir1), str(dir2)])

    assert "Time to Resolution" in captured_titles
    assert "Time to Resolution (TTM)" not in captured_titles
