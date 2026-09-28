"""Options for SREGym's stock Codex baseline (agent ``codex``).

The "Codex + verify" arm appends a generic verification protocol to the stock
instruction. It separates how much of SDO's advantage comes from its built-in
verify loop rather than its memory. The text is fault-agnostic on purpose: it
asks for good SRE practice and names no fault, resource or decoy.

SREGym's Codex driver appends ``SREGYM_AGENT_PROMPT_APPENDIX`` to its
instruction; unset, the instruction is unchanged.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, fields
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

PROMPT_APPENDIX_ENV = "SREGYM_AGENT_PROMPT_APPENDIX"

#: SREGym's agent container image and the hook module the appendix needs in it.
AGENT_IMAGE = "sregym-agent-base:latest"
_IMAGE_HOOK_PATH = "/opt/sregym/clients/harness/prompt_appendix.py"

VERIFY_PROTOCOL_PROMPT = """\
VERIFICATION PROTOCOL (REQUIRED):
Follow these steps in order. They apply to both tasks.

1. REPRODUCE BEFORE DIAGNOSING. Before you submit a diagnosis, reproduce a concrete user-facing symptom: \
for example, an end-to-end request to the application that fails or returns an error, or a Service that \
has no ready endpoints. Record the exact command you ran and its output.
2. TIE THE ROOT CAUSE TO THE SYMPTOM. Do not attribute the issue to a component until you can state the \
evidence that ties the root cause to that symptom. Include the symptom (the command and the relevant output) \
and that evidence in your diagnosis submission.
3. VERIFY THE FIX WITH THE SAME CHECK. After applying your fix, re-run the same symptom check. If it still \
fails, keep investigating and fixing, and re-run the check until it passes. Pod status alone (for example, \
`kubectl get pods`) does not show that the symptom is gone. Only then submit the mitigation.
4. CLEAN UP. Before you submit the mitigation, delete every helper pod, Job or other temporary resource you \
created (for example, pods used to send test requests or run debugging commands), and confirm that they are \
gone.
"""


@dataclass(frozen=True)
class CodexBaselineConfig:
    """``[agent.codex]`` settings of an experiment config."""

    verify_protocol: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.verify_protocol, bool):  # pyright: ignore[reportUnnecessaryIsInstance] - TOML input
            raise TypeError(f"agent.codex.verify_protocol must be a boolean, got {self.verify_protocol!r}")

    @classmethod
    def from_agent_config(cls, raw: Mapping[str, Any]) -> CodexBaselineConfig:
        known = {field.name for field in fields(cls)}
        unknown = sorted(set(raw) - known)
        if unknown:
            raise ValueError(f"unknown agent.codex settings {unknown}; known settings are {sorted(known)}")
        return cls(**raw)

    def prompt_appendix(self) -> str:
        """Text SREGym appends to the Codex instruction; empty keeps the stock prompt."""
        return VERIFY_PROTOCOL_PROMPT if self.verify_protocol else ""


class PromptAppendixImageError(RuntimeError):
    """The agent image predates the prompt-appendix hook, so the appendix would be silently dropped."""


def ensure_agent_image_supports_prompt_appendix(
    env: Mapping[str, str],
    *,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    image: str = AGENT_IMAGE,
) -> None:
    """Refuse to start an appendix run on an agent image without the hook.

    The Codex driver is baked into the image, and SREGym rebuilds the image
    only when it is missing. A stale image would run the stock prompt under
    the verify arm's name.
    """
    if not env.get(PROMPT_APPENDIX_ENV):
        return
    try:
        inspected = run(["docker", "image", "inspect", image], capture_output=True, text=True)
        if inspected.returncode != 0:
            return  # SREGym builds the missing image from this checkout, hook included.
        probe = run(["docker", "run", "--rm", image, f"test -f {_IMAGE_HOOK_PATH}"], capture_output=True, text=True)
    except OSError as exc:
        raise PromptAppendixImageError(f"cannot inspect agent image {image}: {exc}") from exc
    if probe.returncode != 0:
        raise PromptAppendixImageError(
            f"agent image {image} has no {_IMAGE_HOOK_PATH}, so it would drop {PROMPT_APPENDIX_ENV}. "
            "Rebuild it with third_party/sregym/docker/agents/build.sh and rerun."
        )
