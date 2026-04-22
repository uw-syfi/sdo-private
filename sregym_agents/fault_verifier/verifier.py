"""Agent-based fault-injection verifier.

Given a problem's expected fault description and a live cluster kubeconfig,
spawn a Claude agent (via `agentshim`) to investigate whether:

  1. the expected fault is actually present, and
  2. any other unintended faults are present.

Returns a structured `FaultVerification` dataclass. Parsing failures are
recorded in the dataclass (no exceptions), so the caller can treat a
"verifier couldn't decide" differently from a hard error.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

from ._prompts import build_prompt

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = [
    "FaultVerification",
    "build_prompt",
    "parse_agent_output",
    "run_fault_verifier",
]


@dataclass
class FaultVerification:
    fault_confirmed: bool | None
    other_faults: list[str] = field(default_factory=list[str])
    reasoning: str = ""
    raw_output: str = ""
    parse_error: str | None = None
    elapsed_s: float = 0.0


_JSON_BLOCK_RE = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)
_REQUIRED_KEYS = frozenset({"fault_confirmed", "other_faults", "reasoning"})


def parse_agent_output(raw: str, elapsed_s: float) -> FaultVerification:
    """Extract the terminating ```json fenced block and validate its shape."""
    matches = _JSON_BLOCK_RE.findall(raw)
    if not matches:
        return FaultVerification(
            fault_confirmed=None,
            raw_output=raw,
            parse_error="no fenced ```json block found in agent output",
            elapsed_s=elapsed_s,
        )

    last = matches[-1]
    try:
        data: Any = json.loads(last)
    except json.JSONDecodeError as exc:
        return FaultVerification(
            fault_confirmed=None,
            raw_output=raw,
            parse_error=f"malformed json block: {exc}",
            elapsed_s=elapsed_s,
        )

    if not isinstance(data, dict):
        return FaultVerification(
            fault_confirmed=None,
            raw_output=raw,
            parse_error="json block is not an object",
            elapsed_s=elapsed_s,
        )

    data_dict = cast("dict[str, Any]", data)
    missing = _REQUIRED_KEYS - set(data_dict.keys())
    if missing:
        return FaultVerification(
            fault_confirmed=None,
            raw_output=raw,
            parse_error=f"json block missing required keys: {sorted(missing)}",
            elapsed_s=elapsed_s,
        )

    fault_confirmed: Any = data_dict["fault_confirmed"]
    if not isinstance(fault_confirmed, bool):
        return FaultVerification(
            fault_confirmed=None,
            raw_output=raw,
            parse_error="fault_confirmed must be a bool",
            elapsed_s=elapsed_s,
        )

    other_faults_raw: Any = data_dict["other_faults"]
    if not isinstance(other_faults_raw, list):
        return FaultVerification(
            fault_confirmed=None,
            raw_output=raw,
            parse_error="other_faults must be a list",
            elapsed_s=elapsed_s,
        )
    other_faults_list = cast("list[Any]", other_faults_raw)
    other_faults: list[str] = [str(x) for x in other_faults_list]

    reasoning = str(data_dict["reasoning"])

    return FaultVerification(
        fault_confirmed=fault_confirmed,
        other_faults=other_faults,
        reasoning=reasoning,
        raw_output=raw,
        parse_error=None,
        elapsed_s=elapsed_s,
    )


def _default_agent_factory(model: str | None, kubeconfig_path: str) -> Any:
    from agentshim.claude import ClaudeCodeCodingAgent

    agent = ClaudeCodeCodingAgent(model=model)
    # Claude's bash subprocesses inherit this env. Pointing KUBECONFIG at
    # the per-worker unfiltered kubeconfig is how the agent gets cluster
    # access without anyone filtering chaos-mesh / khaos namespaces away.
    agent.env["KUBECONFIG"] = kubeconfig_path
    return agent


def run_fault_verifier(
    *,
    problem_id: str,
    root_cause: str,
    app_name: str,
    namespace: str,
    kubeconfig_path: str,
    model: str | None = None,
    timeout_s: int = 300,
    agent_factory: Callable[[], Any] | None = None,
) -> FaultVerification:
    """Run one fault-verification pass and return a structured verdict.

    `agent_factory` is the test seam — pass a zero-arg callable returning
    an object with `generate(prompt, timeout=...)`. In production callers
    omit it and a `ClaudeCodeCodingAgent` is built with `KUBECONFIG` set
    to `kubeconfig_path`.
    """
    prompt = build_prompt(
        problem_id=problem_id,
        root_cause=root_cause,
        app_name=app_name,
        namespace=namespace,
    )

    factory = agent_factory or (lambda: _default_agent_factory(model, kubeconfig_path))

    started = time.monotonic()
    try:
        agent = factory()
        raw = agent.generate(prompt, timeout=timeout_s)
    except Exception as exc:
        return FaultVerification(
            fault_confirmed=None,
            raw_output="",
            parse_error=f"{type(exc).__name__}: {exc}",
            elapsed_s=time.monotonic() - started,
        )

    elapsed = time.monotonic() - started
    return parse_agent_output(raw or "", elapsed)
