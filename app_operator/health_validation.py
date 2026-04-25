from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from pathlib import Path

    from agentshim import BaseCodingAgent

    from app_operator.core import CommandResult
    from app_operator.dspy_integration import DSPyConfig
    from app_operator.trajectory import TrajectoryRecorderProtocol

from app_operator.core import logger
from app_operator.prompts import get_loader

_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")
_FAILURE_MARKERS = (
    "not running",
    "is not running",
    "not healthy",
    "unhealthy",
    "failed to connect",
    "[ fail ]",
    "[fail]",
    "[ error ]",
    "[error]",
)
_FAILURE_REGEXES = (
    re.compile(r"Failed:\s*[1-9]\d*"),
    re.compile(r"\[\s*FAIL\s*\]"),
    re.compile(r"\[\s*ERROR\s*\]"),
)
_BENIGN_WARNING_MARKERS = ("attribute `version` is obsolete",)
_VERDICT_TAG_RE = re.compile(r"<health_verdict>(healthy|unhealthy)</health_verdict>", re.IGNORECASE)
_REASON_TAG_RE = re.compile(r"<reason>(.*?)</reason>", re.IGNORECASE | re.DOTALL)


@dataclass(frozen=True)
class HealthValidationResult:
    """Normalized health verdict used to gate deployment/monitor success."""

    is_healthy: bool
    reason: str
    source: str
    confidence: float | None = None
    failure_signals: list[str] = field(default_factory=list)  # pyright: ignore[reportUnknownVariableType]
    agent_verdict: str | None = None


@dataclass(frozen=True)
class _HeuristicAssessment:
    is_healthy: bool
    reason: str
    failure_signals: list[str] = field(default_factory=list)  # pyright: ignore[reportUnknownVariableType]


def validate_health_check_result(
    *,
    health_result: CommandResult,
    agent: BaseCodingAgent,
    repo_path: Path,
    check_context: str,
    timeout: int,
    dspy_config: DSPyConfig | None = None,
    recorder: TrajectoryRecorderProtocol | None = None,
) -> HealthValidationResult:
    """Validate health check output using deterministic rules + agent verdict.

    Deterministic checks run first to catch obvious contradictions (e.g. exit 0
    with `[FAIL]` in output). Agent validation is advisory when heuristics pass:
    agent "unhealthy" verdicts are accepted only when corroborated by concrete
    deterministic failure signals. If the agent response is unavailable/
    unparseable, we fall back to heuristic assessment to avoid spurious hard
    failures caused by provider hiccups.
    """
    heuristic = _heuristic_assessment(health_result)
    if not heuristic.is_healthy:
        return HealthValidationResult(
            is_healthy=False,
            reason=heuristic.reason,
            source="heuristic",
            failure_signals=heuristic.failure_signals,
            agent_verdict="skipped",
        )

    agent_result = _agent_assessment(
        health_result=health_result,
        agent=agent,
        repo_path=repo_path,
        check_context=check_context,
        timeout=timeout,
        dspy_config=dspy_config,
        recorder=recorder,
    )
    if agent_result is None:
        return HealthValidationResult(
            is_healthy=heuristic.is_healthy,
            reason=(
                "Heuristic validation passed; agent verdict unavailable or unparseable. "
                "Proceeding with heuristic fallback."
            ),
            source="heuristic_fallback",
            failure_signals=heuristic.failure_signals,
            agent_verdict=None,
        )

    verdict = agent_result.get("verdict")
    is_healthy = verdict == "healthy"
    reason = str(agent_result.get("reason") or "Agent-provided health verdict")
    confidence = _normalize_confidence(agent_result.get("confidence"))
    if not is_healthy and heuristic.is_healthy:
        corroborating_signals = _agent_unhealthy_failure_signals(health_result)
        if not corroborating_signals:
            return HealthValidationResult(
                is_healthy=True,
                reason=(
                    "Heuristic validation passed; agent returned unhealthy "
                    "without concrete failure signals in health output. "
                    "Ignoring agent veto."
                ),
                source="heuristic_override",
                confidence=confidence,
                failure_signals=heuristic.failure_signals,
                agent_verdict=verdict,
            )
    return HealthValidationResult(
        is_healthy=is_healthy,
        reason=reason,
        source="agent",
        confidence=confidence,
        failure_signals=heuristic.failure_signals,
        agent_verdict=verdict,
    )


