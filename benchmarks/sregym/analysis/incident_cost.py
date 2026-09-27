"""Per-stage and cumulative incident cost of an SDO pipeline against stock Codex.

Given one SDO pipeline directory (``logs/<ts>_pipeline_<name>/``) and any number
of Codex run directories (plain experiment or pipeline directories), print for
every SDO stage:

- time to diagnosis, ``ttd_s = diagnosis_submitted_at - fault_injected_at``
  (no judge time falls inside it);
- the headline time to mitigation ``ttm_s``, which excludes judge time: the
  raw time ``mitigation_submitted_at - fault_injected_at`` minus the conductor's
  diagnosis grading wait (``fault_injected_at + TTL - diagnosis_submitted_at``;
  a mitigation POST cannot land before the mitigation stage opens), but never
  less than when the agent's last state-changing command before its mitigation
  submission completed (``last_mut_s``, from the exported Codex rollout), since
  subtracting the whole grading wait undercounts an agent that kept repairing
  while the judge graded its diagnosis; ``ttm_s`` is unknown without the TTL;
- supplementary times: the raw ``raw_incl_judge_s`` (includes diagnosis
  grading), the judge-excluded ``no_judge_s`` alone, when the first
  state-changing command was issued (``first_mut_s``), and the receipt's
  ``incident_resolution_seconds`` (none of them includes reflection);
- oracle verdicts (diagnosis and mitigation);
- responder ("incident") tokens, reflection tokens and reflection wall time in
  separate columns (asynchronous learning is not resolution time);
- the receipt's ``memory_reuse.warm_path`` and whether the responder prompt
  actually contained the warm-path instructions (from exported Codex rollouts);
- operational-memory size after the stage (detectors and playbooks in ``.sdo/``).

It then prints cumulative incident tokens (responder only) and cumulative tokens
including learning (responder plus reflection) against the cumulative Codex
tokens for the same problem sequence, the one-time lifecycle tokens, and the
break-even stage for each measure. Codex is memoryless, so its cost for a
problem is the mean over every Codex run of that problem.

Tokens are ``input_tokens + output_tokens`` as reported by the provider (Codex
``input_tokens`` already includes cached input); ``--uncached`` subtracts
``cached_input_tokens``.

Usage::

    uv run python -m benchmarks.sregym.analysis.incident_cost \\
        third_party/sregym/logs/<ts>_pipeline_sdo-codex-luna-sequence \\
        --codex third_party/sregym/logs/<ts>_codex third_party/sregym/logs/<ts2>_codex \\
        [--lifecycle-usage path/to/sdo_turn_usage.jsonl] [--uncached] [--json out.json]
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from sdo.operational_memory import incident_worktree_dirname

WARM_PROMPT_MARKER = "Warm path: validated incident memory matches this incident."
RECEIPT_NAME = "sdo_production_receipt_strict.json"
LIFECYCLE_USAGE_NAME = "sdo_turn_usage.jsonl"
_RENAMED_STAGE = re.compile(r"\.\d{8}_\d{6}$")
_STAGE_DIR = re.compile(r"^stage_(\d+)_(.+)$")


class IncidentCostError(ValueError):
    """Raised when a result directory lacks the evidence this analysis needs."""


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0

    def __post_init__(self) -> None:
        for name in ("input_tokens", "cached_input_tokens", "output_tokens"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool):
                raise TypeError(f"{name} must be an int, got {value!r}")
            if value < 0:
                raise ValueError(f"{name} must not be negative")

    @classmethod
    def from_mapping(cls, usage: object) -> TokenUsage:
        if not isinstance(usage, dict):
            return cls()
        return cls(
            input_tokens=int(usage.get("input_tokens") or 0),
            cached_input_tokens=int(usage.get("cached_input_tokens") or 0),
            output_tokens=int(usage.get("output_tokens") or 0),
        )

    def __add__(self, other: TokenUsage) -> TokenUsage:
        return TokenUsage(
            self.input_tokens + other.input_tokens,
            self.cached_input_tokens + other.cached_input_tokens,
            self.output_tokens + other.output_tokens,
        )

    def total(self, *, uncached: bool = False) -> int:
        cached = self.cached_input_tokens if uncached else 0
        return self.input_tokens - cached + self.output_tokens


@dataclass(frozen=True)
class Verdict:
    """Harness grading of one problem run and its agent-neutral timing."""

    diagnosis: bool | None
    mitigation: bool | None
    #: ``mitigation_submitted_at - fault_injected_at``; includes the diagnosis grading wait.
    raw_incl_judge_seconds: float | None
    #: Diagnosis POST until the conductor opened the mitigation stage (judge grading).
    grading_wait_seconds: float | None = None
    #: Injection until the agent issued its first state-changing command.
    mitigation_applied_seconds: float | None = None
    #: Time to diagnosis: ``diagnosis_submitted_at - fault_injected_at``.
    diagnosis_seconds: float | None = None
    #: Injection until the agent's last state-changing command before its mitigation POST completed.
    last_mitigation_seconds: float | None = None

    @property
    def passed(self) -> bool:
        return self.diagnosis is True and self.mitigation is True

    @property
    def judge_excluded_seconds(self) -> float | None:
        """Raw time without the diagnosis grading wait the mitigation POST sat behind."""

        if self.raw_incl_judge_seconds is None or self.grading_wait_seconds is None:
            return None
        return self.raw_incl_judge_seconds - self.grading_wait_seconds

    @property
    def ttm_seconds(self) -> float | None:
        """Headline time to mitigation, excluding judge time.

        The judge-excluded time, but never less than when the last mitigating
        command completed: work done while the judge graded the diagnosis is
        agent time, and subtracting the whole grading wait would drop it.
        """

        excluded = self.judge_excluded_seconds
        if excluded is None:
            return None
        if self.last_mitigation_seconds is None:
            return excluded
        return max(excluded, self.last_mitigation_seconds)


@dataclass(frozen=True)
class MemorySize:
    detectors: int
    incident_detectors: int
    playbooks: int


@dataclass(frozen=True)
class SdoStage:
    index: int
    name: str
    problem_id: str
    verdict: Verdict
    incident_resolution_seconds: float | None
    responder: TokenUsage
    reflection: TokenUsage
    reflection_attempts: int | None
    reflection_skipped_reason: str | None
    learning_seconds: float | None
    reflection_turn_seconds: float | None
    warm_path: bool | None
    match_reasons: tuple[str, ...]
    warm_prompt: bool | None
    memory: MemorySize | None
    lifecycle: TokenUsage


@dataclass(frozen=True)
class CodexRun:
    problem_id: str
    verdict: Verdict
    tokens: TokenUsage
    source: str


@dataclass(frozen=True)
class CodexProblemCost:
    problem_id: str
    runs: int
    passed: int
    mean_tokens: float
    mean_raw_incl_judge_seconds: float | None
    mean_judge_excluded_seconds: float | None = None
    mean_mitigation_applied_seconds: float | None = None
    mean_ttd_seconds: float | None = None
    mean_ttm_seconds: float | None = None
    mean_last_mitigation_seconds: float | None = None


@dataclass(frozen=True)
class BreakEven:
    measure: str
    includes_lifecycle: bool
    stage: int | None
    final_gap: float
    projected_extra_repeats: int | None


@dataclass(frozen=True)
class CumulativeRow:
    index: int
    sdo_incident_tokens: int
    sdo_tokens_with_learning: int
    codex_tokens: float | None
    sdo_ttm_seconds: float | None
    codex_ttm_seconds: float | None


@dataclass
class Report:
    stages: list[SdoStage]
    codex: dict[str, CodexProblemCost]
    cumulative: list[CumulativeRow]
    lifecycle_tokens: int
    break_even: list[BreakEven] = field(default_factory=list)
    uncached: bool = False


# --------------------------------------------------------------------------- loading


def _truthy(value: object) -> bool | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in {"true", "1", "yes"}:
        return True
    if text in {"false", "0", "no"}:
        return False
    return None


def _float(value: object) -> float | None:
    try:
        number = float(str(value))
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def read_verdict(results_dir: Path) -> Verdict | None:
    """Read the last row of the problem run's ``*_ALL_results.csv`` (or any results CSV)."""

    candidates = sorted(results_dir.glob("*_ALL_results.csv")) or sorted(results_dir.rglob("*_results.csv"))
    for path in candidates:
        with path.open(encoding="utf-8", newline="") as stream:
            rows = list(csv.DictReader(stream))
        if not rows:
            continue
        row = rows[-1]
        injected = _float(row.get("fault_injected_at"))
        submitted = _float(row.get("mitigation_submitted_at"))
        raw = submitted - injected if injected is not None and submitted is not None else None
        # The conductor records TTL when the diagnosis verdict completes, right before it opens the
        # mitigation stage, on a clock that starts when the fault is injected.
        diagnosed = _float(row.get("diagnosis_submitted_at"))
        ttl = _float(row.get("TTL"))
        grading_wait = (
            max(0.0, injected + ttl - diagnosed)
            if injected is not None and diagnosed is not None and ttl is not None
            else None
        )
        return Verdict(
            diagnosis=_truthy(row.get("Diagnosis.success")),
            mitigation=_truthy(row.get("Mitigation.success")),
            raw_incl_judge_seconds=raw,
            grading_wait_seconds=grading_wait,
            diagnosis_seconds=diagnosed - injected if injected is not None and diagnosed is not None else None,
        )
    return None


