"""Minimal SRE Gym agent that wraps a ``agentshim`` CLI agent.

Per stage, instantiates one provider-routed ``CodingAgent`` from `agentshim`
with the sregym ``/submit`` MCP server wired in. The wrapped CLI itself
calls the ``submit`` tool when it has reached a conclusion — this driver
just builds the prompt, launches the CLI, and after it returns checks
whether the conductor advanced past the current stage.

No judge, no knowledge base, no deferred cleanup — this agent is a
baseline / smoke test. Contrast with ``sregym_agents.crucible.driver``.

Provider support: submission is done by the wrapped CLI through the MCP
server, so only providers whose ``agentshim`` class accepts
``mcp_servers`` are usable — currently ``claude`` and ``codex``. Gemini
and Opencode raise ``ValueError`` inside their constructor when
``mcp_servers`` is non-empty (see
``agentshim/gemini/agent.py`` and ``agentshim/opencode/agent.py``).
"""

from __future__ import annotations

import argparse
import dataclasses
import functools
import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from libs.sregym_lib.conductor import (
    get_api_base,
    get_app_info,
    get_current_stage_sync,
    get_planned_stages,
    get_problem_id,
    poll_stage_sync,
    wait_for_stages_or_last_seen_sync,
)
from libs.sregym_lib.schema import READY_STAGES, TERMINAL_STAGES
from sregym_agents.cli_agent.memory.store import (
    CONFIRMED_DIAGNOSIS_ONLY,
    CONFIRMED_SELF,
    CONFIRMED_VERDICT,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from agentshim import BaseCodingAgent

logger = logging.getLogger(__name__)

# How long to wait after each stage for the conductor to finish grading
# and advance. Overridable via monkeypatch in tests so the
# "never-submitted" path doesn't block a unit test for 5 minutes.
_POST_STAGE_TIMEOUT_S = 300

_SUBMIT_MCP_SERVER_NAME = "sregym"
_AUTONOMOUS_PROMPT_PROFILES = frozenset({"sds", "direct"})
_DEFAULT_AUTONOMOUS_PROMPT_PROFILE = "sds"
_APPLICATION_WORKSPACE_MODES = frozenset({"none", "persistent", "ephemeral"})
_DEFAULT_APPLICATION_WORKSPACE_MODE = "none"
_OBSERVER_CHECK_TIMEOUT_S = 300
_OBSERVER_REPAIR_ATTEMPTS = 1

# The observer preflight samples the cluster over a short window instead of a
# single snapshot. Fault symptoms such as CrashLoopBackOff, OOMKilled, and
# FailedScheduling only mature seconds-to-minutes after injection, so a single
# early snapshot misses the sharp cause-specific detectors and leaves only the
# broad symptom detectors (e.g. zero-endpoints) firing — which mislead the
# agent. Overridable via env for tuning without code changes.
_OBSERVER_WINDOW_ITERATIONS = max(1, int(os.getenv("SDS_OBSERVER_WINDOW_ITERATIONS", "5") or "5"))
_OBSERVER_WINDOW_INTERVAL_S = max(0, int(os.getenv("SDS_OBSERVER_WINDOW_INTERVAL_S", "30") or "30"))


@dataclasses.dataclass(frozen=True)
class _ObserverCheckResult:
    command: list[str]
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


@dataclasses.dataclass(frozen=True)
class _ObserverFinding:
    detector_id: str
    rule_id: str
    status: str
    severity: str
    summary: str
    evidence: str
    primary_resource: str
    playbooks: tuple[str, ...]

    @property
    def key(self) -> tuple[str, str, str]:
        """Identity used to track the same finding across window samples."""
        return (self.detector_id or self.rule_id, self.rule_id, self.primary_resource)


@dataclasses.dataclass(frozen=True)
class _ObserverIterationSample:
    index: int
    findings: tuple[_ObserverFinding, ...]


# --- Pure helpers ----------------------------------------------------------


@functools.lru_cache(maxsize=1)
def _jinja_env() -> Environment:
    """Lazily build the Jinja2 environment for prompt templates.

    Cached as a module-level singleton via ``lru_cache(maxsize=1)`` so the
    ``FileSystemLoader`` and template parsing only happen on first use,
    not on every prompt build.
    """
    return Environment(
        loader=FileSystemLoader(str(Path(__file__).parent / "prompts")),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
    )


def _build_prompt(
    planned_stages: list[str],
    app_info: dict[str, Any],
    *,
    autonomous: bool = False,
    autonomous_prompt_profile: str = _DEFAULT_AUTONOMOUS_PROMPT_PROFILE,
    submit_done_returns_feedback: bool = False,
    memory_enabled: bool = False,
    observer_detectors_enabled: bool = False,
    observer_preflight_report: str = "",
) -> str:
    """Render the single-session prompt handed to the wrapped CLI agent.

    One CLI session handles every planned stage. In the default mode, the
    agent calls the single ``submit`` tool once per stage and the conductor
    routes each call to whichever stage is currently active — the tool
    response carries the grading verdict. In autonomous mode (``autonomous=True``),
    the agent instead calls per-stage ``submit_diagnosis`` / ``submit_mitigation``
    tools that return only a neutral acknowledgement, so the agent must
    self-verify by inspecting the cluster before submitting. Autonomous
    prompt instructions are further selected by ``autonomous_prompt_profile``.

    The prompt deliberately omits the benchmark ``problem_id``: SREGym
    problem IDs are descriptive (``incorrect_image``,
    ``missing_env_variable_astronomy_shop``, ``liveness_probe_misconfiguration_*``)
    and including them would leak the answer to the agent. App name and
    namespace are kept because they're observable from the cluster anyway.
    """
    template_name = "session.j2"
    if autonomous:
        profile = _normalize_autonomous_prompt_profile(autonomous_prompt_profile)
        template_name = {
            "sds": "session_autonomous.j2",
            "direct": "session_autonomous_direct.j2",
        }[profile]
    return (
        _jinja_env()
        .get_template(template_name)
        .render(
            planned_stages=list(planned_stages),
            app_name=app_info.get("app_name", "<unknown>"),
            namespace=app_info.get("namespace", "<unknown>"),
            submit_mcp_server_name=_SUBMIT_MCP_SERVER_NAME,
            submit_done_returns_feedback=submit_done_returns_feedback,
            memory_enabled=memory_enabled,
            observer_detectors_enabled=observer_detectors_enabled,
            observer_preflight_report=observer_preflight_report,
        )
    )


def _normalize_autonomous_prompt_profile(profile: Any) -> str:
    """Normalize and validate the autonomous prompt-profile selector."""
    if profile is None:
        return _DEFAULT_AUTONOMOUS_PROMPT_PROFILE
    normalized = str(profile).strip().lower()
    if not normalized:
        return _DEFAULT_AUTONOMOUS_PROMPT_PROFILE
    if normalized not in _AUTONOMOUS_PROMPT_PROFILES:
        allowed = ", ".join(sorted(_AUTONOMOUS_PROMPT_PROFILES))
        raise ValueError(f"Unknown autonomous prompt profile {profile!r}. Expected one of: {allowed}")
    return normalized


def _normalize_application_workspace_mode(mode: Any) -> str:
    """Normalize and validate the application-workspace mode forwarded by the runner."""
    if mode is None:
        return _DEFAULT_APPLICATION_WORKSPACE_MODE
    if isinstance(mode, bool):
        return "persistent" if mode else "none"
    normalized = str(mode).strip().lower()
    if not normalized:
        return _DEFAULT_APPLICATION_WORKSPACE_MODE
    if normalized == "true":
        return "persistent"
    if normalized == "false":
        return "none"
    if normalized not in _APPLICATION_WORKSPACE_MODES:
        allowed = ", ".join(sorted(_APPLICATION_WORKSPACE_MODES))
        raise ValueError(f"Unknown application workspace mode {mode!r}. Expected one of: {allowed}")
    return normalized


def _observer_detectors_enabled(
    *,
    autonomous: bool,
    autonomous_prompt_profile: str,
    application_workspace_mode: str,
) -> bool:
    """Enable observer-detector authoring only for autonomous persistent `.sds` runs."""
    return (
        autonomous
        and _normalize_autonomous_prompt_profile(autonomous_prompt_profile) == "sds"
        and _normalize_application_workspace_mode(application_workspace_mode) == "persistent"
    )


# --- Memory helpers --------------------------------------------------------


def _default_memory_dir() -> Path:
    """Persistent default store root, outside any ephemeral experiment workdir."""
    return Path.home() / ".sds" / "cli_agent_memory"


def _resolve_memory_dir(args: argparse.Namespace, *, base_cwd: str) -> Path:
    """Resolve the lesson-store dir to an absolute path.

    A relative ``--memory-dir`` is anchored to ``base_cwd`` (the cwd *before*
    the driver chdir's into ``SREGYM_EXP_ENV``) so the store never lands inside
    the ephemeral workdir.
    """
    raw = getattr(args, "memory_dir", None)
    if not raw:
        return _default_memory_dir()
    path = Path(raw).expanduser()
    return path if path.is_absolute() else (Path(base_cwd) / path).resolve()


def _classify_confirmation(
    *,
    autonomous: bool,
    completed: bool,
    final_stage: str | None,
    planned_stages: list[str],
) -> str | None:
    """Decide whether (and how) this run's outcome was confirmed — see §3, §8.

    Returns the ``confirmed_by`` value to store, or ``None`` to write nothing.

    - Autonomous mode has no per-stage grader, so only a fully completed run
      counts (``self_verified``); no partial writes.
    - Default mode reads the conductor stage: a terminal stage means the grader
      accepted everything (``verdict``); reaching ``mitigation`` without
      terminating means diagnosis was accepted but the fix was not
      (``diagnosis_only`` — partial, fix recorded-but-unverified).
    """
    if autonomous:
        return CONFIRMED_SELF if completed else None
    if completed:
        return CONFIRMED_VERDICT
    if "mitigation" in planned_stages and final_stage == "mitigation":
        return CONFIRMED_DIAGNOSIS_ONLY
    return None


def _maybe_write_lesson(
    *,
    session: Any,
    store: Any,
    app: str,
    autonomous: bool,
    completed: bool,
    final_stage: str | None,
    planned_stages: list[str],
    timeout: int,
) -> None:
    """Extract and upsert a lesson if the outcome was confirmed (else no-op).

    Resumes the agent session for one extraction turn, parses the reply, and
    upserts into the per-app store. Best-effort: any failure is logged and
    swallowed — a memory write must never break the benchmark run.
    """
    from sregym_agents.cli_agent.memory import extract as memory_extract

    confirmed_by = _classify_confirmation(
        autonomous=autonomous,
        completed=completed,
        final_stage=final_stage,
        planned_stages=planned_stages,
    )
    if confirmed_by is None:
        logger.info("Memory: outcome not confirmed (final_stage=%r); no lesson written", final_stage)
        return
    try:
        existing = store.load(app)
        prompt = memory_extract.build_extraction_prompt(existing, confirmed_by)
        reply = session.generate(prompt, timeout=timeout)
        result = memory_extract.parse_upsert(reply, existing)
        if result is None:
            logger.warning("Memory: extraction produced no usable lesson; nothing written")
            return
        lesson = memory_extract.apply_upsert(store, app, result, confirmed_by)
        logger.info(
            "Memory: %s lesson id=%d for app=%s (confirmed_by=%s, seen=%d)",
            "merged" if result.decision == "merge" else "stored new",
            lesson.id,
            app,
            confirmed_by,
            lesson.seen_count,
        )
    except Exception:
        logger.exception("Memory: lesson extraction/write failed; continuing")


# --- Observer diagnostics helpers -----------------------------------------


def _observer_manifest_path(app_root: Path) -> Path:
    return app_root / ".sds" / "diagnostics" / "manifest.yaml"


def _exec_observer_commands(
    commands: list[list[str]],
    *,
    cwd: Path,
    timeout: int,
) -> list[_ObserverCheckResult]:
    results: list[_ObserverCheckResult] = []
    for command in commands:
        try:
            completed = subprocess.run(
                command,
                cwd=cwd,
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            result = _ObserverCheckResult(
                command=command,
                returncode=completed.returncode,
                stdout=completed.stdout,
                stderr=completed.stderr,
            )
        except subprocess.TimeoutExpired as exc:
            result = _ObserverCheckResult(
                command=command,
                returncode=124,
                stdout=exc.stdout or "",
                stderr=(exc.stderr or "") + f"\nobserver check timed out after {timeout}s",
            )
        results.append(result)
        if not result.ok:
            break
    return results


def _run_observer_check_commands(
    *,
    app_root: Path,
    namespace: str,
    timeout: int = _OBSERVER_CHECK_TIMEOUT_S,
) -> list[_ObserverCheckResult]:
    commands = [
        [sys.executable, "-m", "observer.updater.check_cli", "test", "--app", str(app_root)],
        [
            sys.executable,
            "-m",
            "observer.updater.check_cli",
            "run-once",
            "--app",
            str(app_root),
            "--namespace",
            namespace,
        ],
    ]
    return _exec_observer_commands(commands, cwd=app_root, timeout=timeout)


def _observer_window_timeout(*, iterations: int, interval_s: int) -> int:
    """Budget for the windowed preflight: build cost plus the full sampling span."""
    return _OBSERVER_CHECK_TIMEOUT_S + iterations * (interval_s + 60)


def _run_observer_window_commands(
    *,
    app_root: Path,
    namespace: str,
    iterations: int = _OBSERVER_WINDOW_ITERATIONS,
    interval_s: int = _OBSERVER_WINDOW_INTERVAL_S,
    timeout: int | None = None,
) -> list[_ObserverCheckResult]:
    if timeout is None:
        timeout = _observer_window_timeout(iterations=iterations, interval_s=interval_s)
    commands = [
        [sys.executable, "-m", "observer.updater.check_cli", "test", "--app", str(app_root)],
        [
            sys.executable,
            "-m",
            "observer.updater.check_cli",
            "watch",
            "--app",
            str(app_root),
            "--namespace",
            namespace,
            "--iterations",
            str(iterations),
            "--interval-s",
            str(interval_s),
        ],
    ]
    return _exec_observer_commands(commands, cwd=app_root, timeout=timeout)


def _format_observer_check_results(results: list[_ObserverCheckResult], *, max_chars: int = 12000) -> str:
    sections: list[str] = []
    for result in results:
        status = "passed" if result.ok else f"failed with exit {result.returncode}"
        section = (
            f"$ {' '.join(result.command)}\n"
            f"status: {status}\n"
            f"stdout:\n{result.stdout.strip() or '<empty>'}\n"
            f"stderr:\n{result.stderr.strip() or '<empty>'}"
        )
        sections.append(section)
    text = "\n\n".join(sections)
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 200] + "\n\n...[observer check output truncated]..."


def _format_observer_resource(resource: Any) -> str:
    if not isinstance(resource, dict):
        return ""
    kind = str(resource.get("kind") or "").strip()
    namespace = str(resource.get("namespace") or "").strip()
    name = str(resource.get("name") or "").strip()
    if not kind and not name:
        return ""
    qualified = f"{kind}/{name}" if kind and name else kind or name
    if namespace:
        return f"{qualified} namespace={namespace}"
    return qualified


def _finding_from_payload(payload: dict[str, Any]) -> _ObserverFinding:
    playbooks_raw = payload.get("playbooks")
    playbooks = tuple(str(item) for item in playbooks_raw if item) if isinstance(playbooks_raw, list) else ()
    return _ObserverFinding(
        detector_id=str(payload.get("detector_id") or payload.get("detector") or ""),
        rule_id=str(payload.get("rule_id") or ""),
        status=str(payload.get("status") or ""),
        severity=str(payload.get("severity") or ""),
        summary=str(payload.get("summary") or ""),
        evidence=str(payload.get("evidence") or ""),
        primary_resource=_format_observer_resource(payload.get("primary_resource")),
        playbooks=playbooks,
    )


def _parse_observer_findings(stdout: str) -> list[_ObserverFinding]:
    findings: list[_ObserverFinding] = []
    for raw_line in stdout.splitlines():
        line = raw_line.strip()
        if not line.startswith("{"):
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict) or "rule_id" not in payload:
            continue
        findings.append(_finding_from_payload(payload))
    return findings


