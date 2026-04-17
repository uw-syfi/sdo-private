"""Tests for GEPAOptimizer."""

import json
from pathlib import Path

import pytest

from app_operator.core import GEPAConfig
from app_operator.gepa.evaluator import (
    EfficiencyMetrics,
    EvaluationExample,
    EvaluationResult,
)
from app_operator.gepa.optimizer import GEPAOptimizer

# --- Test doubles ---


class StubEvaluator:
    """Test double that returns incrementing scores."""

    def __init__(self, base_score=0.5, improvement=0.02):
        self.base_score = base_score
        self.improvement = improvement
        self.call_count = 0

    def evaluate(
        self,
        prompt_text,
        template_name,
        examples,
        candidate_id="",
    ):
        self.call_count += 1
        score = min(self.base_score + (self.call_count * self.improvement), 1.0)
        return EvaluationResult(
            candidate_id=candidate_id,
            scores={"test": score},
            overall_score=score,
            traces=[],
            efficiency=EfficiencyMetrics(
                total_characters=1000,
                estimated_tokens=250,
                turn_count=2,
                tool_call_count=5,
                wall_clock_seconds=30.0,
            ),
        )


class StubReflector:
    """Test double that appends '[mutated]' to prompt text."""

    def __init__(self):
        self.mutate_count = 0
        self.crossover_count = 0

    def mutate(self, current_prompt, traces, template_name):
        self.mutate_count += 1
        return (
            current_prompt + "\n[mutated]",
            f"mutation rationale #{self.mutate_count}",
        )

    def crossover(self, prompt_a, prompt_b, traces_a, traces_b, template_name):
        self.crossover_count += 1
        return (
            prompt_a + "\n" + prompt_b,
            f"crossover rationale #{self.crossover_count}",
        )


class FailingReflector:
    """Test double that always raises ValueError."""

    def mutate(self, current_prompt, traces, template_name):
        raise ValueError("LLM failure")

    def crossover(self, prompt_a, prompt_b, traces_a, traces_b, template_name):
        raise ValueError("LLM failure")


class StubAdapter:
    """Test double for SDSPromptAdapter."""

    def __init__(self, initial_prompt="Deploy on {{ platform }}"):
        self.initial_prompt = initial_prompt
        self.written = {}

    def read_template(self, template_name):
        return self.initial_prompt

    def write_template(self, template_name, content):
        self.written[template_name] = content

    def validate_template(self, template_name, content):
        return "{{ platform }}" in content or "{% " in content

    def get_templates_for_agent(self, agent_type):
        if agent_type == "deployer":
            return ["deployer/system.jinja2"]
        return []

    def get_template_info(self, template_name):
        return {
            "agent_type": "deployer",
            "description": "test",
        }


class InvalidatingAdapter(StubAdapter):
    """Test double that always rejects mutations."""

    def validate_template(self, template_name, content):
        return False


# --- Fixtures ---


@pytest.fixture
def gepa_config(tmp_path):
    return GEPAConfig(
        max_steps=3,
        num_candidates=5,
        minibatch_size=1,
        validation_size=1,
        mutation_probability=1.0,
        patience=10,
        checkpoint_interval=5,
        output_dir=str(tmp_path / "gepa_output"),
    )


@pytest.fixture
def examples():
    return [
        EvaluationExample(
            repo_path=Path("/tmp/test"),
            expected_outcome={},
            agent_type="deployer",
            description="test repo",
        )
    ]


# --- Tests ---


class TestGEPAOptimizerOptimize:
    """Test the optimize() method."""

    def test_returns_complete_results(self, gepa_config, examples):
        """Results dict has all expected keys with valid values."""
        optimizer = GEPAOptimizer(
            config=gepa_config,
            adapter=StubAdapter(),  # type: ignore[arg-type]
            reflector=StubReflector(),  # type: ignore[arg-type]
            evaluator=StubEvaluator(),  # type: ignore[arg-type]
        )
        results = optimizer.optimize("deployer/system.jinja2", examples, examples)
        # Core result fields
        assert results["best_prompt"] is not None
        assert results["best_score"] > 0
        assert results["initial_score"] > 0
        assert results["improvement"] >= 0
        assert len(results["history"]) > 0
        assert "all_candidates" in results
        # Config and efficiency tracking
        assert results["config"]["max_steps"] == gepa_config.max_steps
        assert results["total_wall_clock_seconds"] >= 0
        assert "avg_estimated_tokens" in results["efficiency_summary"]
        # History entry structure
        for entry in results["history"]:
            assert "scores" in entry
            assert "step_duration_seconds" in entry
            assert entry["step_duration_seconds"] >= 0
        # Step entries have efficiency
        step_entries = [h for h in results["history"] if h["step"] > 0]
        for entry in step_entries:
            assert "efficiency" in entry

    def test_mutation_failure_continues(self, gepa_config, examples):
        optimizer = GEPAOptimizer(
            config=gepa_config,
            adapter=StubAdapter(),  # type: ignore[arg-type]
            reflector=FailingReflector(),  # type: ignore[arg-type]
            evaluator=StubEvaluator(),  # type: ignore[arg-type]
        )
        results = optimizer.optimize("deployer/system.jinja2", examples, examples)
        assert results["best_prompt"] is not None

    def test_invalid_mutation_skipped(self, gepa_config, examples):
        optimizer = GEPAOptimizer(
            config=gepa_config,
            adapter=InvalidatingAdapter(),  # type: ignore[arg-type]
            reflector=StubReflector(),  # type: ignore[arg-type]
            evaluator=StubEvaluator(),  # type: ignore[arg-type]
        )
        results = optimizer.optimize("deployer/system.jinja2", examples, examples)
        assert results["final_pool_size"] == 1


