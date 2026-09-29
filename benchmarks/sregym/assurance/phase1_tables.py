"""Per-problem, per-arm tables for ``PHASE1_RESULTS.md`` (headline and latent-fault splits).

Reads the same run directories as :mod:`benchmarks.sregym.assurance.phase1_analyze`
and prints Markdown tables plus a JSON dump. Everything is judge-free
(``Verdict.ttm_seconds``); token weights are PLAN.md's (cache_read 0.1,
output 8). Time cells use runs that passed end to end, because a failed run
has no time to mitigation; success cells use every run.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from benchmarks.sregym.analysis.incident_cost import (
    CodexRun,
    SdoStage,
    load_codex_runs,
    load_sdo_pipeline,
    problem_results_dirs,
)
from benchmarks.sregym.analysis.stats import newcombe_interval, stratified_bootstrap_ratio, wilson_interval
from benchmarks.sregym.assurance.phase1_analyze import PLAN_TOKEN_WEIGHTS, split_rounds
from benchmarks.sregym.assurance.phase1_timeline import _receipt

if TYPE_CHECKING:
    from collections.abc import Sequence

S1 = "missing_configmap_hotel_reservation"
S2 = "wrong_service_selector_hotel_reservation"
S3 = "network_policy_block"
K1 = "composite_policy_and_rate_configmap_hotel_reservation"
K2 = "composite_frontend_selector_and_readiness_hotel_reservation"
HEADLINE = (S1, S2, K2)
LATENT = (S3, K1)
SHORT = {S1: "S1", S2: "S2", S3: "S3", K1: "K1", K2: "K2"}
#: A diagnosis that leans on the failure-admin decoy scripts (revoked MongoDB privileges) or the benign log-level drift.
DECOY_PATTERN = re.compile(r"failure-admin|revok|readWrite|privilege|LOG_LEVEL", re.IGNORECASE)


@dataclass(frozen=True)
class Row:
    arm: str  # "sdo-r1", "sdo-r2" or "codex"
    problem: str
    diagnosis: bool | None
    mitigation: bool | None
    ttd: float | None
    ttm: float | None
    last_mut: float | None
    uncached: int
    cache_read: int
    output: int
    reasoning: int
    weighted: float
    weighted_with_reflection: float
    decoy_cited: bool
    closed_by_controller: bool | None
    source: str

    @property
    def e2e(self) -> bool:
        return self.diagnosis is True and self.mitigation is True


def _diagnosis_text(results: str) -> str:
    for path in sorted(Path(results).rglob("*_results.csv")):
        if "ALL" in path.name:
            continue
        rows = list(csv.DictReader(path.open(encoding="utf-8")))
        if rows and rows[0].get("Diagnosis.submission"):
            return rows[0]["Diagnosis.submission"]
    return ""


def _sdo_row(arm: str, stage: SdoStage) -> Row:
    v = stage.verdict
    usage = stage.responder
    receipt = _receipt(Path(stage.source))
    closed = None
    if receipt:
        closed = bool(receipt.get("incident_resolution_seconds"))
    return Row(
        arm,
        stage.problem_id,
        v.diagnosis,
        v.mitigation,
        v.diagnosis_seconds,
        v.ttm_seconds,
        v.last_mitigation_seconds,
        usage.uncached_input_tokens,
        usage.cached_input_tokens,
        usage.output_tokens,
        usage.reasoning_output_tokens,
        usage.weighted(PLAN_TOKEN_WEIGHTS),
        usage.weighted(PLAN_TOKEN_WEIGHTS) + stage.reflection.weighted(PLAN_TOKEN_WEIGHTS),
        bool(DECOY_PATTERN.search(_diagnosis_text(stage.source))),
        closed,
        stage.source,
    )


def _codex_row(run: CodexRun) -> Row:
    v = run.verdict
    usage = run.tokens
    weighted = usage.weighted(PLAN_TOKEN_WEIGHTS)
    return Row(
        "codex",
        run.problem_id,
        v.diagnosis,
        v.mitigation,
        v.diagnosis_seconds,
        v.ttm_seconds,
        v.last_mitigation_seconds,
        usage.uncached_input_tokens,
        usage.cached_input_tokens,
        usage.output_tokens,
        usage.reasoning_output_tokens,
        weighted,
        weighted,
        bool(DECOY_PATTERN.search(_diagnosis_text(run.source))),
        None,
        run.source,
    )


def load_rows(sdo_dirs: Sequence[Path], codex_dirs: Sequence[Path]) -> list[Row]:
    rows: list[Row] = []
    for directory in sdo_dirs:
        stages = load_sdo_pipeline(directory)
        # Stages with no result row (a detection miss) are absent from load_sdo_pipeline; add them as failures.
        present = {stage.source for stage in stages}
        first, second = split_rounds(_with_missing(directory, stages, present))
        rows += [_row_for(stage, "sdo-r1") for stage in first]
        rows += [_row_for(stage, "sdo-r2") for stage in second]
    for directory in codex_dirs:
        rows += [_codex_row(run) for run in load_codex_runs(directory)]
    return rows


def _row_for(stage: SdoStage, arm: str) -> Row:
    return _sdo_row(arm, stage)


def _with_missing(directory: Path, stages: list[SdoStage], present: set[str]) -> list[SdoStage]:
    """Every pipeline has 10 stages; ``load_sdo_pipeline`` already returns no-verdict stages, so this only checks."""

    if len(stages) % 2:
        raise ValueError(f"{directory}: odd number of stages ({len(stages)})")
    return stages


def _med(values: Sequence[float]) -> float | None:
    return statistics.median(values) if values else None


def _fmt(value: float | None, digits: int = 0) -> str:
    return "-" if value is None else f"{value:,.{digits}f}"


def _prop(k: int, n: int) -> str:
    if n == 0:
        return "-"
    w = wilson_interval(k, n)
    return f"{k}/{n} ({w.low:.2f}-{w.high:.2f})"


def cell(rows: Sequence[Row]) -> dict[str, Any]:
    passed = [r for r in rows if r.e2e]
    return {
        "n": len(rows),
        "diag": sum(1 for r in rows if r.diagnosis is True),
        "mit": sum(1 for r in rows if r.mitigation is True),
        "e2e": len(passed),
        "no_verdict": sum(1 for r in rows if r.diagnosis is None and r.mitigation is None),
        "ttd": _med([r.ttd for r in passed if r.ttd is not None]),
        "ttm": _med([r.ttm for r in passed if r.ttm is not None]),
        "last_mut": _med([r.last_mut for r in passed if r.last_mut is not None]),
        "uncached": _med([r.uncached for r in rows if r.weighted]),
        "cache_read": _med([r.cache_read for r in rows if r.weighted]),
        "output": _med([r.output for r in rows if r.weighted]),
        "reasoning": _med([r.reasoning for r in rows if r.weighted]),
        "weighted": _med([r.weighted for r in rows if r.weighted]),
        "weighted_with_reflection": _med([r.weighted_with_reflection for r in rows if r.weighted]),
        "decoy_cited": sum(1 for r in rows if r.decoy_cited),
        "decoy_driven": sum(1 for r in rows if r.decoy_cited and r.diagnosis is False),
    }


ARMS = (("sdo-r1", "SDO round 1 (first encounter)"), ("sdo-r2", "SDO round 2 (repeat)"), ("codex", "Codex (memoryless)"))


def render_problem_table(rows: Sequence[Row], problems: Sequence[str]) -> str:
    lines = [
        "| Problem | Arm | n | Diagnosis | Mitigation | End-to-end (Wilson 95%) | TTD s (median) | TTM s (median) "
        "| last-mutation s | uncached in | cache read | output | reasoning | weighted | weighted + reflection |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for problem in problems:
        for arm, label in ARMS:
            subset = [r for r in rows if r.problem == problem and r.arm == arm]
            c = cell(subset)
            n = c["n"]
            lines.append(
                f"| {SHORT[problem]} | {label} | {n} | {c['diag']}/{n} | {c['mit']}/{n} | {_prop(c['e2e'], n)} "
                f"| {_fmt(c['ttd'], 1)} | {_fmt(c['ttm'], 1)} | {_fmt(c['last_mut'], 1)} | {_fmt(c['uncached'])} "
                f"| {_fmt(c['cache_read'])} | {_fmt(c['output'])} | {_fmt(c['reasoning'])} | {_fmt(c['weighted'])} "
                f"| {_fmt(c['weighted_with_reflection'])} |"
            )
    return "\n".join(lines)


def _cells(rows: Sequence[Row], arm_num: str, arm_den: str, problems: Sequence[str], field: str, *, passed_only: bool):
    out = {}
    for problem in problems:
        pick = lambda arm: [  # noqa: E731
            getattr(r, field)
            for r in rows
            if r.problem == problem and r.arm == arm and (r.e2e or not passed_only) and getattr(r, field) is not None
        ]
        out[problem] = (pick(arm_num), pick(arm_den))
    return out


def ratio_line(label: str, rows: Sequence[Row], num: str, den: str, problems: Sequence[str], field: str, *, passed_only: bool) -> str:
    res = stratified_bootstrap_ratio(_cells(rows, num, den, problems, field, passed_only=passed_only))
    if res.point is None or res.ci_low is None or res.ci_high is None:
        return f"| {label} | - | - | - |"
    per = ", ".join(f"{SHORT[p]} {v:.2f}" for p, v in res.per_cell.items() if v is not None)
    return f"| {label} | {res.point:.2f} | {res.ci_low:.2f}-{res.ci_high:.2f} | {per} |"


def render_ratios(rows: Sequence[Row], problems: Sequence[str]) -> str:
    head = ["| Ratio | Pooled | 95% CI (bootstrap) | Per problem |", "|---|---|---|---|"]
    body = [
        ratio_line("TTM: Codex / SDO repeat (e2e passes only)", rows, "codex", "sdo-r2", problems, "ttm", passed_only=True),
        ratio_line("TTM: Codex / SDO first encounter", rows, "codex", "sdo-r1", problems, "ttm", passed_only=True),
        ratio_line("TTM: SDO repeat / SDO first (C5)", rows, "sdo-r2", "sdo-r1", problems, "ttm", passed_only=True),
        ratio_line("last-mutation: Codex / SDO repeat", rows, "codex", "sdo-r2", problems, "last_mut", passed_only=True),
        ratio_line("last-mutation: SDO repeat / SDO first", rows, "sdo-r2", "sdo-r1", problems, "last_mut", passed_only=True),
        ratio_line("weighted tokens: SDO repeat (responder) / Codex", rows, "sdo-r2", "codex", problems, "weighted", passed_only=False),
        ratio_line("weighted tokens: SDO repeat (+reflection) / Codex", rows, "sdo-r2", "codex", problems, "weighted_with_reflection", passed_only=False),
        ratio_line("weighted tokens: SDO first (responder) / Codex", rows, "sdo-r1", "codex", problems, "weighted", passed_only=False),
        ratio_line("weighted tokens: SDO first (+reflection) / Codex", rows, "sdo-r1", "codex", problems, "weighted_with_reflection", passed_only=False),
        ratio_line("weighted tokens: SDO repeat / SDO first (responder)", rows, "sdo-r2", "sdo-r1", problems, "weighted", passed_only=False),
    ]
    return "\n".join([*head, *body])


def render_e2e_diffs(rows: Sequence[Row], problems: Sequence[str]) -> str:
    def counts(arms: Sequence[str]) -> tuple[int, int]:
        sub = [r for r in rows if r.problem in problems and r.arm in arms]
        return sum(1 for r in sub if r.e2e), len(sub)

    lines = ["| Comparison | Difference in e2e rate | Newcombe 95% CI |", "|---|---|---|"]
    for label, a, b in (
        ("SDO (both rounds) - Codex", ("sdo-r1", "sdo-r2"), ("codex",)),
        ("SDO round 1 - Codex", ("sdo-r1",), ("codex",)),
        ("SDO round 2 - Codex", ("sdo-r2",), ("codex",)),
    ):
        k1, n1 = counts(a)
        k2, n2 = counts(b)
        if n1 and n2:
            diff = newcombe_interval(k1, n1, k2, n2)
            lines.append(f"| {label} ({k1}/{n1} vs {k2}/{n2}) | {diff.point:+.2f} | {diff.low:+.2f} to {diff.high:+.2f} |")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n", 1)[0])
    parser.add_argument("--sdo", type=Path, nargs="+", required=True)
    parser.add_argument("--codex", type=Path, nargs="+", required=True)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args(argv)
    rows = load_rows(args.sdo, args.codex)
    print("## Headline set (S1, S2, K2)\n")
    print(render_problem_table(rows, HEADLINE), "\n")
    print(render_ratios(rows, HEADLINE), "\n")
    print(render_e2e_diffs(rows, HEADLINE), "\n")
    print("## Latent set (S3, K1)\n")
    print(render_problem_table(rows, LATENT), "\n")
    print(render_e2e_diffs(rows, LATENT), "\n")
    print("## All five\n")
    print(render_e2e_diffs(rows, (*HEADLINE, *LATENT)), "\n")
    if args.json:
        payload = {
            f"{SHORT[p]}/{arm}": cell([r for r in rows if r.problem == p and r.arm == arm])
            for p in (*HEADLINE, *LATENT)
            for arm, _ in ARMS
        }
        args.json.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