def _parse_observer_iterations(stdout: str) -> list[_ObserverIterationSample]:
    """Parse the per-iteration time series emitted by ``check_cli watch``.

    Each iteration is a single JSON line ``{"observer_iteration": N,
    "findings": [...]}``. Lines that are not iteration records (build logs,
    legacy single-shot finding lines) are ignored.
    """
    samples: list[_ObserverIterationSample] = []
    for raw_line in stdout.splitlines():
        line = raw_line.strip()
        if not line.startswith("{") or "observer_iteration" not in line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict) or "observer_iteration" not in payload:
            continue
        raw_findings = payload.get("findings")
        findings = (
            tuple(
                _finding_from_payload(item) for item in raw_findings if isinstance(item, dict) and item.get("rule_id")
            )
            if isinstance(raw_findings, list)
            else ()
        )
        samples.append(_ObserverIterationSample(index=int(payload["observer_iteration"]), findings=findings))
    samples.sort(key=lambda sample: sample.index)
    return samples


_OBSERVER_NO_MATCH_MESSAGE = (
    "no existing observer detector matched this incident — "
    "there is no detector→playbook mapping for the fault present here. "
    "This does NOT mean the cluster is healthy: a fault is present and the cluster is in an "
    "unhealthy state. Diagnose and mitigate it directly from the live cluster and source, "
    "then after submission produce a detector that would have caught this incident and map it "
    "to a playbook — an existing playbook if one fits, or a new one you author."
)


