"""Tests for PromptOptimizer."""

import json
import pytest
from unittest.mock import Mock, patch

from app_operator.dspy_integration.optimizer import PromptOptimizer
from app_operator.dspy_integration.config import DSPyConfig, DSPyOptimizationConfig
from app_operator.dspy_integration.data_loader import TrajectoryExample


@pytest.fixture
def dspy_config():
    """Create test DSPy configuration."""
    return DSPyConfig(
        optimization=DSPyOptimizationConfig(
            optimizer="BootstrapFewShot",
            teacher_model="claude-sonnet-4-5",
            num_examples=10,
            validation_split=0.2,
        )
    )


@pytest.fixture
def sample_examples():
    """Create sample trajectory examples."""
    return [
        TrajectoryExample(
            trajectory_file="/test/traj1.json",
            run_id="run1",
            phase="deployment",
            call_id=1,
            prompt="Deploy app",
            response="Deployed",
            success=True,
            iterations=1,
            tool_calls=[],
            duration_seconds=120.0,
            token_usage={"input": 100, "output": 50, "total": 150},
        ),
        TrajectoryExample(
            trajectory_file="/test/traj2.json",
            run_id="run2",
            phase="deployment",
            call_id=2,
            prompt="Deploy app",
            response="Failed",
            success=False,
            iterations=3,
            tool_calls=[],
            duration_seconds=300.0,
            token_usage={"input": 200, "output": 100, "total": 300},
        ),
    ]


@pytest.fixture
def trajectories_dir(tmp_path, sample_examples):
    """Create temp directory with sample trajectories."""
    traj_dir = tmp_path / "trajectories"
    traj_dir.mkdir()

    # Create a sample trajectory file
    trajectory = {
        "metadata": {"status": "completed", "run_id": "test"},
        "calls": [],
        "deployment": [],
        "monitoring": [],
        "script_generation": [],
    }

    traj_file = traj_dir / "trajectory_test.json"
    with open(traj_file, "w") as f:
        json.dump(trajectory, f)

    return traj_dir


