"""Tests for the optimization module: metric, dataset, adapter, candidate pool."""

import dspy
import pytest

from app_operator_dspy.optimize.dataset import EVAL_APPS, TRAIN_APPS, make_examples
from app_operator_dspy.optimize.gepa.adapter import (
    get_instruction,
    make_initial_candidate,
    set_instruction,
    validate_candidate,
)
from app_operator_dspy.optimize.gepa.candidate import CandidatePool, PromptCandidate, _dominates
from app_operator_dspy.optimize.metric import deployment_metric


class TestDeploymentMetric:
    def test_failed_with_no_progress(self):
        pred = dspy.Prediction(success=False, phase="exception", attempts=0, error="")
        assert deployment_metric(dspy.Example(repo_path="/x"), pred) == 0.0

    def test_failed_with_partial_progress(self):
        error = "Container myapp-db-1 Started\nContainer myapp-web-1 Created"
        pred = dspy.Prediction(success=False, phase="deployment", attempts=3, error=error)
        score = deployment_metric(dspy.Example(repo_path="/x"), pred)
        assert 0.15 < score < 0.40  # partial credit for milestones

    def test_perfect_scores_high(self):
        pred = dspy.Prediction(
            success=True,
            phase="monitoring",
            attempts=1,
            statuses=["healthy", "healthy"],
            time_seconds=60.0,
            total_tokens=100_000,
        )
        assert deployment_metric(dspy.Example(repo_path="/x"), pred) > 0.9

    def test_success_last_attempt_scores_base(self):
        pred = dspy.Prediction(
            success=True,
            phase="monitoring",
            attempts=5,
            statuses=["unhealthy"],
            time_seconds=1800.0,
            total_tokens=1_500_000,
        )
        score = deployment_metric(dspy.Example(repo_path="/x"), pred)
        assert 0.49 <= score <= 0.55


class TestDataset:
    def test_train_eval_no_overlap(self):
        assert set(TRAIN_APPS).isdisjoint(set(EVAL_APPS))

    def test_make_examples_filters_invalid(self):
        assert make_examples(["/nonexistent"]) == []

    def test_make_examples_with_real_dir(self, tmp_path):
        (tmp_path / "app").mkdir()
        assert len(make_examples([str(tmp_path / "app")])) == 1


class TestCandidatePool:
    def test_get_best(self):
        pool = CandidatePool()
        c1 = PromptCandidate(signature_name="T", instruction_text="a")
        c1.overall_score = 0.5
        c1.scores = {"m": 0.5}
        c2 = PromptCandidate(signature_name="T", instruction_text="b")
        c2.overall_score = 0.8
        c2.scores = {"m": 0.8}
        pool.add(c1)
        pool.add(c2)
        assert pool.get_best() is c2

    def test_dominates(self):
        assert _dominates({"a": 0.9, "b": 0.8}, {"a": 0.5, "b": 0.5})
        assert not _dominates({"a": 0.9, "b": 0.3}, {"a": 0.5, "b": 0.8})


class TestAdapter:
    def test_roundtrip_instruction(self):
        original = get_instruction("GenerateDeployScript")
        set_instruction("GenerateDeployScript", "Test")
        assert get_instruction("GenerateDeployScript") == "Test"
        set_instruction("GenerateDeployScript", original)

    def test_unknown_raises(self):
        with pytest.raises(ValueError, match="Unknown signature"):
            get_instruction("Fake")

    def test_validate_rejects_empty(self):
        assert not validate_candidate(PromptCandidate(signature_name="T", instruction_text=""))

    def test_make_initial(self):
        c = make_initial_candidate("RepairDeploymentError")
        assert c.signature_name == "RepairDeploymentError"
        assert len(c.instruction_text) > 0