def _format_observer_finding_block(finding: _ObserverFinding, *, index: int, annotation: str = "") -> list[str]:
    heading_bits = [bit for bit in [finding.severity, finding.status, finding.rule_id] if bit]
    heading = " ".join(heading_bits) or "finding"
    lines = [f"{index}. [{heading}] {finding.summary or '<no summary>'}"]
    if annotation:
        lines.append(f"   {annotation}")
    detector_id = finding.detector_id or finding.rule_id
    if detector_id:
        lines.append(f"   detector: {detector_id}")
    if finding.primary_resource:
        lines.append(f"   resource: {finding.primary_resource}")
    if finding.evidence:
        lines.append(f"   evidence: {finding.evidence}")
    if finding.playbooks:
        lines.append("   recommended playbooks:")
        lines.extend(f"   - {playbook}" for playbook in finding.playbooks)
    else:
        lines.append("   recommended playbooks: <none>")
    return lines


def _persistence_annotation(*, fired_iters: list[int], total: int) -> str:
    count = len(fired_iters)
    iters_str = ",".join(str(i) for i in fired_iters)
    if count >= total:
        kind = "persistent"
    elif fired_iters[0] > 0 and fired_iters[-1] == total - 1:
        kind = "late-appearing — a fault state that matured during the window; likely the true root cause"
    elif fired_iters[0] == 0 and fired_iters[-1] < total - 1:
        kind = "fading — an early/transient symptom that cleared; may be downstream, not the root cause"
    elif count == 1:
        kind = "one-off — possibly transient; verify before trusting"
    else:
        kind = "intermittent"
    return f"persistence: fired {count}/{total} samples (iters {iters_str}) — {kind}"


