#!/usr/bin/env python3
"""Extract benchmark results from two SREGym crucible run directories.

Usage:
    python3 extract_results.py <run1_dir> <run2_dir> [--label1 run1] [--label2 run2]

Output: JSON to stdout with per-problem results and comparison metadata.
"""

import argparse
import json
import os
import re
import sys


def extract_problems(logdir):
    """Extract problem results from crucible/<problem>/{diagnosis,mitigation}.md files."""
    results = {}
    crucible_dir = os.path.join(logdir, "crucible")
    if not os.path.isdir(crucible_dir):
        return results
    for problem in os.listdir(crucible_dir):
        problem_dir = os.path.join(crucible_dir, problem)
        if not os.path.isdir(problem_dir):
            continue

        content = ""
        for name in ("diagnosis.md", "mitigation.md"):
            path = os.path.join(problem_dir, name)
            if os.path.exists(path):
                with open(path) as fh:
                    content += fh.read()
        if not content:
            continue

        success_m = re.search(r"success: (True|False)", content)
        ttl_m = re.search(r'"TTL": ([0-9.]+)', content)
        ttm_m = re.search(r'"TTM": ([0-9.]+)', content)
        accuracy_m = re.search(r'"accuracy": ([0-9.]+)', content)

        diag_iters = len(re.findall(r"### Iteration \d+ — Agent Hypothesis", content))
        mit_iters = len(re.findall(r"### Iteration \d+ — Agent Strategy", content))

        results[problem] = {
            "success": success_m.group(1) == "True" if success_m else None,
            "ttl": float(ttl_m.group(1)) if ttl_m else None,
            "ttm": float(ttm_m.group(1)) if ttm_m else None,
            "accuracy": float(accuracy_m.group(1)) if accuracy_m else None,
            "diagnosis_iterations": diag_iters,
            "mitigation_iterations": mit_iters,
            "dir": os.path.join("crucible", problem),
        }
    return results


def compare(r1, r2, label1, label2):
    """Compare two sets of results and produce a structured comparison."""
    all_problems = sorted(set(list(r1.keys()) + list(r2.keys())))
    both = sorted(set(r1.keys()) & set(r2.keys()))
    only1 = sorted(set(r1.keys()) - set(r2.keys()))
    only2 = sorted(set(r2.keys()) - set(r1.keys()))

    improved = []
    regressed = []
    both_succeeded = []
    both_failed = []

    for p in both:
        s1 = r1[p].get("success")
        s2 = r2[p].get("success")
        t1 = r1[p].get("ttl", 0) or 0
        t2 = r2[p].get("ttl", 0) or 0

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

    s1_count = sum(1 for p in r1 if r1[p].get("success"))
    s2_count = sum(1 for p in r2 if r2[p].get("success"))

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
        "both_succeeded": sorted(both_succeeded, key=lambda x: x["speedup_pct"], reverse=True),
        "both_failed": both_failed,
        f"only_in_{label1}": only1,
        f"only_in_{label2}": only2,
        "per_problem": {
            p: {
                label1: r1.get(p),
                label2: r2.get(p),
            }
            for p in all_problems
        },
    }


def main():
    parser = argparse.ArgumentParser(description="Extract and compare SREGym benchmark results")
    parser.add_argument("run1_dir", help="Path to first run directory")
    parser.add_argument("run2_dir", help="Path to second run directory")
    parser.add_argument("--label1", default="run1", help="Label for first run")
    parser.add_argument("--label2", default="run2", help="Label for second run")
    args = parser.parse_args()

    r1 = extract_problems(args.run1_dir)
    r2 = extract_problems(args.run2_dir)
    comparison = compare(r1, r2, args.label1, args.label2)
    json.dump(comparison, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