_KUBECTL_WRITES = frozenset(
    {"apply", "create", "patch", "replace", "delete", "rollout", "set", "scale", "edit", "label", "annotate"}
)
_KUBECTL_ROLLOUT_WRITES = frozenset({"restart", "undo", "pause", "resume"})
_KUBECTL_VALUE_FLAGS = frozenset({"-n", "--namespace", "--context", "--kubeconfig", "-l", "--selector"})
_CODE_MODE_CMD = re.compile(r'\bcmd\s*:\s*("(?:[^"\\]|\\.)*")')
_COMMAND_SPLIT = re.compile(r"&&|\|\||[;|\n]")


def _rollout_commands(payload: dict[str, Any]) -> list[str]:
    """Shell commands of one Codex tool call (plain ``exec_command`` or code-mode ``exec``)."""

    if payload.get("type") == "function_call":
        try:
            arguments = json.loads(str(payload.get("arguments") or "{}"))
        except json.JSONDecodeError:
            return []
        command = arguments.get("cmd") or arguments.get("command") if isinstance(arguments, dict) else None
        if isinstance(command, list):
            command = " ".join(str(part) for part in command)
        return [command] if isinstance(command, str) else []
    if payload.get("type") == "custom_tool_call":
        commands = []
        for literal in _CODE_MODE_CMD.findall(str(payload.get("input") or "")):
            try:
                commands.append(json.loads(literal))
            except json.JSONDecodeError:
                continue
        return commands
    return []