def _format_observer_window_report(samples: list[_ObserverIterationSample], *, max_chars: int) -> str:
    total = len(samples)
    # Aggregate each distinct finding across the window, keeping the most
    # recent observation for its summary/evidence/playbooks.
    fired_iters: dict[tuple[str, str, str], list[int]] = {}
    latest: dict[tuple[str, str, str], _ObserverFinding] = {}
    for sample in samples:
        for finding in sample.findings:
            fired_iters.setdefault(finding.key, []).append(sample.index)
            latest[finding.key] = finding

    header = (
        f"Observer preflight sampled the cluster over {total} iteration(s) "
        f"{_OBSERVER_WINDOW_INTERVAL_S}s apart, because fault symptoms "
        "(CrashLoopBackOff, OOMKilled, FailedScheduling) often take time to mature into a "
        "detectable state. Each finding below is annotated with how many samples it fired in. "
        "Treat persistent findings as the most reliable leads; a finding that only appears in "
        "later samples is likely the true root cause maturing, while one that fades early is "
        "more likely a downstream symptom. Verify every finding against the live cluster."
    )

    if not fired_iters:
        return f"Observer preflight completed successfully, but {_OBSERVER_NO_MATCH_MESSAGE}\n\n{header}"

    timeline = ["Per-iteration timeline (detector ids that fired):"]
    for sample in samples:
        ids = ", ".join(sorted((f.detector_id or f.rule_id) for f in sample.findings)) or "<none>"
        timeline.append(f"  iter{sample.index}: {ids}")

    # Most-persistent findings first, then by first-appearance so late-maturing
    # detectors still surface prominently.
    ordered_keys = sorted(
        fired_iters,
        key=lambda k: (-len(fired_iters[k]), fired_iters[k][0], k),
    )
    finding_lines = [f"{len(ordered_keys)} distinct observer finding(s):"]
    for index, key in enumerate(ordered_keys, start=1):
        finding_lines.extend(
            _format_observer_finding_block(
                latest[key],
                index=index,
                annotation=_persistence_annotation(fired_iters=sorted(fired_iters[key]), total=total),
            )
        )

    text = "\n".join([header, "", *timeline, "", *finding_lines])
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 200] + "\n\n...[observer preflight report truncated]..."


