"""LLM-based retrieval over recorded trajectories.

The other half of the "two ways to retrieve" (the first being embedding/RAG in
``embedding.py``): instead of cosine similarity, an LLM reads the candidate past
runs and picks/synthesises the relevant ones, as an in-tool sub-agent. Two
backends:

- :class:`CodingAgentSearcher` (default) — reuses the *same* agentshim agent as
  the cli_agent (its provider/model, e.g. the ``claude`` CLI), so search shares
  the benchmark agent's auth and behaviour.
- :class:`CallSubagentSearcher` — a single isolated ``call_subagent`` litellm
  call to a distinct (often cheaper) model; opt in via
  ``trajectory_llm_search_model`` / ``$SDS_TRAJECTORY_LLM_SEARCH_MODEL``.

The searcher is a callable so it can be stubbed in tests. With neither a model
nor a provider/model available, :func:`default_searcher` degrades to
:class:`PassthroughSearcher` (candidates unranked) so the tool still functions.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from .store import TrajectoryDigest, TrajectoryStore

logger = logging.getLogger(__name__)

# Per-candidate content budget handed to the search LLM. Trajectories can be
# 50-100 KB; the narrative view (reasoning + tool sequence + outcome) is far
# smaller, and this caps it further so many candidates fit one prompt.
_PER_DOC_BUDGET = 3000

_SYSTEM = (
    "You are a retrieval assistant over an SRE team's past incident investigations on "
    "one application. Given the operator's current situation and a list of prior "
    "investigations (each with a run id and a narrative of what was checked and "
    "concluded), pick the few most relevant prior runs and explain, in 3-6 sentences, "
    "what they found that could help right now — the discriminating signals and the "
    "confirmed root cause/fix. Cite the run ids you used. If none are relevant, say so. "
    "These are hypotheses to verify against the live cluster, not answers."
)


class LLMSearcher(Protocol):
    """Selects/synthesises the relevant past runs for a query."""

    def __call__(self, query: str, digests: list[TrajectoryDigest], store: TrajectoryStore) -> str: ...


def _candidate_block(store: TrajectoryStore, d: TrajectoryDigest) -> str:
    narrative = store.narrative(d.path, budget=_PER_DOC_BUDGET)
    return f"### run {d.run_id} (outcome: {d.outcome or 'unknown'}, path: {d.path})\n{narrative}"


def _user_prompt(query: str, digests: list[TrajectoryDigest], store: TrajectoryStore) -> str:
    catalog = "\n\n".join(_candidate_block(store, d) for d in digests)
    return (
        f"Current situation:\n{query}\n\n"
        f"Past investigations on this application:\n{catalog}\n\n"
        "Which prior run(s) are most relevant, and what did they find that helps here?"
    )


class CodingAgentSearcher:
    """LLM searcher that reuses the *same* agentshim agent as the cli_agent.

    Spins up a one-shot ``CodingAgent(provider, model)`` (no MCP servers, no event
    handler) and asks it to synthesise the relevant prior runs from the catalog
    given inline. This shares the benchmark agent's provider/auth (e.g. the
    ``claude`` CLI) instead of a separate litellm/vertex path.
    """

    def __init__(self, provider: str, model: str, *, timeout: int = 120) -> None:
        self.provider = provider
        self.model = model
        self.timeout = timeout

    def __call__(self, query: str, digests: list[TrajectoryDigest], store: TrajectoryStore) -> str:
        from agentshim import CodingAgent

        prompt = f"{_SYSTEM}\n\nAnswer from the provided text only; do not use any tools.\n\n" + _user_prompt(
            query, digests, store
        )
        logger.info(
            "trajectory llm-search: %d candidate(s) via cli agent provider=%s model=%s",
            len(digests),
            self.provider,
            self.model,
        )
        agent = CodingAgent(provider=self.provider, model=self.model)
        return agent.generate(prompt, timeout=self.timeout, silent=True)


class CallSubagentSearcher:
    """LLM searcher backed by one ``call_subagent`` litellm call (a distinct,
    typically cheaper, model than the cli_agent)."""

    def __init__(self, model: str) -> None:
        self.model = model

    def __call__(self, query: str, digests: list[TrajectoryDigest], store: TrajectoryStore) -> str:
        from agentshim.subagent import call_subagent

        logger.info("trajectory llm-search: %d candidate(s) via model=%s", len(digests), self.model)
        return call_subagent(model=self.model, system_prompt=_SYSTEM, user_prompt=_user_prompt(query, digests, store))


class PassthroughSearcher:
    """Fallback when no searcher can be built: return candidate narratives
    unranked so the tool still works."""

    def __call__(self, query: str, digests: list[TrajectoryDigest], store: TrajectoryStore) -> str:
        return "\n\n".join(_candidate_block(store, d) for d in digests)


def default_searcher(
    *, litellm_model: str | None = None, provider: str | None = None, model: str | None = None
) -> LLMSearcher:
    """Pick the LLM-search backend.

    Priority: an explicit ``litellm_model`` (or ``$SDS_TRAJECTORY_LLM_SEARCH_MODEL``)
    → :class:`CallSubagentSearcher`; otherwise reuse the cli_agent's own
    ``provider``/``model`` → :class:`CodingAgentSearcher` (the default — "same agent
    as the configured cli agent"); otherwise :class:`PassthroughSearcher`.
    """
    litellm_model = litellm_model or os.getenv("SDS_TRAJECTORY_LLM_SEARCH_MODEL")
    if litellm_model:
        logger.info("trajectory llm-search: using call_subagent model=%s", litellm_model)
        return CallSubagentSearcher(litellm_model)
    if provider and model:
        logger.info("trajectory llm-search: using cli agent provider=%s model=%s", provider, model)
        return CodingAgentSearcher(provider, model)
    logger.warning("trajectory llm-search: no model/provider — returning candidates unranked")
    return PassthroughSearcher()
