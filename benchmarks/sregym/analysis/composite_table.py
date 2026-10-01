"""Per-run table for composite (multi-fault) fast-loop results.

Reads ``composite_<index>_<problem>.json`` (written by ``benchmarks.sregym.fastloop.composite``)
and ``incidents.jsonl`` from one or more fast-loop results directories.

    uv run python -m benchmarks.sregym.analysis.composite_table <results-dir>...
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _load_incidents(directory: Path) -> dict[int, dict[str, Any]]:
    path = directory / "incidents.jsonl"
    if not path.is_file():
        return {}
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return {int(record["index"]): record for record in records}


def rows(directories: list[Path]) -> list[dict[str, Any]]:
    table: list[dict[str, Any]] = []
    for directory in directories:
        incidents = _load_incidents(directory)
        for report_path in sorted(directory.glob("composite_*.json")):
            report = json.loads(report_path.read_text(encoding="utf-8"))
            index = int(report_path.name.split("_")[1])
            record = incidents.get(index, {})
            usage = record.get("responder_tokens") or {}
            reflection = record.get("reflection_tokens") or {}
            oracle = (record.get("oracle") or {}).get("details", {})
            table.append(
                {
                    "run": directory.name,
                    "index": index,
                    "agent": report.get("agent"),
                    "problem": report.get("problem_id"),
                    "resolved": f"{report['faults_resolved']}/{report['faults_total']}",
                    "all_resolved_s": report.get("all_resolved_s"),
                    "per_fault_s": report.get("resolved_s"),
                    "oracle_accuracy": oracle.get("accuracy"),
                    "oracle_success": (record.get("oracle") or {}).get("success"),
                    "stop_reason": report.get("stop_reason"),
                    "incidents": len(report.get("incidents", [])) or None,
                    "tokens": (usage.get("total_tokens") or 0) + (reflection.get("total_tokens") or 0),
                    "responder_tokens": usage.get("total_tokens"),
                    "error": (record.get("error") or "")[:80] or None,
                }
            )
    return table


def format_table(table: list[dict[str, Any]]) -> str:
    if not table:
        return "no composite reports found"
    header = [
        "run",
        "#",
        "agent",
        "resolved",
        "all_s",
        "oracle",
        "stop",
        "incidents",
        "tokens",
        "per-fault resolution (s)",
    ]
    lines = [header]
    for row in table:
        per_fault = ", ".join(
            f"{name.split(':')[0]}:{'-' if value is None else f'{value:.0f}'}"
            for name, value in row["per_fault_s"].items()
        )
        lines.append(
            [
                str(row["run"]),
                str(row["index"]),
                str(row["agent"]),
                row["resolved"],
                "-" if row["all_resolved_s"] is None else f"{row['all_resolved_s']:.0f}",
                str(row["oracle_success"]),
                str(row["stop_reason"] or "-"),
                str(row["incidents"] or "-"),
                str(row["tokens"]),
                per_fault,
            ]
        )
    widths = [max(len(line[column]) for line in lines) for column in range(len(header))]
    return "\n".join("  ".join(cell.ljust(width) for cell, width in zip(line, widths, strict=True)) for line in lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directories", type=Path, nargs="+")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    table = rows([path for directory in args.directories for path in ([directory, *sorted(directory.glob("*/"))])])
    print(json.dumps(table, indent=2) if args.json else format_table(table))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