def _format_observer_preflight_report(results: list[_ObserverCheckResult], *, max_chars: int = 12000) -> str:
    if not results:
        return ""
    failed = next((result for result in results if not result.ok), None)
    if failed is not None:
        return (
            "Observer preflight failed before producing reliable findings. "
            "Do not let this block incident mitigation; after submission, repair `.sds/diagnostics/` if needed.\n\n"
            f"{_format_observer_check_results(results, max_chars=max_chars)}"
        )

    # Windowed preflight (check_cli watch) emits a per-iteration time series.
    samples: list[_ObserverIterationSample] = []
    for result in results:
        samples.extend(_parse_observer_iterations(result.stdout))
    if samples:
        return _format_observer_window_report(samples, max_chars=max_chars)

    # Fallback: legacy single-shot run-once output.
    findings: list[_ObserverFinding] = []
    for result in results:
        findings.extend(_parse_observer_findings(result.stdout))

    if not findings:
        return f"Observer preflight ran, but {_OBSERVER_NO_MATCH_MESSAGE}"

    lines = ["Observer preflight completed successfully.", f"{len(findings)} observer finding(s):"]
    for index, finding in enumerate(findings, start=1):
        lines.extend(_format_observer_finding_block(finding, index=index))

    text = "\n".join(lines)
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 200] + "\n\n...[observer preflight report truncated]..."


def _maybe_build_observer_preflight_report(
    *,
    app_root: Path,
    namespace: str,
    iterations: int = _OBSERVER_WINDOW_ITERATIONS,
    interval_s: int = _OBSERVER_WINDOW_INTERVAL_S,
) -> str:
    if not _observer_manifest_path(app_root).is_file():
        logger.info("Observer diagnostics: no manifest found; skipping preflight check")
        return ""
    if not namespace:
        logger.info("Observer diagnostics: namespace missing; skipping preflight check")
        return ""

    logger.info(
        "Observer diagnostics: running windowed preflight check (%d iterations, %ds apart)",
        iterations,
        interval_s,
    )
    results = _run_observer_window_commands(
        app_root=app_root,
        namespace=namespace,
        iterations=iterations,
        interval_s=interval_s,
    )
    if all(result.ok for result in results):
        logger.info("Observer diagnostics: preflight check passed")
    else:
        logger.warning("Observer diagnostics: preflight check failed; including failure in agent prompt")
    return _format_observer_preflight_report(results)


def _observer_repair_prompt(results: list[_ObserverCheckResult]) -> str:
    return (
        "The post-submission observer diagnostics check failed.\n\n"
        "Fix only the `.sds/diagnostics/` detector code, `.sds/playbooks/` files, "
        "or related `.sds/` routing needed to make these observer checks pass. "
        "Do not change application manifests or live cluster state unless the observer "
        "check failure proves those files are directly involved.\n\n"
        "Keep detector-to-playbook routing precise while repairing: target the directly "
        "observed Kubernetes symptom, avoid speculative subsystem-specific labels, and "
        "do not gate a generic symptom detector on unrelated application resources.\n\n"
        "Do not call `submit_diagnosis`, `submit_mitigation`, or `submit_done`; "
        "the benchmark submission is already complete.\n\n"
        "After editing, stop. The driver will rerun the observer checks.\n\n"
        "Observer check output:\n\n"
        "```text\n"
        f"{_format_observer_check_results(results)}\n"
        "```\n"
    )


