"""Prompt candidate and pool management with Pareto selection."""

import random
import uuid
from dataclasses import dataclass, field


@dataclass
class PromptCandidate:
    """A single signature instruction variant."""

    signature_name: str
    instruction_text: str
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    parent_id: str | None = None
    generation: int = 0
    scores: dict[str, float] = field(default_factory=dict)
    overall_score: float | None = None
    mutation_type: str | None = None
    mutation_rationale: str | None = None


class CandidatePool:
    """Population of prompt candidates with Pareto-based selection."""

    def __init__(self, max_size: int = 20):
        self.max_size = max_size
        self._candidates: list[PromptCandidate] = []

    def add(self, candidate: PromptCandidate) -> None:
        self._candidates.append(candidate)

    @property
    def candidates(self) -> list[PromptCandidate]:
        return list(self._candidates)

    def get_best(self) -> PromptCandidate | None:
        scored = [c for c in self._candidates if c.overall_score is not None]
        if not scored:
            return None
        return max(scored, key=lambda c: c.overall_score)

    def pareto_select(self, rng: random.Random | None = None) -> PromptCandidate:
        """Select a candidate from the Pareto frontier, weighted by score."""
        rng = rng or random.Random()
        frontier = self._pareto_frontier()
        if not frontier:
            frontier = self._candidates
        weights = [max(c.overall_score or 0.01, 0.01) for c in frontier]
        return rng.choices(frontier, weights=weights, k=1)[0]

    def prune(self) -> None:
        """Prune pool to max_size, keeping Pareto frontier."""
        if len(self._candidates) <= self.max_size:
            return
        frontier = set(id(c) for c in self._pareto_frontier())
        # Keep all frontier candidates, fill rest by score
        in_frontier = [c for c in self._candidates if id(c) in frontier]
        not_frontier = sorted(
            [c for c in self._candidates if id(c) not in frontier],
            key=lambda c: c.overall_score or 0,
            reverse=True,
        )
        remaining = self.max_size - len(in_frontier)
        self._candidates = in_frontier + not_frontier[:max(0, remaining)]

    def _pareto_frontier(self) -> list[PromptCandidate]:
        """Find non-dominated candidates across all score dimensions."""
        scored = [c for c in self._candidates if c.scores]
        if not scored:
            return list(self._candidates)

        frontier = []
        for candidate in scored:
            dominated = False
            for other in scored:
                if other is candidate:
                    continue
                if _dominates(other.scores, candidate.scores):
                    dominated = True
                    break
            if not dominated:
                frontier.append(candidate)
        return frontier or scored


def _dominates(a: dict[str, float], b: dict[str, float]) -> bool:
    """Return True if a dominates b (>= in all metrics, > in at least one)."""
    keys = set(a.keys()) & set(b.keys())
    if not keys:
        return False
    all_geq = all(a.get(k, 0) >= b.get(k, 0) for k in keys)
    any_gt = any(a.get(k, 0) > b.get(k, 0) for k in keys)
    return all_geq and any_gt