def _heuristic_assessment(health_result: CommandResult) -> _HeuristicAssessment:
    """Fast deterministic check that catches obvious false-positive signals."""
    if not health_result.get("success", False):
        exit_code = int(health_result.get("exit_code", -1))
        return _HeuristicAssessment(
            is_healthy=False,
            reason=f"Health script exited non-zero (exit_code={exit_code}).",
            failure_signals=[f"exit_code={exit_code}"],
        )

    combined = _ANSI_ESCAPE.sub("", f"{health_result.get('stdout', '')}\n{health_result.get('stderr', '')}").lower()
    signals = _collect_failure_signals(combined)

    if signals:
        deduped = sorted(set(signals))
        return _HeuristicAssessment(
            is_healthy=False,
            reason=(
                "Health script returned exit_code=0 but output includes failure signals: " + ", ".join(deduped[:5])
            ),
            failure_signals=deduped,
        )

    return _HeuristicAssessment(
        is_healthy=True,
        reason="Health script exited 0 and no failure markers were detected.",
    )


def _collect_failure_signals(text: str) -> list[str]:
    signals: list[str] = [marker for marker in _FAILURE_MARKERS if marker in text]
    signals.extend(regex.pattern for regex in _FAILURE_REGEXES if regex.search(text))
    return sorted(set(signals))


def _agent_unhealthy_failure_signals(health_result: CommandResult) -> list[str]:
    """Return deterministic failure signals that corroborate an agent veto."""
    combined = _ANSI_ESCAPE.sub("", f"{health_result.get('stdout', '')}\n{health_result.get('stderr', '')}").lower()
    filtered_lines = [
        line for line in combined.splitlines() if not any(marker in line for marker in _BENIGN_WARNING_MARKERS)
    ]
    filtered = "\n".join(filtered_lines)
    return _collect_failure_signals(filtered)


def _agent_assessment(
    *,
    health_result: CommandResult,
    agent: BaseCodingAgent,
    repo_path: Path,
    check_context: str,
    timeout: int,
    dspy_config: DSPyConfig | None,
    recorder: TrajectoryRecorderProtocol | None,
) -> dict[str, Any] | None:
    """Ask the coding agent for a strict health verdict."""
    prompt = get_loader(dspy_config).render(
        "monitor/validate_health.jinja2",
        repo_path=repo_path,
        check_context=check_context,
        exit_code=int(health_result.get("exit_code", -1)),
        stdout=health_result.get("stdout", ""),
        stderr=health_result.get("stderr", ""),
        recorder=recorder,
    )

    try:
        raw = agent.generate(
            prompt,
            cwd=str(repo_path),
            timeout=timeout,
            silent=True,
        )
    except (OSError, RuntimeError, ValueError) as e:
        logger.warning("Health validation agent call failed: %s", e)
        return None

    parsed = _parse_agent_response(raw)
    if parsed is None:
        logger.warning("Health validation agent response was unparseable; falling back to heuristics")
    return parsed


def _parse_agent_response(raw: str) -> dict[str, Any] | None:
    """Parse an agent response into a normalized `{verdict, reason, confidence}` dict."""
    text = str(raw or "").strip()
    if not text:
        return None

    payload = _extract_first_json_object(text)
    if payload is None:
        payload = _extract_from_xml_tags(text)
    if payload is None:
        return None

    verdict = str(payload.get("verdict") or "").strip().lower()
    if verdict not in ("healthy", "unhealthy"):
        return None

    reason = str(payload.get("reason") or "").strip()
    if not reason:
        reason = "No reason provided."

    return {
        "verdict": verdict,
        "reason": reason,
        "confidence": payload.get("confidence"),
    }


def _extract_from_xml_tags(text: str) -> dict[str, Any] | None:
    """Support a minimal XML-tag fallback when the model ignores JSON instructions."""
    verdict_match = _VERDICT_TAG_RE.search(text)
    if not verdict_match:
        return None
    verdict = verdict_match.group(1).strip().lower()
    reason_match = _REASON_TAG_RE.search(text)
    reason = reason_match.group(1).strip() if reason_match else "No reason provided."
    return {"verdict": verdict, "reason": reason}


def _extract_first_json_object(text: str) -> dict[str, Any] | None:
    """Extract the first JSON object from free-form text."""
    decoder = json.JSONDecoder()

    stripped = text.strip()
    try:
        obj = json.loads(stripped)
        if isinstance(obj, dict):
            return cast("dict[str, Any]", obj)
    except (json.JSONDecodeError, TypeError):
        pass

    for idx, char in enumerate(text):
        if char != "{":
            continue
        try:
            obj, _ = decoder.raw_decode(text[idx:])
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return cast("dict[str, Any]", obj)
    return None


def _normalize_confidence(value: Any) -> float | None:
    """Normalize confidence values into [0.0, 1.0] when possible."""
    if value is None:
        return None
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, min(1.0, confidence))
