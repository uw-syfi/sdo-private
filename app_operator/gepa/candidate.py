"""Candidate pool management with Pareto-based selection for GEPA."""

import random
from dataclasses import dataclass, field


@dataclass
class PromptCandidate:
    """A single prompt candidate in the evolution pool."""

    id: str
    template_name: str
    prompt_text: str
    parent_id: str | None = None
    generation: int = 0
    scores: dict[str, float] = field(default_factory=dict)
    validation_score: float | None = None
    mutation_type: str | None = None
    mutation_rationale: str | None = None


class CandidatePool:
    """Manages the pool of prompt candidates with multi-objective Pareto selection.

    Implements Pareto-based selection: candidates on the Pareto frontier
    (non-dominated candidates) are preferred, with sampling weighted by
    validation score (floored at 0.01 for diversity).
    """

    def __init__(self) -> None:
        self.candidates: list[PromptCandidate] = []

    def add(self, candidate: PromptCandidate) -> None:
        """Add a candidate to the pool."""
        self.candidates.append(candidate)

    def _is_dominated(self, a: PromptCandidate, b: PromptCandidate) -> bool:
        """Return True if b dominates a (b >= a in all objectives, b > a in at least one)."""
        all_keys = set(a.scores) | set(b.scores)
        if not all_keys:
            return False
        at_least_one_better = False
        for key in all_keys:
            sa = a.scores.get(key, 0.0)
            sb = b.scores.get(key, 0.0)
            if sb < sa:
                return False
            if sb > sa:
                at_least_one_better = True
        return at_least_one_better

    def _get_pareto_frontier(self) -> list[PromptCandidate]:
        """Identify non-dominated candidates (Pareto frontier)."""
        frontier = [
            candidate
            for candidate in self.candidates
            if not any(self._is_dominated(candidate, other) for other in self.candidates if other is not candidate)
        ]
        return frontier if frontier else list(self.candidates)

    def pareto_select(
        self,
        rng: random.Random | None = None,
    ) -> PromptCandidate:
        """Select a candidate using multi-objective Pareto-aware sampling.

        1. Identify the Pareto frontier (non-dominated candidates)
        2. Weight frontier candidates by their validation score
        3. Sample proportionally with a 0.01 floor for diversity

        Args:
            rng: Optional seeded Random instance for reproducibility.
                 Falls back to module-level random if not provided.
        """
        if not self.candidates:
            raise ValueError("Cannot select from an empty pool")
        if len(self.candidates) == 1:
            return self.candidates[0]

        frontier = self._get_pareto_frontier()

        weights = []
        for c in frontier:
            w = c.validation_score if c.validation_score is not None else 0.0
            weights.append(max(w, 0.01))

        total = sum(weights)
        weights = [w / total for w in weights]

        choices_fn = rng.choices if rng else random.choices
        return choices_fn(frontier, weights=weights, k=1)[0]

    def prune_dominated(self, max_pool_size: int = 20) -> None:
        """Remove dominated candidates to keep pool size manageable.

        Keeps all Pareto-frontier candidates, then fills remaining slots
        with best overall-score candidates from the dominated set.
        """
        if len(self.candidates) <= max_pool_size:
            return

        frontier_ids = {c.id for c in self._get_pareto_frontier()}
        frontier_candidates = [c for c in self.candidates if c.id in frontier_ids]
        dominated_candidates = [c for c in self.candidates if c.id not in frontier_ids]

        if len(frontier_candidates) >= max_pool_size:
            frontier_candidates.sort(key=lambda c: c.validation_score or 0.0, reverse=True)
            self.candidates = frontier_candidates[:max_pool_size]
        else:
            remaining = max_pool_size - len(frontier_candidates)
            dominated_candidates.sort(key=lambda c: c.validation_score or 0.0, reverse=True)
            self.candidates = frontier_candidates + dominated_candidates[:remaining]

    def get_best(self) -> PromptCandidate:
        """Return the best candidate by validation score."""
        if not self.candidates:
            raise ValueError("Cannot get best from an empty pool")
        return max(self.candidates, key=lambda c: c.validation_score or 0.0)

    def __len__(self) -> int:
        return len(self.candidates)