def _maybe_repair_observer_diagnostics(
    *,
    session: Any,
    app_root: Path,
    namespace: str,
    repair_timeout: int,
    check_timeout: int = _OBSERVER_CHECK_TIMEOUT_S,
) -> list[_ObserverCheckResult]:
    if not _observer_manifest_path(app_root).is_file():
        logger.info("Observer diagnostics: no manifest found; skipping post-agent check")
        return []
    if not namespace:
        logger.info("Observer diagnostics: namespace missing; skipping post-agent check")
        return []

    logger.info("Observer diagnostics: running post-agent check")
    results = _run_observer_check_commands(app_root=app_root, namespace=namespace, timeout=check_timeout)
    if all(result.ok for result in results):
        logger.info("Observer diagnostics: post-agent check passed")
        return results

    for attempt in range(1, _OBSERVER_REPAIR_ATTEMPTS + 1):
        logger.warning(
            "Observer diagnostics: check failed; asking agent to repair (attempt %d/%d)",
            attempt,
            _OBSERVER_REPAIR_ATTEMPTS,
        )
        session.generate(_observer_repair_prompt(results), cwd=str(app_root), timeout=repair_timeout)
        results = _run_observer_check_commands(app_root=app_root, namespace=namespace, timeout=check_timeout)
        if all(result.ok for result in results):
            logger.info("Observer diagnostics: check passed after repair attempt %d", attempt)
            return results

    logger.warning("Observer diagnostics: still failing after repair attempt(s)")
    return results


# --- Conductor I/O ---------------------------------------------------------
# Conductor HTTP client lives in libs/sregym_lib/conductor.py — this driver
# only owns the cli_agent-specific orchestration below.


def _default_agent_factory(
    provider: str,
    model: str,
    submit_mcp_url: str,
    extra_mcp_servers: list[Any] | None = None,
) -> BaseCodingAgent:
    """Build a ``CodingAgent`` with the sregym submit MCP server wired in.

    Raises ``ValueError`` if the requested provider does not support MCP
    (e.g. ``gemini``/``opencode``) — the underlying class raises it from
    its ``__init__`` when ``mcp_servers`` is non-empty.
    """
    # Deferred imports keep `--help` fast and avoid triggering heavy
    # provider imports until the driver actually needs a backend.
    from agentshim.mcp_config import HttpMcpServer

    from agentshim import CodingAgent

    mcp_servers: list[Any] = [HttpMcpServer(name=_SUBMIT_MCP_SERVER_NAME, url=submit_mcp_url)]
    if extra_mcp_servers:
        mcp_servers.extend(extra_mcp_servers)
    try:
        return CodingAgent(provider=provider, model=model, mcp_servers=mcp_servers)
    except ValueError as exc:
        raise ValueError(f"Unknown cli_agent provider {provider!r}: {exc}") from exc


# --- Entry point -----------------------------------------------------------