def _is_state_change(command: str) -> bool:
    """Whether one shell command changes cluster state: a kubectl write or a playbook repair script."""

    for segment in _COMMAND_SPLIT.split(command):
        tokens = segment.split()
        while tokens and ("=" in tokens[0] and not tokens[0].startswith("-") or tokens[0] in {"env", "sudo"}):
            tokens = tokens[1:]
        if not tokens:
            continue
        program = tokens[0]
        if Path(program).name == "kubectl":
            if any(token.startswith("--dry-run") for token in tokens):
                continue
            rest = iter(tokens[1:])
            for token in rest:
                if token in _KUBECTL_VALUE_FLAGS:
                    next(rest, None)
                elif not token.startswith("-"):
                    if token == "rollout":
                        action = next((item for item in rest if not item.startswith("-")), "")
                        if action in _KUBECTL_ROLLOUT_WRITES:
                            return True
                    elif token in _KUBECTL_WRITES:
                        return True
                    break
            continue
        if program in {"bash", "sh"}:
            arguments = [token for token in tokens[1:] if not token.startswith("-")]
            if any(token.startswith("-n") for token in tokens[1:2]):
                continue  # ``bash -n`` only checks syntax
            program = arguments[0] if arguments else ""
        if Path(program).parent.name == "scripts" and Path(program).name.startswith("repair"):
            return True
    return False


def _timestamp(value: object) -> float | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def first_mutation_at(rollouts: list[Path], *, after: float) -> float | None:
    """Epoch of the first state-changing tool call issued at or after *after* in Codex rollouts."""

    first: float | None = None
    for path in rollouts:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if '"function_call"' not in line and '"custom_tool_call"' not in line:
                continue
            record = json.loads(line)
            payload = record.get("payload")
            issued = _timestamp(record.get("timestamp"))
            if not isinstance(payload, dict) or issued is None or issued < after:
                continue
            if (first is None or issued < first) and any(_is_state_change(c) for c in _rollout_commands(payload)):
                first = issued
    return first


_TOOL_OUTPUTS = frozenset({"function_call_output", "custom_tool_call_output"})


