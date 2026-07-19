"""Classify a recovered diagnosis to a KB slug for the recovery playbook shortcut.

When the benchmark rejected the agent's diagnosis but the recovery agent produced
a grounded corrected answer, we can still try to apply a mitigation playbook from
the long-term summary. This module runs a single LLM pass that picks a slug from
the KB's known root-cause classes, or returns ``None`` when no class fits.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from benchmarks.sregym.participants.crucible.agents.base import AgentDriver
    from libs.pydantic_agent import UsageCollector

logger = logging.getLogger(__name__)


class RecoveryPlaybookMatch(BaseModel):
    """LLM classifier output for recovery-path playbook selection."""

    slug: str | None = Field(
        default=None,
        description="Matched slug from the long-term summary, or null if none fits.",
    )
    reasoning: str = Field(
        default="",
        description="Brief explanation of why this slug matches (or why none does).",
    )


_SYSTEM_PROMPT = """\
You are a classifier. Given a knowledge-base long-term summary containing root-cause \
classes (each identified by a slug) and a recovered diagnosis for the current \
incident, pick the single slug whose root-cause class best matches the recovered \
diagnosis.

Rules:
- Output exactly one slug from the list below, or null if no class is a clear match.
- Do not invent slugs. Only slugs present in the summary are valid.
- Prefer null over a weak match — a wrong playbook is worse than no playbook.
- Match on the underlying root-cause mechanism, not surface wording.
"""


def _render_user_prompt(
    *,
    summary_text: str,
    recovery_answer: str,
    recovery_justification: str,
    recovery_causal_chain: str,
    benchmark_reasoning: str,
    valid_slugs: list[str],
) -> str:
    slug_list = "\n".join(f"- {s}" for s in valid_slugs) or "(none)"
    parts = [
        "## Long-Term Summary (knowledge base)",
        summary_text.strip() or "(empty)",
        "",
        "## Valid slugs (pick exactly one, or null)",
        slug_list,
        "",
        "## Recovered Diagnosis",
        f"**Answer**: {recovery_answer}",
    ]
    if recovery_justification:
        parts.append(f"**Justification**: {recovery_justification}")
    if recovery_causal_chain:
        parts.append(f"**Causal Chain**: {recovery_causal_chain}")
    if benchmark_reasoning:
        parts += ["", "## Benchmark Oracle Reasoning", benchmark_reasoning]
    parts += [
        "",
        "Return a JSON object with `slug` (one of the valid slugs, or null) and `reasoning`.",
    ]
    return "\n".join(parts)


async def classify_recovery_playbook(
    *,
    driver: AgentDriver,
    model_id: Any,
    summary_text: str,
    recovery_answer: str,
    recovery_justification: str = "",
    recovery_causal_chain: str = "",
    benchmark_reasoning: str = "",
    usage_collector: UsageCollector | None = None,
) -> str | None:
    """Return a KB slug for the recovered diagnosis, or ``None`` if no match.

    The returned slug is guaranteed to appear in the long-term summary. The
    caller must still verify that a mitigation playbook exists for it.
    """
    from benchmarks.sregym.participants.crucible.knowledge_base.merge_result import extract_class_slugs

    del model_id  # driver carries the model binding; kept for future per-call overrides

    if not summary_text or not summary_text.strip():
        logger.info("[recovery-playbook-classifier] Skipped — empty long-term summary.")
        return None
    if not recovery_answer or not recovery_answer.strip():
        logger.info("[recovery-playbook-classifier] Skipped — empty recovery answer.")
        return None

    class_slugs = extract_class_slugs(summary_text)
    valid_slugs = sorted(set(class_slugs.values()))
    if not valid_slugs:
        logger.info("[recovery-playbook-classifier] Skipped — no slugs in summary.")
        return None

    user_prompt = _render_user_prompt(
        summary_text=summary_text,
        recovery_answer=recovery_answer,
        recovery_justification=recovery_justification,
        recovery_causal_chain=recovery_causal_chain,
        benchmark_reasoning=benchmark_reasoning,
        valid_slugs=valid_slugs,
    )

    try:
        result = await driver.run(
            prompt=user_prompt,
            system_prompt=_SYSTEM_PROMPT,
            tools=None,
            output_type=RecoveryPlaybookMatch,
            agent_name="recovery-playbook-classifier",
            usage_collector=usage_collector,
        )
    except Exception as exc:
        logger.warning("[recovery-playbook-classifier] Driver failed: %s", exc)
        return None

    if result.output is None:
        logger.info("[recovery-playbook-classifier] No output — returning None.")
        return None

    picked = (result.output.slug or "").strip() or None
    if picked is None:
        logger.info(
            "[recovery-playbook-classifier] No match. Reasoning: %s",
            result.output.reasoning,
        )
        return None

    if picked not in valid_slugs:
        logger.warning(
            "[recovery-playbook-classifier] Rejected invalid slug %r (not in %d known slugs).",
            picked,
            len(valid_slugs),
        )
        return None

    logger.info(
        "[recovery-playbook-classifier] Picked slug=%r. Reasoning: %s",
        picked,
        result.output.reasoning,
    )
    return picked