class TestGEPAOptimizerEarlyStopping:
    """Test early stopping behavior."""

    def test_early_stopping_triggers(self, tmp_path, examples):
        config = GEPAConfig(
            max_steps=20,
            num_candidates=5,
            minibatch_size=1,
            validation_size=1,
            mutation_probability=1.0,
            patience=2,
            output_dir=str(tmp_path / "gepa_output"),
        )
        evaluator = StubEvaluator(base_score=0.5, improvement=0.0)
        optimizer = GEPAOptimizer(
            config=config,
            adapter=StubAdapter(),  # type: ignore[arg-type]
            reflector=StubReflector(),  # type: ignore[arg-type]
            evaluator=evaluator,  # type: ignore[arg-type]
        )
        results = optimizer.optimize("deployer/system.jinja2", examples, examples)
        assert len(results["history"]) < 20
        log_content = (optimizer.output_dir / "optimization.log").read_text()
        assert "Early stopping" in log_content


class TestGEPAOptimizerResume:
    """Test resume from checkpoint."""

    def test_resume_from_checkpoint(self, tmp_path, examples):
        config = GEPAConfig(
            max_steps=6,
            num_candidates=5,
            minibatch_size=1,
            validation_size=1,
            mutation_probability=1.0,
            patience=100,
            checkpoint_interval=3,
            output_dir=str(tmp_path / "gepa_output"),
        )
        adapter = StubAdapter()
        optimizer = GEPAOptimizer(
            config=config,
            adapter=adapter,  # type: ignore[arg-type]
            reflector=StubReflector(),  # type: ignore[arg-type]
            evaluator=StubEvaluator(),  # type: ignore[arg-type]
        )
        optimizer.optimize("deployer/system.jinja2", examples, examples)

        checkpoint_files = list(optimizer.output_dir.glob("checkpoint_*.json"))
        assert len(checkpoint_files) >= 1

        resume_config = GEPAConfig(
            max_steps=10,
            num_candidates=5,
            minibatch_size=1,
            validation_size=1,
            mutation_probability=1.0,
            patience=100,
            checkpoint_interval=5,
            output_dir=str(tmp_path / "gepa_resume"),
        )
        resume_optimizer = GEPAOptimizer(
            config=resume_config,
            adapter=adapter,  # type: ignore[arg-type]
            reflector=StubReflector(),  # type: ignore[arg-type]
            evaluator=StubEvaluator(),  # type: ignore[arg-type]
        )
        results = resume_optimizer.resume(str(optimizer.output_dir), examples, examples)
        assert results["best_prompt"] is not None
        assert results["best_score"] > 0

    def test_resume_missing_checkpoint_raises(self, tmp_path, examples):
        config = GEPAConfig(
            max_steps=3,
            output_dir=str(tmp_path / "gepa_output"),
        )
        optimizer = GEPAOptimizer(
            config=config,
            adapter=StubAdapter(),  # type: ignore[arg-type]
            reflector=StubReflector(),  # type: ignore[arg-type]
            evaluator=StubEvaluator(),  # type: ignore[arg-type]
        )
        with pytest.raises(FileNotFoundError, match="No checkpoint"):
            optimizer.resume(str(tmp_path / "nonexistent"), examples, examples)

    def test_resume_empty_pool_raises(self, tmp_path, examples):
        checkpoint_dir = tmp_path / "empty_checkpoint"
        checkpoint_dir.mkdir()
        checkpoint = {
            "template_name": "deployer/system.jinja2",
            "step": 5,
            "pool": [],
            "history": [],
        }
        with open(checkpoint_dir / "checkpoint_deployer_system.jinja2.json", "w") as f:
            json.dump(checkpoint, f)

        config = GEPAConfig(
            max_steps=10,
            output_dir=str(tmp_path / "gepa_output"),
        )
        optimizer = GEPAOptimizer(
            config=config,
            adapter=StubAdapter(),  # type: ignore[arg-type]
            reflector=StubReflector(),  # type: ignore[arg-type]
            evaluator=StubEvaluator(),  # type: ignore[arg-type]
        )
        with pytest.raises(ValueError, match="no candidates"):
            optimizer.resume(str(checkpoint_dir), examples, examples)


class TestGEPAOptimizerSeed:
    """Test seed reproducibility."""

    def test_seed_produces_deterministic_minibatch(self, tmp_path):
        """Same seed should produce the same minibatch sampling order."""
        examples = [EvaluationExample(Path(f"/tmp/repo{i}"), {}, "deployer", f"repo{i}") for i in range(10)]

        batches = []
        for run in range(2):
            config = GEPAConfig(
                max_steps=1,
                num_candidates=5,
                minibatch_size=3,
                seed=42,
                patience=100,
                output_dir=str(tmp_path / f"seed_run_{run}"),
            )
            optimizer = GEPAOptimizer(
                config=config,
                adapter=StubAdapter(),  # type: ignore[arg-type]
                reflector=StubReflector(),  # type: ignore[arg-type]
                evaluator=StubEvaluator(),  # type: ignore[arg-type]
            )
            batch = optimizer._sample_minibatch(examples)
            batches.append([e.description for e in batch])

        assert batches[0] == batches[1]