def last_mutation_done_at(rollouts: list[Path], *, after: float, before: float) -> float | None:
    """Epoch at which the last state-changing tool call issued in ``[after, before]`` completed.

    Completion is the call's output record (paired by ``call_id``), capped at
    *before* (the mitigation POST); a call without a recorded output counts at
    its issue time.
    """

    issued_at: dict[str, float] = {}
    done_at: dict[str, float] = {}
    last: float | None = None
    for path in rollouts:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if '"call_id"' not in line and '"custom_tool_call"' not in line and '"function_call"' not in line:
                continue
            record = json.loads(line)
            payload = record.get("payload")
            stamp = _timestamp(record.get("timestamp"))
            if not isinstance(payload, dict) or stamp is None:
                continue
            call_id = str(payload.get("call_id") or "")
            if payload.get("type") in _TOOL_OUTPUTS:
                if call_id:
                    done_at[call_id] = stamp
                continue
            if not after <= stamp <= before:
                continue
            if not any(_is_state_change(command) for command in _rollout_commands(payload)):
                continue
            if call_id:
                issued_at[call_id] = stamp
            else:
                last = stamp if last is None else max(last, stamp)
    for call_id, issued in issued_at.items():
        done = min(done_at.get(call_id, issued), before)
        last = done if last is None else max(last, done)
    return last


def _with_mitigation_applied(verdict: Verdict, results_dir: Path, rollouts: list[Path]) -> Verdict:
    injected = submitted = None
    for path in sorted(results_dir.glob("*_ALL_results.csv")) or sorted(results_dir.rglob("*_results.csv")):
        with path.open(encoding="utf-8", newline="") as stream:
            rows = list(csv.DictReader(stream))
        if rows:
            injected = _float(rows[-1].get("fault_injected_at"))
            submitted = _float(rows[-1].get("mitigation_submitted_at"))
            break
    if injected is None or not rollouts:
        return verdict
    applied = first_mutation_at(rollouts, after=injected)
    last = last_mutation_done_at(rollouts, after=injected, before=submitted) if submitted is not None else None
    return replace(
        verdict,
        mitigation_applied_seconds=applied - injected if applied is not None else None,
        last_mitigation_seconds=last - injected if last is not None else None,
    )


def _problem_results_dirs(experiment_dir: Path) -> list[tuple[str, Path]]:
    """Return ``(problem_id, results_dir)`` in sequence order for one experiment directory."""

    return [
        (run_dir.name.split("_", 1)[1], results)
        for run_dir in sorted((experiment_dir / "runs").glob("*_*"))
        for results in sorted(run_dir.glob("worker_*/results"))
    ]


def _sum_usage_jsonl(paths: list[Path], *, incident_id: str | None = None) -> tuple[TokenUsage, float | None]:
    """Sum per-turn usage records, optionally only the turns run in one incident's worktree.

    A persistent controller's usage log accumulates every incident it served,
    so a stage filters by its receipt's incident; the broker runs reflection in
    the incident worktree, whose directory name is derived from the incident ID.
    """

    worktree = incident_worktree_dirname(incident_id) if incident_id else None
    usage = TokenUsage()
    seconds: float | None = None
    for path in paths:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            if worktree is not None and Path(str(record.get("cwd") or "")).name != worktree:
                continue
            usage = usage + TokenUsage.from_mapping(record.get("usage"))
            duration = _float(record.get("duration_seconds"))
            if duration is not None:
                seconds = (seconds or 0.0) + duration
    return usage, seconds


def warm_prompt_fired(results_dir: Path, responder_session_id: str | None) -> bool | None:
    """Whether a responder rollout's user prompt carried the warm-path instructions.

    Returns ``None`` when no Codex rollout was exported.
    """

    rollouts = sorted(results_dir.rglob("sdo_runtime/codex/sessions/**/rollout-*.jsonl"))
    if responder_session_id:
        own = [path for path in rollouts if responder_session_id in path.name]
        rollouts = own or rollouts
    if not rollouts:
        return None
    for path in rollouts:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if WARM_PROMPT_MARKER not in line:
                continue
            record = json.loads(line)
            payload = record.get("payload")
            if isinstance(payload, dict) and payload.get("type") == "message" and payload.get("role") == "user":
                return True
    return False


