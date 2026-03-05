"""Tests for CandidatePool and PromptCandidate."""

import pytest

from app_operator.gepa.candidate import CandidatePool, PromptCandidate


def _make_candidate(candidate_id, score=None, scores=None, **kwargs):
    """Helper to create a candidate with optional score."""
    c = PromptCandidate(
        id=candidate_id,
        template_name="deployer/system.jinja2",
        prompt_text=f"prompt for {candidate_id}",
        scores=scores or {},
        **kwargs,
    )
    c.validation_score = score
    return c


class TestIsDominated:
    """Test CandidatePool._is_dominated()."""

    def test_a_not_dominated_when_better_on_one(self):
        pool = CandidatePool()
        a = _make_candidate("a", scores={"m1": 0.8, "m2": 0.2})
        b = _make_candidate("b", scores={"m1": 0.3, "m2": 0.9})
        assert pool._is_dominated(a, b) is False

    def test_partial_domination_b_better_everywhere(self):
        """b >= a on all metrics, b > a on at least one."""
        pool = CandidatePool()
        a = _make_candidate("a", scores={"m1": 0.3, "m2": 0.3})
        b = _make_candidate("b", scores={"m1": 0.3, "m2": 0.4})
        assert pool._is_dominated(a, b) is True

    def test_missing_key_defaults_to_zero(self):
        pool = CandidatePool()
        a = _make_candidate("a", scores={"m1": 0.0})
        b = _make_candidate("b", scores={"m1": 0.5, "m2": 0.5})
        assert pool._is_dominated(a, b) is True


class TestGetParetoFrontier:
    """Test CandidatePool._get_pareto_frontier()."""

    def test_all_tradeoffs_all_on_frontier(self):
        pool = CandidatePool()
        pool.add(_make_candidate("a", scores={"m1": 0.9, "m2": 0.1}))
        pool.add(_make_candidate("b", scores={"m1": 0.1, "m2": 0.9}))
        pool.add(_make_candidate("c", scores={"m1": 0.5, "m2": 0.5}))
        frontier = pool._get_pareto_frontier()
        assert len(frontier) == 3

    def test_dominated_excluded_from_frontier(self):
        pool = CandidatePool()
        pool.add(_make_candidate("good", scores={"m1": 0.9, "m2": 0.9}))
        pool.add(_make_candidate("bad", scores={"m1": 0.1, "m2": 0.1}))
        frontier = pool._get_pareto_frontier()
        assert len(frontier) == 1
        assert frontier[0].id == "good"


class TestCandidatePoolParetoSelect:
    """Test CandidatePool.pareto_select()."""

    def test_empty_pool_raises(self):
        pool = CandidatePool()
        with pytest.raises(ValueError, match="empty pool"):
            pool.pareto_select()

    def test_selects_from_frontier(self):
        pool = CandidatePool()
        pool.add(_make_candidate("good", score=0.9, scores={"m1": 0.9, "m2": 0.9}))
        pool.add(_make_candidate("bad", score=0.1, scores={"m1": 0.1, "m2": 0.1}))
        selected_ids = {pool.pareto_select().id for _ in range(50)}
        assert "good" in selected_ids


class TestCandidatePoolPruneDominated:
    """Test CandidatePool.prune_dominated()."""

    def test_prunes_to_max_size(self):
        pool = CandidatePool()
        for i in range(10):
            pool.add(_make_candidate(f"c{i}", score=float(i),
                                     scores={"m1": float(i)}))
        pool.prune_dominated(max_pool_size=3)
        assert len(pool) == 3

    def test_preserves_frontier_members(self):
        pool = CandidatePool()
        pool.add(_make_candidate("trade1", score=0.5, scores={"m1": 0.9, "m2": 0.1}))
        pool.add(_make_candidate("trade2", score=0.5, scores={"m1": 0.1, "m2": 0.9}))
        pool.add(_make_candidate("dom1", score=0.1, scores={"m1": 0.1, "m2": 0.1}))
        pool.add(_make_candidate("dom2", score=0.05, scores={"m1": 0.05, "m2": 0.05}))
        pool.prune_dominated(max_pool_size=3)
        ids = {c.id for c in pool.candidates}
        assert "trade1" in ids
        assert "trade2" in ids


class TestCandidatePoolGetBest:
    """Test CandidatePool.get_best()."""

    def test_empty_pool_raises(self):
        pool = CandidatePool()
        with pytest.raises(ValueError, match="empty pool"):
            pool.get_best()

    def test_returns_highest_score(self):
        pool = CandidatePool()
        pool.add(_make_candidate("c1", score=0.3))
        pool.add(_make_candidate("c2", score=0.9))
        pool.add(_make_candidate("c3", score=0.5))
        assert pool.get_best().id == "c2"

    def test_handles_none_scores(self):
        pool = CandidatePool()
        pool.add(_make_candidate("c1", score=None))
        pool.add(_make_candidate("c2", score=0.5))
        assert pool.get_best().id == "c2"