class TestPromptOptimizer:
    """Tests for PromptOptimizer class."""

    def test_init(self, dspy_config, tmp_path):
        """Test initialization."""
        optimizer = PromptOptimizer(dspy_config, tmp_path)
        assert optimizer.config == dspy_config
        assert optimizer.prompts_dir == tmp_path
        assert optimizer.optimized_dir == tmp_path / "optimized"

    def test_optimize_invalid_prompt_names(self, dspy_config, tmp_path, trajectories_dir):
        """Test optimization with invalid prompt names raises ValueError."""
        optimizer = PromptOptimizer(dspy_config, tmp_path)

        with pytest.raises(ValueError, match="Invalid prompt names"):
            optimizer.optimize(
                prompt_names=["invalid_prompt", "another_invalid"],
                trajectories_dir=trajectories_dir,
            )

    def test_optimize_no_training_data(self, dspy_config, tmp_path):
        """Test optimization with no training data raises RuntimeError."""
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()

        optimizer = PromptOptimizer(dspy_config, empty_dir)

        with pytest.raises(RuntimeError, match="No training examples found"):
            optimizer.optimize(
                prompt_names=["deployer_fix_error"],
                trajectories_dir=empty_dir,
            )

    @patch("app_operator.dspy_integration.optimizer.TrajectoryDataLoader")
    def test_optimize_dry_run(self, mock_loader, dspy_config, tmp_path, sample_examples):
        """Test dry run mode."""
        # Mock data loader
        mock_instance = Mock()
        mock_instance.load_examples.return_value = sample_examples
        mock_loader.return_value = mock_instance

        optimizer = PromptOptimizer(dspy_config, tmp_path)

        result = optimizer.optimize(
            prompt_names=["deployer_fix_error"],
            trajectories_dir=tmp_path,
            dry_run=True,
        )

        assert result["dry_run"] is True
        assert result["prompt_names"] == ["deployer_fix_error"]
        assert result["train_examples"] == 1  # 80% of 2 examples
        assert result["val_examples"] == 1    # 20% of 2 examples
        assert "config" in result

    def test_get_next_version_no_existing(self, dspy_config, tmp_path):
        """Test version numbering with no existing versions."""
        optimizer = PromptOptimizer(dspy_config, tmp_path)
        version = optimizer._get_next_version()
        assert version == 1

    def test_get_next_version_with_existing(self, dspy_config, tmp_path):
        """Test version numbering with existing versions."""
        optimizer = PromptOptimizer(dspy_config, tmp_path)

        # Create some existing version directories
        optimizer.optimized_dir.mkdir(parents=True)
        (optimizer.optimized_dir / "v1").mkdir()
        (optimizer.optimized_dir / "v2").mkdir()
        (optimizer.optimized_dir / "v5").mkdir()

        version = optimizer._get_next_version()
        assert version == 6

    def test_get_next_version_with_non_version_dirs(self, dspy_config, tmp_path):
        """Test version numbering ignores non-version directories."""
        optimizer = PromptOptimizer(dspy_config, tmp_path)

        optimizer.optimized_dir.mkdir(parents=True)
        (optimizer.optimized_dir / "v1").mkdir()
        (optimizer.optimized_dir / "latest").mkdir()
        (optimizer.optimized_dir / "backup").mkdir()

        version = optimizer._get_next_version()
        assert version == 2

    def test_save_optimized_prompts(self, dspy_config, tmp_path):
        """Test saving optimized prompts creates correct structure."""
        optimizer = PromptOptimizer(dspy_config, tmp_path)

        results = {
            "deployer_fix_error": {
                "success": True,
                "validation_score": 0.85,
            },
            "deployer_summarize": {
                "success": False,
                "error": "Optimization failed",
            },
        }

        output_dir = tmp_path / "optimized" / "v1"
        optimizer._save_optimized_prompts(results, output_dir)

        # Check directory was created
        assert output_dir.exists()

        # Check metadata file
        metadata_file = output_dir / "metadata.json"
        assert metadata_file.exists()

        with open(metadata_file) as f:
            metadata = json.load(f)

        assert metadata["version"] == "v1"
        assert "deployer_fix_error" in metadata["prompts"]
        assert metadata["prompts"]["deployer_fix_error"]["optimized"] is True
        assert metadata["prompts"]["deployer_summarize"]["optimized"] is False

        # Check prompt file was created for successful optimization
        prompt_file = output_dir / "deployer_fix_error.dspy.json"
        assert prompt_file.exists()

        # Failed prompt should not have a file
        failed_file = output_dir / "deployer_summarize.dspy.json"
        assert not failed_file.exists()

    def test_create_optimizer_bootstrap(self, dspy_config, tmp_path):
        """Test creating BootstrapFewShot optimizer."""
        from app_operator.dspy_integration.metrics import CompositeMetric
        import dspy

        optimizer = PromptOptimizer(dspy_config, tmp_path)
        metric = CompositeMetric()

        dspy_optimizer = optimizer._create_optimizer(metric)
        assert isinstance(dspy_optimizer, dspy.BootstrapFewShot)

    def test_create_optimizer_invalid(self, dspy_config, tmp_path):
        """Test creating invalid optimizer raises ValueError."""
        from app_operator.dspy_integration.metrics import CompositeMetric

        dspy_config.optimization.optimizer = "InvalidOptimizer"
        optimizer = PromptOptimizer(dspy_config, tmp_path)
        metric = CompositeMetric()

        with pytest.raises(ValueError, match="Unknown optimizer"):
            optimizer._create_optimizer(metric)

    def test_evaluate(self, dspy_config, tmp_path, sample_examples):
        """Test evaluation of module on examples."""
        from app_operator.dspy_integration.metrics import CompositeMetric

        optimizer = PromptOptimizer(dspy_config, tmp_path)
        metric = CompositeMetric()

        # Create mock module
        mock_module = Mock()

        score = optimizer._evaluate(mock_module, sample_examples, metric)

        # Should return average of metric scores
        assert 0.0 <= score <= 1.0