def memory_size(workspace: Path) -> MemorySize | None:
    """Count registered detectors and playbooks in a workspace's ``.sdo/``."""

    sdo = workspace / ".sdo"
    if not sdo.is_dir():
        return None
    detectors: list[dict[str, Any]] = []
    manifest = sdo / "diagnostics" / "manifest.yaml"
    if manifest.is_file():
        document = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
        detectors = [item for item in document.get("detectors") or [] if isinstance(item, dict)]
    playbooks = list((sdo / "playbooks").glob("*/README.md"))
    return MemorySize(
        detectors=len(detectors),
        incident_detectors=sum(1 for item in detectors if item.get("class") == "incident"),
        playbooks=len(playbooks),
    )


def pipeline_stage_dirs(pipeline_dir: Path) -> list[tuple[int, str, Path]]:
    """Ordered ``(index, name, dir)`` of a pipeline's current stages, skipping renamed aborted attempts."""

    state_path = pipeline_dir / "pipeline_state.json"
    stages: list[tuple[int, str, Path]] = []
    if state_path.is_file():
        state = json.loads(state_path.read_text(encoding="utf-8"))
        for stage in state.get("stages", []):
            recorded = Path(str(stage.get("experiment_dir", "")))
            local = pipeline_dir / recorded.name
            directory = local if local.is_dir() else recorded
            if directory.is_dir():
                stages.append((int(stage["index"]), str(stage["name"]), directory))
    else:
        for directory in pipeline_dir.iterdir():
            match = _STAGE_DIR.match(directory.name)
            if match and directory.is_dir() and not _RENAMED_STAGE.search(directory.name):
                stages.append((int(match.group(1)), match.group(2), directory))
    if not stages:
        raise IncidentCostError(f"{pipeline_dir} has no pipeline stages")
    return sorted(stages)


def load_sdo_pipeline(pipeline_dir: Path) -> list[SdoStage]:
    stages: list[SdoStage] = []
    for index, name, stage_dir in pipeline_stage_dirs(pipeline_dir):
        memory = memory_size(stage_dir / "application_workspace")
        for problem_id, results in _problem_results_dirs(stage_dir):
            receipts = sorted(results.rglob(RECEIPT_NAME))
            receipt: dict[str, Any] = json.loads(receipts[-1].read_text(encoding="utf-8")) if receipts else {}
            reuse = receipt.get("memory_reuse") if isinstance(receipt.get("memory_reuse"), dict) else {}
            phases = receipt.get("phase_timings_seconds") or {}
            incident_id = receipt.get("incident_id")
            _, reflection_seconds = _sum_usage_jsonl(
                sorted(results.rglob("sdo_runtime/usage/controller-turns.jsonl")),
                incident_id=incident_id if isinstance(incident_id, str) and incident_id else None,
            )
            lifecycle, _ = _sum_usage_jsonl(sorted(results.rglob(LIFECYCLE_USAGE_NAME)))
            session = receipt.get("responder_session_id")
            responder_rollouts = [
                path
                for path in sorted(results.rglob("sdo_runtime/codex/sessions/**/rollout-*.jsonl"))
                if isinstance(session, str) and session and session in path.name
            ]
            verdict = _with_mitigation_applied(
                read_verdict(results) or Verdict(None, None, None), results, responder_rollouts
            )
            stages.append(
                SdoStage(
                    index=index,
                    name=name,
                    problem_id=problem_id,
                    verdict=verdict,
                    incident_resolution_seconds=_float(receipt.get("incident_resolution_seconds")),
                    responder=TokenUsage.from_mapping(receipt.get("usage")),
                    reflection=TokenUsage.from_mapping(receipt.get("reflection_usage")),
                    reflection_attempts=receipt.get("reflection_attempts"),
                    reflection_skipped_reason=receipt.get("reflection_skipped_reason"),
                    learning_seconds=_float(phases.get("post_recovery_learning_and_receipt")),
                    reflection_turn_seconds=reflection_seconds,
                    warm_path=reuse.get("warm_path") if reuse else None,
                    match_reasons=tuple(reuse.get("match_reasons") or ()) if reuse else (),
                    warm_prompt=warm_prompt_fired(results, session if isinstance(session, str) else None),
                    memory=memory,
                    lifecycle=lifecycle,
                )
            )
    return stages


def _codex_tokens(results_dir: Path) -> TokenUsage:
    for path in sorted(results_dir.rglob("codex_results_*.json")):
        document = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(document.get("usage_metrics"), dict):
            return TokenUsage.from_mapping(document["usage_metrics"])
    for path in sorted(results_dir.rglob("trajectory.json")):
        metrics = json.loads(path.read_text(encoding="utf-8")).get("final_metrics") or {}
        return TokenUsage(
            input_tokens=int(metrics.get("total_prompt_tokens") or 0),
            cached_input_tokens=int(metrics.get("total_cached_tokens") or 0),
            output_tokens=int(metrics.get("total_completion_tokens") or 0),
        )
    return TokenUsage()


