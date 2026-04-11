"""Tests for lineage-report command."""

import json
from argparse import Namespace

from app_operator.commands.lineage_report import run_command


def test_lineage_report_strict_passes_on_consistent_hashes(tmp_path, capsys):
    optimized_dir = tmp_path / "optimized"
    family_dir = optimized_dir / "simple_LLM"
    candidate_dir = family_dir / "eval_1_c1"
    version_dir = family_dir / "v1"
    candidate_dir.mkdir(parents=True)
    version_dir.mkdir(parents=True)

    module_text = '{"optimized_instruction":"demo"}\n'
    (candidate_dir / "deployer_fix_error.dspy.json").write_text(module_text)
    (version_dir / "deployer_fix_error.dspy.json").write_text(module_text)
    (version_dir / "metadata.json").write_text(
        json.dumps(
            {
                "iteration": 1,
                "best_candidate_index": 1,
                "best_score": 0.9,
                "prompts": {"deployer_fix_error": {"optimized": True}},
            }
        )
    )
    (family_dir / "lineage.jsonl").write_text(
        json.dumps({"event_type": "candidate_generated", "payload": {"candidate_id": "simple_LLM/eval_1_c1"}}) + "\n"
    )

    code = run_command(
        Namespace(
            optimized_dir=optimized_dir,
            family="simple_LLM",
            strict=True,
            json=False,
        )
    )
    captured = capsys.readouterr().out
    assert code == 0
    assert "Issues: none" in captured


def test_lineage_report_strict_fails_on_hash_mismatch(tmp_path, capsys):
    optimized_dir = tmp_path / "optimized"
    family_dir = optimized_dir / "simple_LLM"
    candidate_dir = family_dir / "eval_1_c1"
    version_dir = family_dir / "v1"
    candidate_dir.mkdir(parents=True)
    version_dir.mkdir(parents=True)

    (candidate_dir / "deployer_fix_error.dspy.json").write_text('{"optimized_instruction":"candidate"}\n')
    (version_dir / "deployer_fix_error.dspy.json").write_text('{"optimized_instruction":"promoted"}\n')
    (version_dir / "metadata.json").write_text(
        json.dumps(
            {
                "iteration": 1,
                "best_candidate_index": 1,
                "best_score": 0.5,
                "prompts": {"deployer_fix_error": {"optimized": True}},
            }
        )
    )

    code = run_command(
        Namespace(
            optimized_dir=optimized_dir,
            family="simple_LLM",
            strict=True,
            json=False,
        )
    )
    captured = capsys.readouterr().out
    assert code == 1
    assert "hash mismatch" in captured
