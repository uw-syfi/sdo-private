from __future__ import annotations

import json
from typing import TYPE_CHECKING

from benchmarks.sregym.analysis.composite_table import format_table, rows

if TYPE_CHECKING:
    from pathlib import Path


def test_table_joins_the_composite_report_with_the_incident_record(tmp_path: Path) -> None:
    (tmp_path / "composite_000_composite3_x.json").write_text(
        json.dumps(
            {
                "agent": "codex",
                "problem_id": "composite3_x",
                "faults_resolved": 2,
                "faults_total": 3,
                "all_resolved_s": None,
                "resolved_s": {
                    "readiness:geo": 40.0,
                    "configmap:mongodb-rate": 90.0,
                    "network_policy:recommendation": None,
                },
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "incidents.jsonl").write_text(
        json.dumps(
            {
                "index": 0,
                "oracle": {"success": False, "details": {"accuracy": 66.67}},
                "responder_tokens": {"total_tokens": 100},
                "reflection_tokens": {"total_tokens": 5},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    table = rows([tmp_path])

    assert table[0]["resolved"] == "2/3"
    assert table[0]["tokens"] == 105
    assert table[0]["oracle_success"] is False
    text = format_table(table)
    assert "readiness:40" in text
    assert "network_policy:-" in text