def load_codex_runs(directory: Path) -> list[CodexRun]:
    """Load every Codex problem run below a plain experiment or pipeline directory."""

    if (directory / "pipeline_state.json").is_file() or not (directory / "runs").is_dir():
        experiment_dirs = [path for _, _, path in pipeline_stage_dirs(directory)]
    else:
        experiment_dirs = [directory]
    runs: list[CodexRun] = []
    for experiment_dir in experiment_dirs:
        for problem_id, results in _problem_results_dirs(experiment_dir):
            verdict = read_verdict(results)
            if verdict is None:
                continue
            rollouts = sorted(results.rglob("sessions/**/rollout-*.jsonl"))
            verdict = _with_mitigation_applied(verdict, results, rollouts)
            runs.append(CodexRun(problem_id, verdict, _codex_tokens(results), str(results)))
    return runs


# --------------------------------------------------------------------------- analysis


def _mean(values: list[float | None]) -> float | None:
    known = [value for value in values if value is not None]
    return sum(known) / len(known) if known else None


def codex_costs(runs: list[CodexRun], *, uncached: bool) -> dict[str, CodexProblemCost]:
    by_problem: dict[str, list[CodexRun]] = {}
    for run in runs:
        by_problem.setdefault(run.problem_id, []).append(run)
    costs: dict[str, CodexProblemCost] = {}
    for problem_id, group in by_problem.items():
        costs[problem_id] = CodexProblemCost(
            problem_id=problem_id,
            runs=len(group),
            passed=sum(run.verdict.passed for run in group),
            mean_tokens=sum(run.tokens.total(uncached=uncached) for run in group) / len(group),
            mean_raw_incl_judge_seconds=_mean([run.verdict.raw_incl_judge_seconds for run in group]),
            mean_judge_excluded_seconds=_mean([run.verdict.judge_excluded_seconds for run in group]),
            mean_mitigation_applied_seconds=_mean([run.verdict.mitigation_applied_seconds for run in group]),
            mean_ttd_seconds=_mean([run.verdict.diagnosis_seconds for run in group]),
            mean_ttm_seconds=_mean([run.verdict.ttm_seconds for run in group]),
            mean_last_mitigation_seconds=_mean([run.verdict.last_mitigation_seconds for run in group]),
        )
    return costs


def _break_even(
    measure: str,
    sdo: list[int],
    codex: list[float | None],
    offset: int,
    stages: list[SdoStage],
) -> BreakEven:
    """First stage whose cumulative SDO tokens (plus ``offset``) are at most Codex's."""

    crossing: int | None = None
    gap = 0.0
    for position, (sdo_total, codex_total) in enumerate(zip(sdo, codex, strict=True)):
        if codex_total is None:
            break
        gap = sdo_total + offset - codex_total
        if crossing is None and gap <= 0:
            crossing = stages[position].index
    projected: int | None = None
    if crossing is None and gap > 0 and codex and codex[-1] is not None:
        # Project with the mean saving of stages whose problem was already seen earlier.
        seen: set[str] = set()
        savings: list[float] = []
        previous_sdo, previous_codex = 0, 0.0
        for stage, sdo_total, codex_total in zip(stages, sdo, codex, strict=True):
            if codex_total is None:
                break
            if stage.problem_id in seen:
                savings.append((codex_total - previous_codex) - (sdo_total - previous_sdo))
            seen.add(stage.problem_id)
            previous_sdo, previous_codex = sdo_total, codex_total
        mean_saving = sum(savings) / len(savings) if savings else 0.0
        if mean_saving > 0:
            projected = math.ceil(gap / mean_saving)
    return BreakEven(measure, offset > 0, crossing, gap, projected)


