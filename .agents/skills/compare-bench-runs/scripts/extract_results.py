#!/usr/bin/env python3
"""Extract benchmark results from two SREGym crucible run directories.

Usage:
    python3 extract_results.py <run1_dir> <run2_dir> [--label1 run1] [--label2 run2]

Output: JSON to stdout with per-problem results and comparison metadata.
"""

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TypeAlias, cast

JsonValue: TypeAlias = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]


@dataclass(frozen=True)
class ProblemResult:
    success: bool | None
    ttl: float | None
    ttm: float | None
    accuracy: float | None
    diagnosis_iterations: int
    mitigation_iterations: int
    file: str

    def as_json(self) -> JsonObject:
        return {
            "success": self.success,
            "ttl": self.ttl,
            "ttm": self.ttm,
            "accuracy": self.accuracy,
            "diagnosis_iterations": self.diagnosis_iterations,
            "mitigation_iterations": self.mitigation_iterations,
            "file": self.file,
        }


@dataclass(frozen=True)
class CliArgs:
    run1_dir: Path
    run2_dir: Path
    label1: str
    label2: str


def _speedup_percentage(entry: JsonObject) -> float:
    value = entry["speedup_pct"]
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise TypeError("comparison speedup_pct must be numeric")
    return float(value)


def extract_problems(logdir: str | Path) -> dict[str, ProblemResult]:
    """Extract problem results from trajectory MD files in a run directory."""
    results: dict[str, ProblemResult] = {}
    for filepath in Path(logdir).iterdir():
        filename = filepath.name
        if filename.startswith("sregym_") and filename.endswith(".md"):
            m = re.match(r"sregym_\d+_\d+_w\d+_(.*?)\.md", filename)
            if not m:
                continue
            problem = m.group(1)
            content = filepath.read_text(encoding="utf-8")

            success_m = re.search(r"success: (True|False)", content)
            ttl_m = re.search(r'"TTL": ([0-9.]+)', content)
            ttm_m = re.search(r'"TTM": ([0-9.]+)', content)
            accuracy_m = re.search(r'"accuracy": ([0-9.]+)', content)

            # Count diagnosis iterations
            diag_iters = len(re.findall(r"### Iteration \d+ — Agent Hypothesis", content))
            # Count mitigation iterations
            mit_iters = len(re.findall(r"### Iteration \d+ — Agent Strategy", content))

            results[problem] = ProblemResult(
                success=success_m.group(1) == "True" if success_m else None,
                ttl=float(ttl_m.group(1)) if ttl_m else None,
                ttm=float(ttm_m.group(1)) if ttm_m else None,
                accuracy=float(accuracy_m.group(1)) if accuracy_m else None,
                diagnosis_iterations=diag_iters,
                mitigation_iterations=mit_iters,
                file=filename,
            )
    return results


def compare(
    r1: dict[str, ProblemResult],
    r2: dict[str, ProblemResult],
    label1: str,
    label2: str,
) -> JsonObject:
    """Compare two sets of results and produce a structured comparison."""
    all_problems = sorted(r1.keys() | r2.keys())
    both = sorted(r1.keys() & r2.keys())
    only1 = sorted(r1.keys() - r2.keys())
    only2 = sorted(r2.keys() - r1.keys())

    improved: list[JsonValue] = []
    regressed: list[JsonValue] = []
    both_succeeded: list[JsonObject] = []
    both_failed: list[JsonValue] = []

    for p in both:
        s1 = r1[p].success
        s2 = r2[p].success
        t1 = r1[p].ttl or 0
        t2 = r2[p].ttl or 0

        if s1 and s2:
            speedup = ((t1 - t2) / t1 * 100) if t1 > 0 else 0
            both_succeeded.append(
                {
                    "problem": p,
                    f"{label1}_ttl": t1,
                    f"{label2}_ttl": t2,
                    "speedup_pct": round(speedup, 1),
                }
            )
        elif not s1 and not s2:
            both_failed.append(
                {
                    "problem": p,
                    f"{label1}_ttl": t1,
                    f"{label2}_ttl": t2,
                }
            )
        elif not s1 and s2:
            improved.append(
                {
                    "problem": p,
                    f"{label1}_ttl": t1,
                    f"{label2}_ttl": t2,
                }
            )
        elif s1 and not s2:
            regressed.append(
                {
                    "problem": p,
                    f"{label1}_ttl": t1,
                    f"{label2}_ttl": t2,
                }
            )

    s1_count = sum(1 for result in r1.values() if result.success)
    s2_count = sum(1 for result in r2.values() if result.success)

    sorted_successes = cast(
        "list[JsonValue]",
        sorted(both_succeeded, key=_speedup_percentage, reverse=True),
    )
    only1_json = cast("list[JsonValue]", only1)
    only2_json = cast("list[JsonValue]", only2)
    per_problem: JsonObject = {
        problem: {
            label1: r1[problem].as_json() if problem in r1 else None,
            label2: r2[problem].as_json() if problem in r2 else None,
        }
        for problem in all_problems
    }

    return {
        "summary": {
            f"{label1}_total": len(r1),
            f"{label2}_total": len(r2),
            "in_both": len(both),
            f"{label1}_solved": s1_count,
            f"{label2}_solved": s2_count,
            f"{label1}_solve_rate": round(s1_count / len(r1) * 100, 1) if r1 else 0,
            f"{label2}_solve_rate": round(s2_count / len(r2) * 100, 1) if r2 else 0,
        },
        "improved": improved,
        "regressed": regressed,
        "both_succeeded": sorted_successes,
        "both_failed": both_failed,
        f"only_in_{label1}": only1_json,
        f"only_in_{label2}": only2_json,
        "per_problem": per_problem,
    }


def _parse_args(argv: list[str] | None = None) -> CliArgs:
    parser = argparse.ArgumentParser(description="Extract and compare SREGym benchmark results")
    parser.add_argument("run1_dir", help="Path to first run directory")
    parser.add_argument("run2_dir", help="Path to second run directory")
    parser.add_argument("--label1", default="run1", help="Label for first run")
    parser.add_argument("--label2", default="run2", help="Label for second run")
    args = parser.parse_args(argv)
    return CliArgs(
        run1_dir=Path(str(args.run1_dir)),
        run2_dir=Path(str(args.run2_dir)),
        label1=str(args.label1),
        label2=str(args.label2),
    )


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)

    r1 = extract_problems(args.run1_dir)
    r2 = extract_problems(args.run2_dir)
    comparison = compare(r1, r2, args.label1, args.label2)
    json.dump(comparison, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
