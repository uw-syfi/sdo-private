"""Typed representation for the ``<benchmark_result>`` block.

The crucible orchestrator and judge emit a textual ``<benchmark_result>...
</benchmark_result>`` block that is appended to a shared file shown to LLMs.
Historically that block was constructed inline via f-strings at ~8 sites and
parsed back out via regex in several places — see GitLab issues #89 and #99.

This module centralizes both directions:

* :class:`BenchmarkResult` / :class:`Oracle` — dataclasses carrying the data.
* :meth:`BenchmarkResult.render` — the **only** f-string producing the
  ``<benchmark_result>`` / ``<oracle>`` markup.
* :meth:`BenchmarkResult.parse` — the **only** regex site that reads that
  markup back out.

Keep it that way: if you need another serialization shape, add a helper
method here rather than re-introducing inline f-strings elsewhere.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Literal, cast

Stage = Literal["diagnosis", "mitigation"]

_ORACLE_TAG = "oracle"
_BENCHMARK_TAG = "benchmark_result"
_BENCHMARK_RE = re.compile(rf"<{_BENCHMARK_TAG}>\s*(.*?)\s*</{_BENCHMARK_TAG}>", re.DOTALL)
_ORACLE_RE = re.compile(rf"<{_ORACLE_TAG}>\s*(.*?)\s*</{_ORACLE_TAG}>", re.DOTALL)
_SUCCESS_RE = re.compile(r"^\s*success:\s*(True|False)\s*$", re.MULTILINE)
_MESSAGE_RE = re.compile(r"^\s*message:\s*(.*)$", re.MULTILINE)
_ERROR_RE = re.compile(r"^\s*Error submitting to benchmark:\s*(.*)$", re.MULTILINE)


def _stage_key(stage: Stage) -> str:
    return "Mitigation" if stage == "mitigation" else "Diagnosis"


@dataclass(frozen=True)
class Oracle:
    """Structured view of the stage-scoped oracle payload.

    ``data`` holds the raw stage dict (i.e. the JSON nested under
    ``"Diagnosis"`` / ``"Mitigation"`` plus the sibling timing key if
    present). ``reasoning`` / ``matched_candidate_index`` are thin accessors
    so callers don't have to re-navigate the JSON themselves.
    """

    stage: Stage
    data: dict[str, Any] = field(default_factory=dict)  # pyright: ignore[reportUnknownVariableType]

    @property
    def reasoning(self) -> str:
        stage_dict: Any = self.data.get(_stage_key(self.stage), {})
        if not isinstance(stage_dict, dict):
            return ""
        value: Any = stage_dict.get("reasoning", "")  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        return value if isinstance(value, str) else ""

    @property
    def matched_candidate_index(self) -> int | None:
        stage_dict: Any = self.data.get(_stage_key(self.stage), {})
        if not isinstance(stage_dict, dict):
            return None
        idx: Any = stage_dict.get("matched_candidate_index")  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        if idx is None:
            return None
        try:
            return int(idx)  # pyright: ignore[reportUnknownArgumentType]
        except (TypeError, ValueError):
            return None


@dataclass(frozen=True)
class BenchmarkResult:
    """Typed representation of a ``<benchmark_result>`` block.

    Four render shapes are produced in practice:

    * success-with-oracle — ``success``, ``message``, and an ``oracle`` JSON.
    * success-without-oracle — same but ``oracle`` is ``None``.
    * error — populated via ``error`` when the MCP submit raises.
    * note-only — a free-form ``note`` string for cases where the orchestrator
      could not submit (e.g. the agent already submitted directly via HTTP and
      the conductor advanced the stage). The rendered block contains only the
      note text, matching the pre-refactor shape for that branch.

    The ``stage`` field mirrors the stage filter applied by
    :func:`submit_to_benchmark` (the oracle dict it returns is already
    stage-scoped), so parsing an external block also requires a caller-
    supplied ``stage`` hint for :meth:`Oracle.reasoning` to find the right
    sub-dict.
    """

    stage: Stage
    success: bool = False
    message: str = ""
    oracle: Oracle | None = None
    error: str | None = None
    note: str | None = None

    def render(self) -> str:
        """Serialize to the textual ``<benchmark_result>`` form.

        This is the ONLY f-string site for ``<benchmark_result>`` / ``<oracle>``
        markup in the codebase. Any reader must use :meth:`parse`.
        """
        if self.error is not None:
            return f"\n<{_BENCHMARK_TAG}>\nError submitting to benchmark: {self.error}\n</{_BENCHMARK_TAG}>\n"
        if self.note is not None:
            return f"\n<{_BENCHMARK_TAG}>\nmessage: {self.note}\n</{_BENCHMARK_TAG}>\n"
        oracle_text = ""
        if self.oracle is not None:
            oracle_text = f"<{_ORACLE_TAG}>\n{json.dumps(self.oracle.data, indent=2)}\n</{_ORACLE_TAG}>"
        return (
            f"\n<{_BENCHMARK_TAG}>\n"
            f"success: {self.success}\n"
            f"message: {self.message}\n"
            f"{oracle_text}\n"
            f"</{_BENCHMARK_TAG}>\n"
        )

    @classmethod
    def parse(cls, text: str, stage: Stage = "diagnosis") -> BenchmarkResult | None:
        """Parse a string that may contain a ``<benchmark_result>`` block.

        Returns ``None`` if no block is found. This is the ONLY regex site
        for ``<benchmark_result>`` / ``<oracle>`` in the codebase.

        Malformed oracle JSON degrades gracefully: the returned result has
        ``oracle=None`` rather than raising — historically callers fail
        open and we preserve that behavior.
        """
        match = _BENCHMARK_RE.search(text)
        if not match:
            return None
        body = match.group(1)

        error_match = _ERROR_RE.search(body)
        success_match = _SUCCESS_RE.search(body)
        message_match = _MESSAGE_RE.search(body)
        oracle = _parse_oracle(body, stage)

        if error_match and success_match is None:
            return cls(stage=stage, error=error_match.group(1).strip())

        # Note-only shape: message but no success/oracle/error.
        if success_match is None and error_match is None and oracle is None and message_match is not None:
            return cls(stage=stage, note=message_match.group(1).strip())

        success = success_match.group(1) == "True" if success_match else False
        message = message_match.group(1).strip() if message_match else ""
        return cls(stage=stage, success=success, message=message, oracle=oracle)


def _parse_oracle(text: str, stage: Stage) -> Oracle | None:
    match = _ORACLE_RE.search(text)
    if not match:
        return None
    try:
        data: Any = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    return Oracle(stage=stage, data=cast("dict[str, Any]", data))