def build_report(
    stages: list[SdoStage],
    codex_runs: list[CodexRun],
    *,
    lifecycle_override: TokenUsage | None = None,
    uncached: bool = False,
) -> Report:
    costs = codex_costs(codex_runs, uncached=uncached)
    lifecycle = lifecycle_override or sum((stage.lifecycle for stage in stages), TokenUsage())
    lifecycle_tokens = lifecycle.total(uncached=uncached)

    cumulative: list[CumulativeRow] = []
    incident = learning = 0
    codex_total: float | None = 0.0
    sdo_time: float | None = 0.0
    codex_time: float | None = 0.0
    for stage in stages:
        incident += stage.responder.total(uncached=uncached)
        learning += stage.responder.total(uncached=uncached) + stage.reflection.total(uncached=uncached)
        cost = costs.get(stage.problem_id)
        codex_total = codex_total + cost.mean_tokens if codex_total is not None and cost else None
        sdo_time = (
            sdo_time + stage.verdict.ttm_seconds
            if sdo_time is not None and stage.verdict.ttm_seconds is not None
            else None
        )
        codex_time = (
            codex_time + cost.mean_ttm_seconds
            if codex_time is not None and cost and cost.mean_ttm_seconds is not None
            else None
        )
        cumulative.append(CumulativeRow(stage.index, incident, learning, codex_total, sdo_time, codex_time))

    codex_series = [row.codex_tokens for row in cumulative]
    incident_series = [row.sdo_incident_tokens for row in cumulative]
    learning_series = [row.sdo_tokens_with_learning for row in cumulative]
    break_even = [
        _break_even("incident tokens (responder)", incident_series, codex_series, 0, stages),
        _break_even("total tokens incl. learning", learning_series, codex_series, 0, stages),
    ]
    if lifecycle_tokens:
        break_even += [
            _break_even("incident tokens (responder)", incident_series, codex_series, lifecycle_tokens, stages),
            _break_even("total tokens incl. learning", learning_series, codex_series, lifecycle_tokens, stages),
        ]
    return Report(stages, costs, cumulative, lifecycle_tokens, break_even, uncached)


# --------------------------------------------------------------------------- rendering


def _fmt_num(value: float | None, digits: int = 0) -> str:
    if value is None:
        return "-"
    return f"{value:,.{digits}f}"


def _fmt_bool(value: bool | None) -> str:
    return "-" if value is None else ("yes" if value else "no")


def _fmt_verdict(verdict: Verdict) -> str:
    if verdict.diagnosis is None and verdict.mitigation is None:
        return "-"
    letters = "".join(
        f"{label}{'+' if value else '-' if value is False else '?'}"
        for label, value in (("D", verdict.diagnosis), ("M", verdict.mitigation))
    )
    return f"{'PASS' if verdict.passed else 'FAIL'} {letters}"


def _table(headers: list[str], rows: list[list[str]]) -> str:
    widths = [
        max(len(header), *(len(row[i]) for row in rows)) if rows else len(header) for i, header in enumerate(headers)
    ]
    lines = ["  ".join(header.ljust(width) for header, width in zip(headers, widths, strict=True))]
    lines.append("  ".join("-" * width for width in widths))
    lines += ["  ".join(cell.ljust(width) for cell, width in zip(row, widths, strict=True)) for row in rows]
    return "\n".join(lines)