def _load_toml_agent_config() -> dict[str, Any]:
    """Decode the ``[agent.cli_agent]`` TOML block forwarded by the launcher.

    The sregym launcher (``scripts/run_sregym.py`` → ``config_to_env``)
    serializes the per-agent TOML block as a JSON string in
    ``SREGYM_EXPERIMENT_AGENT_CONFIG``. An empty/missing var is a valid
    "use defaults" signal; we don't error.
    """
    raw = os.getenv("SREGYM_EXPERIMENT_AGENT_CONFIG")
    if not raw:
        return {}
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning("SREGYM_EXPERIMENT_AGENT_CONFIG is not valid JSON (%s); ignoring", exc)
        return {}
    if not isinstance(decoded, dict):
        logger.warning(
            "SREGYM_EXPERIMENT_AGENT_CONFIG must decode to an object; got %s — ignoring",
            type(decoded).__name__,
        )
        return {}
    return cast("dict[str, Any]", decoded)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI args, using ``[agent.cli_agent]`` TOML values as defaults.

    Precedence: explicit CLI flag > TOML (``SREGYM_EXPERIMENT_AGENT_CONFIG``)
    > env var (``MODEL_ID``) > hardcoded default.
    """
    toml_cfg = _load_toml_agent_config()

    parser = argparse.ArgumentParser(
        description="Minimal SRE Gym agent that wraps a agentshim CLI.",
    )
    parser.add_argument(
        "--provider",
        default=toml_cfg.get("provider", "claude"),
        help=(
            "CLI provider from agentshim.CodingAgent "
            "(must support mcp_servers: currently claude or codex; default: claude)"
        ),
    )
    parser.add_argument(
        "--model",
        default=toml_cfg.get("model", os.getenv("MODEL_ID", "claude-sonnet-4-6")),
        help="Model identifier passed to the CLI (default: $MODEL_ID or claude-sonnet-4-6)",
    )
    parser.add_argument(
        "--logs-dir",
        default=toml_cfg.get("logs_dir"),
        help="Directory to write a per-run results JSON (optional)",
    )
    parser.add_argument(
        "--timeout-sec",
        type=int,
        default=int(toml_cfg.get("timeout_sec", 2000)),
        help=(
            "Whole-session CLI timeout in seconds. One CLI run covers all "
            "planned stages (diagnosis + mitigation), so this is the budget "
            "for the entire problem (default: 2000)"
        ),
    )
    parser.add_argument(
        "--autonomous-prompt-profile",
        choices=sorted(_AUTONOMOUS_PROMPT_PROFILES),
        default=_normalize_autonomous_prompt_profile(toml_cfg.get("autonomous_prompt_profile")),
        help=(
            "Autonomous prompt variant to use when SREGYM_AUTONOMOUS_SUBMIT=1. "
            "'sds' keeps the repo-local .sds operational-tooling workflow; "
            "'direct' removes .sds/script-writing expectations and focuses on direct "
            "source + cluster investigation (default: sds)."
        ),
    )
    parser.add_argument(
        "--application-workspace-mode",
        choices=sorted(_APPLICATION_WORKSPACE_MODES),
        default=_normalize_application_workspace_mode(toml_cfg.get("application_workspace_mode")),
        help=(
            "Application workspace mode forwarded by the benchmark runner. "
            "Observer detector guidance is enabled only for autonomous persistent workspaces "
            "(default: none)."
        ),
    )
    parser.add_argument(
        "--memory-enabled",
        action=argparse.BooleanOptionalAction,
        default=bool(toml_cfg.get("memory_enabled", False)),
        help=(
            "Enable persistent incident memory: a `recall` MCP tool for the agent "
            "to query past lessons, plus a post-confirmation lesson write. Only "
            "writes after the conductor confirms the outcome (default: off)."
        ),
    )
    parser.add_argument(
        "--memory-dir",
        default=toml_cfg.get("memory_dir"),
        help=(
            "Directory holding the per-app lesson store (one `{app}.jsonl` per app). "
            "Must persist across runs and live OUTSIDE the ephemeral SREGYM_EXP_ENV "
            "workdir (default: ~/.sds/cli_agent_memory)."
        ),
    )
    # No-op flags accepted for compatibility with sregym's agent launcher
    # (`bench/sregym/main.py` ~L1314-L1330), which appends these to every
    # agent's argv depending on the experiment config — cli_agent has no
    # summary/knowledge-base feature to control, so they are ignored.
    parser.add_argument("--no-inject-summary", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--enable-summary", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--summary-dir", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--summary-model", default=None, help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def _run(
    args: argparse.Namespace,
    *,
    agent_factory: Callable[[str, str, str], BaseCodingAgent] | None = None,
) -> None:
    logger.info("cli_agent driver starting (provider=%s, model=%s)", args.provider, args.model)

    api_base = get_api_base()
    mcp_port = os.getenv("MCP_SERVER_PORT", "9954")
    submit_mcp_url = f"http://localhost:{mcp_port}/submit/sse"

    # Captured before the chdir below so a relative --memory-dir resolves
    # outside the ephemeral SREGYM_EXP_ENV workdir, not inside it.
    base_cwd = os.getcwd()

    agent_workdir = os.getenv("SREGYM_AGENT_WORKDIR") or os.getenv("SREGYM_EXP_ENV")
    if agent_workdir:
        if not os.path.isdir(agent_workdir):
            logger.error("Agent workdir %s is not a valid directory", agent_workdir)
            sys.exit(1)
        os.chdir(agent_workdir)
        logger.info("Working directory: %s", os.getcwd())
    else:
        logger.warning("SREGYM_EXP_ENV is not set — running in cwd: %s", os.getcwd())

    poll_stage_sync(api_base, wait_for=READY_STAGES, timeout=300, on_timeout="raise")

    app_info = get_app_info(api_base)
    problem_id = get_problem_id(api_base)
    planned_stages = get_planned_stages(api_base)

    logger.info(
        "Problem: %s | App: %s | Stages: %s",
        problem_id,
        app_info.get("app_name", "?"),
        planned_stages,
    )

    extra_mcp_servers: list[Any] = []

    # Persistent incident memory (opt-in). Start a read-only `recall` MCP
    # server bound to this app's lesson store and wire it in; the matching
    # write happens out-of-band after the conductor confirms the outcome.
    app_name = app_info.get("app_name") or "unknown"
    memory_enabled = bool(getattr(args, "memory_enabled", False))
    memory_store = None
    recall_server = None
    if memory_enabled:
        from agentshim.mcp_config import HttpMcpServer

        from sregym_agents.cli_agent.memory.recall_server import RecallServer
        from sregym_agents.cli_agent.memory.store import LessonStore

        store_dir = _resolve_memory_dir(args, base_cwd=base_cwd)
        memory_store = LessonStore(store_dir)
        recall_server = RecallServer(memory_store, app_name)
        recall_server.start()
        extra_mcp_servers.append(HttpMcpServer(name="memory", url=recall_server.url))
        logger.info("Memory enabled: store=%s recall=%s", store_dir, recall_server.url)

    if agent_factory is not None:
        factory = agent_factory
    else:
        factory = functools.partial(_default_agent_factory, extra_mcp_servers=extra_mcp_servers)

    # Single CLI session for the whole problem. In the default mode the
    # agent calls a stage-routing `submit` tool and reads the oracle verdict
    # from its response. In autonomous mode (SREGYM_AUTONOMOUS_SUBMIT=1) the
    # agent instead calls per-stage `submit_diagnosis` / `submit_mitigation`
    # tools that return a neutral ack, and must self-verify via kubectl.
    autonomous = os.getenv("SREGYM_AUTONOMOUS_SUBMIT", "").strip() == "1"
    submit_done_returns_feedback = os.getenv("SREGYM_SUBMIT_DONE_RETURNS_FEEDBACK", "").strip() == "1"
    application_workspace_mode = _normalize_application_workspace_mode(
        getattr(args, "application_workspace_mode", _DEFAULT_APPLICATION_WORKSPACE_MODE)
    )
    observer_detectors_enabled = _observer_detectors_enabled(
        autonomous=autonomous,
        autonomous_prompt_profile=args.autonomous_prompt_profile,
        application_workspace_mode=application_workspace_mode,
    )
    observer_preflight_report = ""
    if observer_detectors_enabled:
        observer_preflight_report = _maybe_build_observer_preflight_report(
            app_root=Path(os.getcwd()),
            namespace=str(app_info.get("namespace") or ""),
        )
    prompt = _build_prompt(
        planned_stages,
        app_info,
        autonomous=autonomous,
        autonomous_prompt_profile=args.autonomous_prompt_profile,
        submit_done_returns_feedback=submit_done_returns_feedback,
        memory_enabled=memory_enabled,
        observer_detectors_enabled=observer_detectors_enabled,
        observer_preflight_report=observer_preflight_report,
    )
    started = time.monotonic()
    crashed_with: str | None = None
    agent = None
    session = None
    try:
        agent = factory(args.provider, args.model, submit_mcp_url)
        # A stateful session (not one-shot generate) so the same conversation
        # can be resumed after the verdict to extract a lesson — the agent's
        # full investigation is already in its context window.
        session = agent.start_session(cwd=os.getcwd(), timeout=args.timeout_sec)
        session.generate(prompt, cwd=os.getcwd(), timeout=args.timeout_sec)
    except Exception as exc:
        logger.exception("CLI agent raised")
        crashed_with = f"{type(exc).__name__}: {exc}"
    elapsed = time.monotonic() - started

    if autonomous:
        # Autonomous mode: submit_autonomous grades synchronously and does not
        # advance the sequential state machine, so the conductor will never
        # reach a terminal stage on its own — the worker's force_cleanup runs
        # after the agent process exits. Report the current stage without
        # blocking for a transition that will never happen.
        final_stage = get_current_stage_sync(api_base)
        completed = final_stage is not None
    else:
        # Brief grace wait so the conductor can finish the last
        # `(verifying)` window if the agent's final submit returned just
        # before the oracle finished grading.
        expected: set[str] = set(TERMINAL_STAGES)
        final_stage = wait_for_stages_or_last_seen_sync(api_base, expected=expected, timeout=_POST_STAGE_TIMEOUT_S)
        completed = final_stage in expected
    logger.info(
        "session done; elapsed=%.1fs final_stage=%r completed=%s crashed=%s",
        elapsed,
        final_stage,
        completed,
        bool(crashed_with),
    )

    if observer_detectors_enabled and crashed_with is None and session is not None:
        _maybe_repair_observer_diagnostics(
            session=session,
            app_root=Path(os.getcwd()),
            namespace=str(app_info.get("namespace") or ""),
            repair_timeout=args.timeout_sec,
        )

    # Verified-only write (§3): extract a lesson only when the environment
    # confirmed the outcome, and never if the agent crashed mid-run.
    if memory_enabled and memory_store is not None:
        try:
            if crashed_with is None and session is not None:
                _maybe_write_lesson(
                    session=session,
                    store=memory_store,
                    app=app_name,
                    autonomous=autonomous,
                    completed=completed,
                    final_stage=final_stage,
                    planned_stages=planned_stages,
                    timeout=args.timeout_sec,
                )
        finally:
            if recall_server is not None:
                recall_server.stop()

    if args.logs_dir:
        logs_dir = Path(args.logs_dir)
        logs_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out = logs_dir / f"cli_agent_results_{problem_id}_{ts}.json"
        # Prefer cumulative usage (summed across every CLI invocation,
        # including post-submit detector-repair reprompts); fall back to the
        # last invocation only if the backend predates cumulative tracking.
        run_usage = getattr(agent, "cumulative_usage", None) if agent is not None else None
        if run_usage is None and agent is not None:
            run_usage = getattr(agent, "last_usage", None)
        usage_metrics: dict[str, Any] | None = {"total": run_usage.to_dict()} if run_usage is not None else None
        with open(out, "w") as f:
            json.dump(
                {
                    "problem_id": problem_id,
                    "provider": args.provider,
                    "model": args.model,
                    "planned_stages": planned_stages,
                    "elapsed_s": elapsed,
                    "completed": completed,
                    "final_stage": final_stage,
                    "crashed_with": crashed_with,
                    "usage_metrics": usage_metrics,
                },
                f,
                indent=2,
            )
        logger.info("Saved results to %s", out)

    logger.info("cli_agent driver complete.")


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    _run(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