def render(report: Report) -> str:
    uncached = report.uncached
    unit = "uncached tokens" if uncached else "tokens"
    stage_rows = []
    for stage in report.stages:
        memory = stage.memory
        stage_rows.append(
            [
                str(stage.index),
                stage.name,
                stage.problem_id,
                _fmt_verdict(stage.verdict),
                _fmt_num(stage.verdict.diagnosis_seconds, 1),
                _fmt_num(stage.verdict.ttm_seconds, 1),
                _fmt_num(stage.verdict.raw_incl_judge_seconds, 1),
                _fmt_num(stage.verdict.judge_excluded_seconds, 1),
                _fmt_num(stage.verdict.mitigation_applied_seconds, 1),
                _fmt_num(stage.verdict.last_mitigation_seconds, 1),
                _fmt_num(stage.incident_resolution_seconds, 1),
                _fmt_num(stage.responder.total(uncached=uncached)),
                _fmt_num(stage.reflection.total(uncached=uncached)),
                "skipped" if stage.reflection_skipped_reason else _fmt_num(stage.reflection_attempts),
                _fmt_num(stage.learning_seconds, 1),
                _fmt_num(stage.reflection_turn_seconds, 1),
                _fmt_bool(stage.warm_path),
                _fmt_bool(stage.warm_prompt),
                f"{memory.detectors}({memory.incident_detectors})/{memory.playbooks}" if memory else "-",
            ]
        )
    sections = [
        f"SDO stages ({unit}; ttm_s is the headline and excludes judge time; no time includes reflection)",
        _table(
            [
                "#",
                "stage",
                "problem",
                "oracles",
                "ttd_s",
                "ttm_s",
                "raw_incl_judge_s",
                "no_judge_s",
                "first_mut_s",
                "last_mut_s",
                "resolution_s",
                "responder_tok",
                "reflection_tok",
                "refl",
                "learning_s",
                "refl_turn_s",
                "warm_path",
                "warm_prompt",
                "det(inc)/pb",
            ],
            stage_rows,
        ),
        "",
        "Codex baseline (mean per problem)",
        _table(
            [
                "problem",
                "runs",
                "passed",
                "mean_tok",
                "mean_ttd_s",
                "mean_ttm_s",
                "mean_raw_incl_judge_s",
                "mean_no_judge_s",
                "mean_first_mut_s",
                "mean_last_mut_s",
            ],
            [
                [
                    cost.problem_id,
                    str(cost.runs),
                    str(cost.passed),
                    _fmt_num(cost.mean_tokens),
                    _fmt_num(cost.mean_ttd_seconds, 1),
                    _fmt_num(cost.mean_ttm_seconds, 1),
                    _fmt_num(cost.mean_raw_incl_judge_seconds, 1),
                    _fmt_num(cost.mean_judge_excluded_seconds, 1),
                    _fmt_num(cost.mean_mitigation_applied_seconds, 1),
                    _fmt_num(cost.mean_last_mitigation_seconds, 1),
                ]
                for cost in sorted(report.codex.values(), key=lambda item: item.problem_id)
            ],
        ),
        "",
        "Cumulative",
        _table(
            ["#", "sdo_incident_tok", "sdo_total_tok_incl_learning", "codex_tok", "sdo_ttm_s", "codex_ttm_s"],
            [
                [
                    str(row.index),
                    _fmt_num(row.sdo_incident_tokens),
                    _fmt_num(row.sdo_tokens_with_learning),
                    _fmt_num(row.codex_tokens),
                    _fmt_num(row.sdo_ttm_seconds, 1),
                    _fmt_num(row.codex_ttm_seconds, 1),
                ]
                for row in report.cumulative
            ],
        ),
        f"One-time SDO lifecycle: {_fmt_num(report.lifecycle_tokens)} {unit}"
        + ("" if report.lifecycle_tokens else " (no sdo_turn_usage.jsonl found; pass --lifecycle-usage)"),
        "",
        "Break-even against Codex (first stage where cumulative SDO <= cumulative Codex)",
    ]
    for item in report.break_even:
        scope = "with lifecycle" if item.includes_lifecycle else "without lifecycle"
        if item.stage is not None:
            outcome = f"stage {item.stage}"
        else:
            outcome = f"not reached (gap {_fmt_num(item.final_gap)})"
            if item.projected_extra_repeats is not None:
                outcome += f"; ~{item.projected_extra_repeats} more repeat incidents at the observed repeat saving"
        sections.append(f"  {item.measure}, {scope}: {outcome}")
    return "\n".join(sections)


def _report_json(report: Report) -> dict[str, Any]:
    document = asdict(report)
    for stage, row in zip(report.stages, document["stages"], strict=True):
        row["verdict"]["judge_excluded_seconds"] = stage.verdict.judge_excluded_seconds
        row["verdict"]["ttm_seconds"] = stage.verdict.ttm_seconds
    return document


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("sdo_pipeline", type=Path, help="SDO pipeline log directory")
    parser.add_argument("--codex", type=Path, nargs="*", default=[], help="Codex run directories")
    parser.add_argument(
        "--lifecycle-usage",
        type=Path,
        nargs="*",
        default=None,
        help="sdo_turn_usage.jsonl files for the one-time lifecycle (overrides files found in the pipeline)",
    )
    parser.add_argument("--uncached", action="store_true", help="exclude cached input tokens")
    parser.add_argument("--json", type=Path, help="also write the report as JSON")
    args = parser.parse_args(argv)

    try:
        stages = load_sdo_pipeline(args.sdo_pipeline)
        codex_runs = [run for directory in args.codex for run in load_codex_runs(directory)]
    except IncidentCostError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    lifecycle = _sum_usage_jsonl(args.lifecycle_usage)[0] if args.lifecycle_usage else None
    report = build_report(stages, codex_runs, lifecycle_override=lifecycle, uncached=args.uncached)
    print(render(report))
    if args.json:
        args.json.write_text(json.dumps(_report_json(report), indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
