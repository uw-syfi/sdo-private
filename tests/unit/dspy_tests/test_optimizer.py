"""Tests for PromptOptimizer."""

import json
from unittest.mock import Mock, patch

import pytest

from app_operator.dspy_integration._data_loader import TrajectoryExample
from app_operator.dspy_integration.config import DSPyConfig, DSPyOptimizationConfig
from app_operator.dspy_integration.optimizer import PromptOptimizer


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
                trajectories_dirs=[trajectories_dir],
            )

    def test_optimize_no_training_data(self, dspy_config, tmp_path):
        """Test optimization with no training data raises RuntimeError."""
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()

        optimizer = PromptOptimizer(dspy_config, empty_dir)

        with pytest.raises(RuntimeError, match="No training examples found"):
            optimizer.optimize(
                prompt_names=["deployer_fix_error"],
                trajectories_dirs=[empty_dir],
            )

    @patch("app_operator.dspy_integration.optimizer.TrajectoryDataLoader")
    def test_optimize_single_example_per_phase_not_skipped(self, mock_loader, dspy_config, tmp_path):
        """A phase with exactly one example must not be skipped.

        Previously int(1 * 0.8) == 0 put the sole example in val,
        leaving train empty and triggering the skip path.
        """
        examples = [
            TrajectoryExample(
                trajectory_file="/t.json",
                run_id="r",
                phase="deployment",
                call_id=1,
                prompt="p",
                response="r",
                success=True,
                iterations=1,
                tool_calls=[],
                duration_seconds=1.0,
                prompt_kwargs={
                    "repo_path": "/repo",
                    "error_context": "err",
                    "attempt": 1,
                    "max_attempts": 20,
                    "deploy_script": "/repo/.sds/deploy.sh",
                    "health_check_script": "/repo/.sds/health_check.sh",
                },
            )
        ]
        mock_loader.return_value.load_examples.return_value = examples

        optimizer = PromptOptimizer(dspy_config, tmp_path)
        optimizer._configure_dspy_lm = Mock()
        optimizer._optimize_single_prompt = Mock(return_value={"success": True, "optimized_module": Mock()})
        optimizer._save_optimized_prompts = Mock()

        result = optimizer.optimize(
            prompt_names=["deployer_fix_error"],
            trajectories_dirs=[tmp_path],
        )

        # Was not skipped — _optimize_single_prompt was reached
        optimizer._optimize_single_prompt.assert_called_once()
        prompt_name, train, val, _ = optimizer._optimize_single_prompt.call_args[0]
        assert prompt_name == "deployer_fix_error"
        assert len(train) == 1  # the single example goes to train
        assert train[0].phase == "deployment"
        assert len(val) == 0  # nothing left for val
        assert result["success"] is True

    @patch("app_operator.dspy_integration.optimizer.dspy")
    @patch("app_operator.dspy_integration.optimizer.TrajectoryDataLoader")
    def test_optimize_all_prompts_failed_raises(self, mock_loader, mock_dspy, dspy_config, tmp_path):
        """optimize() raises RuntimeError and saves nothing when every prompt fails."""
        # Provide examples that will pass through to _optimize_single_prompt
        examples = [
            TrajectoryExample(
                trajectory_file="/t.json",
                run_id="r",
                phase="deployment",
                call_id=i,
                prompt="p",
                response="r",
                success=True,
                iterations=1,
                tool_calls=[],
                duration_seconds=1.0,
                prompt_kwargs={
                    "repo_path": "/repo",
                    "error_context": "err",
                    "attempt": 1,
                    "max_attempts": 20,
                    "deploy_script": "/repo/.sds/deploy.sh",
                    "health_check_script": "/repo/.sds/health_check.sh",
                },
            )
            for i in range(4)
        ]
        mock_loader.return_value.load_examples.return_value = examples

        # compile() returns a module but never calls the metric → 0 traces
        mock_optimizer_instance = Mock()
        mock_optimizer_instance.compile.return_value = Mock()
        mock_dspy.BootstrapFewShot.return_value = mock_optimizer_instance
        mock_dspy.Example = __import__("dspy").Example
        mock_dspy.Module = __import__("dspy").Module
        mock_dspy.Predict = __import__("dspy").Predict

        optimizer = PromptOptimizer(dspy_config, tmp_path)

        with pytest.raises(RuntimeError, match="All prompts failed optimization"):
            optimizer.optimize(
                prompt_names=["deployer_fix_error", "deployer_summarize"],
                trajectories_dirs=[tmp_path],
            )

        # No version directory should have been created
        optimized_dir = tmp_path / "optimized"
        assert not optimized_dir.exists()

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
            trajectories_dirs=[tmp_path],
            dry_run=True,
        )

        assert result["dry_run"] is True
        assert result["prompt_names"] == ["deployer_fix_error"]
        assert result["train_examples"] == 1  # 80% of 2 examples
        assert result["val_examples"] == 1  # 20% of 2 examples
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

        # Provide a mock module with at least one demo so _save_dspy_module succeeds
        mock_predictor = Mock()
        mock_predictor.demos = [{"input": "fix this", "output": "fixed"}]
        mock_predictor.signature = Mock()
        del mock_predictor.signature.instructions  # simulate BootstrapFewShot (no rewrite)
        mock_module = Mock()
        mock_module.predictor = mock_predictor

        results = {
            "deployer_fix_error": {
                "success": True,
                "validation_score": 0.85,
                "optimized_module": mock_module,
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

    def test_save_dspy_module_captures_optimized_instruction(self, dspy_config, tmp_path):
        """_save_dspy_module persists the instruction rewritten by COPRO."""
        optimizer = PromptOptimizer(dspy_config, tmp_path)

        # Simulate a COPRO-compiled predictor: no demos, but rewritten instruction
        mock_predictor = Mock()
        mock_predictor.demos = []
        mock_predictor.signature = Mock()
        mock_predictor.signature.instructions = "Detect the platform first, then fix."

        mock_module = Mock()
        mock_module.predictor = mock_predictor

        output_file = tmp_path / "test_copro.dspy.json"
        optimizer._save_dspy_module(mock_module, output_file, "deployer_fix_error")

        with open(output_file) as f:
            saved = json.load(f)

        assert saved["optimized_instruction"] == "Detect the platform first, then fix."
        assert saved["demos"] == []

    def test_create_optimizer_bootstrap(self, dspy_config, tmp_path):
        """Test creating BootstrapFewShot optimizer."""
        import dspy

        from app_operator.dspy_integration.metrics import CompositeMetric

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

    def test_evaluate(self, dspy_config, tmp_path):
        """Test evaluation invokes module and scores predictions."""
        from app_operator.dspy_integration.metrics import CompositeMetric

        optimizer = PromptOptimizer(dspy_config, tmp_path)
        metric = CompositeMetric()

        examples = [
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
                prompt_kwargs={
                    "repo_path": "/repo",
                    "error_context": "err",
                    "attempt": 1,
                    "max_attempts": 20,
                    "deploy_script": "/repo/.sds/deploy.sh",
                    "health_check_script": "/repo/.sds/health_check.sh",
                },
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
                prompt_kwargs={
                    "repo_path": "/repo",
                    "error_context": "timeout",
                    "attempt": 2,
                    "max_attempts": 20,
                    "deploy_script": "/repo/.sds/deploy.sh",
                    "health_check_script": "/repo/.sds/health_check.sh",
                },
            ),
        ]

        # Module must be callable with input kwargs and return a prediction
        mock_module = Mock(return_value="predicted output")

        score = optimizer._evaluate(mock_module, examples, metric, "deployer_fix_error")

        # Module was actually invoked for each example
        assert mock_module.call_count == len(examples)
        # Score is numeric (CompositeMetric returns > 0 for non-None predictions)
        assert score is not None
        assert 0.0 <= score <= 1.0

    def test_convert_to_dspy_examples_with_prompt_kwargs(self, dspy_config, tmp_path):
        """prompt_kwargs are mapped through field_mappings into dspy.Example."""

        optimizer = PromptOptimizer(dspy_config, tmp_path)
        examples = [
            TrajectoryExample(
                trajectory_file="/test/traj.json",
                run_id="run1",
                phase="deployment",
                call_id=1,
                prompt="Deploy",
                response="Fix applied",
                success=True,
                iterations=1,
                tool_calls=[],
                duration_seconds=10.0,
                prompt_kwargs={
                    "repo_path": "/my/repo",
                    "error_context": "Connection refused",
                    "attempt": 3,
                    "max_attempts": 20,
                    "deploy_script": "/my/repo/.sds/deploy.sh",
                    "health_check_script": "/my/repo/.sds/health_check.sh",
                    "previous_summary_note": "Port not open",
                },
                rendered_prompt="You are a DevOps agent. Fix the connection error.",
            )
        ]

        result = optimizer._convert_to_dspy_examples(examples, "deployer_fix_error")

        assert len(result) == 1
        ex = result[0]
        # error_context comes directly from prompt_kwargs (not from prompt[:500])
        assert ex.error_context == "Connection refused"
        # previous_summary_note is mapped to previous_summary via EXPLICIT_MAPPINGS
        assert ex.previous_summary == "Port not open"
        assert ex.repo_path == "/my/repo"
        # Output field is set from rendered_prompt (ground-truth instruction prompt)
        assert ex.rendered_prompt == "You are a DevOps agent. Fix the connection error."

    def test_convert_to_dspy_examples_without_prompt_kwargs_skips(self, dspy_config, tmp_path):
        """Examples without prompt_kwargs are skipped entirely."""
        optimizer = PromptOptimizer(dspy_config, tmp_path)
        examples = [
            TrajectoryExample(
                trajectory_file="/test/traj.json",
                run_id="run1",
                phase="deployment",
                call_id=1,
                prompt="Some legacy prompt text here",
                response="Legacy response",
                success=True,
                iterations=1,
                tool_calls=[],
                duration_seconds=10.0,
                prompt_kwargs=None,
            )
        ]

        result = optimizer._convert_to_dspy_examples(examples, "deployer_fix_error")

        assert len(result) == 0

    def test_convert_to_dspy_examples_mixed_kwargs_keeps_only_valid(self, dspy_config, tmp_path):
        """Only examples with prompt_kwargs produce dspy.Examples; others are skipped."""
        optimizer = PromptOptimizer(dspy_config, tmp_path)
        examples = [
            TrajectoryExample(
                trajectory_file="/test/traj.json",
                run_id="run1",
                phase="deployment",
                call_id=1,
                prompt="Legacy prompt",
                response="Legacy response",
                success=True,
                iterations=1,
                tool_calls=[],
                duration_seconds=10.0,
                prompt_kwargs=None,
            ),
            TrajectoryExample(
                trajectory_file="/test/traj.json",
                run_id="run2",
                phase="deployment",
                call_id=2,
                prompt="Deploy",
                response="Fix applied",
                success=True,
                iterations=1,
                tool_calls=[],
                duration_seconds=10.0,
                prompt_kwargs={
                    "repo_path": "/my/repo",
                    "error_context": "Timeout",
                    "attempt": 1,
                    "max_attempts": 20,
                    "deploy_script": "/my/repo/.sds/deploy.sh",
                    "health_check_script": "/my/repo/.sds/health_check.sh",
                },
            ),
        ]

        result = optimizer._convert_to_dspy_examples(examples, "deployer_fix_error")

        assert len(result) == 1
        assert result[0].repo_path == "/my/repo"
        assert result[0].error_context == "Timeout"

    def test_convert_to_dspy_examples_generic_prompt(self, dspy_config, tmp_path):
        """Generic prompt (monitor_analyze_health) populates fields correctly."""
        optimizer = PromptOptimizer(dspy_config, tmp_path)
        examples = [
            TrajectoryExample(
                trajectory_file="/test/traj.json",
                run_id="run1",
                phase="monitoring",
                call_id=1,
                prompt="Health check",
                response="All healthy",
                success=True,
                iterations=1,
                tool_calls=[],
                duration_seconds=5.0,
                prompt_kwargs={
                    "health_check_output": "HTTP 200 OK",
                    "exit_code": 0,
                    "iteration": 2,
                },
                rendered_prompt="Analyze the health check output and report status.",
            )
        ]

        result = optimizer._convert_to_dspy_examples(examples, "monitor_analyze_health")

        assert len(result) == 1
        ex = result[0]
        assert ex.health_check_output == "HTTP 200 OK"
        assert ex.exit_code == 0
        assert ex.iteration == 2
        # Output field populated from rendered_prompt
        assert ex.health_status == "Analyze the health check output and report status."

    @patch("app_operator.dspy_integration.optimizer.dspy")
    def test_compile_dispatch_miprv2(self, mock_dspy, dspy_config, tmp_path):
        """MIPROv2 compile is called with valset."""
        mock_optimizer_instance = Mock()
        mock_optimizer_instance.compile.return_value = Mock()
        mock_dspy.MIPROv2.return_value = mock_optimizer_instance
        mock_dspy.Example = __import__("dspy").Example
        mock_dspy.Module = __import__("dspy").Module
        mock_dspy.Predict = __import__("dspy").Predict

        dspy_config.optimization.optimizer = "MIPROv2"
        optimizer = PromptOptimizer(dspy_config, tmp_path)

        train = [
            TrajectoryExample(
                trajectory_file="/t.json",
                run_id="r",
                phase="deployment",
                call_id=1,
                prompt="p",
                response="r",
                success=True,
                iterations=1,
                tool_calls=[],
                duration_seconds=1.0,
                prompt_kwargs={"health_check_output": "ok", "exit_code": 0, "iteration": 1},
            )
        ]
        val = [
            TrajectoryExample(
                trajectory_file="/t.json",
                run_id="r",
                phase="deployment",
                call_id=2,
                prompt="p",
                response="r",
                success=True,
                iterations=1,
                tool_calls=[],
                duration_seconds=1.0,
                prompt_kwargs={"health_check_output": "ok", "exit_code": 0, "iteration": 1},
            )
        ]

        from app_operator.dspy_integration.metrics import CompositeMetric

        metric = CompositeMetric()

        optimizer._optimize_single_prompt("monitor_analyze_health", train, val, metric)

        # Verify compile was called with valset
        call_kwargs = mock_optimizer_instance.compile.call_args[1]
        assert "valset" in call_kwargs
        assert "trainset" in call_kwargs

    @patch("app_operator.dspy_integration.optimizer.dspy")
    def test_compile_dispatch_copro(self, mock_dspy, dspy_config, tmp_path):
        """COPRO compile is called with eval_kwargs."""
        mock_optimizer_instance = Mock()
        mock_optimizer_instance.compile.return_value = Mock()
        mock_dspy.COPRO.return_value = mock_optimizer_instance
        mock_dspy.Example = __import__("dspy").Example
        mock_dspy.Module = __import__("dspy").Module
        mock_dspy.Predict = __import__("dspy").Predict

        dspy_config.optimization.optimizer = "COPRO"
        optimizer = PromptOptimizer(dspy_config, tmp_path)

        train = [
            TrajectoryExample(
                trajectory_file="/t.json",
                run_id="r",
                phase="deployment",
                call_id=1,
                prompt="p",
                response="r",
                success=True,
                iterations=1,
                tool_calls=[],
                duration_seconds=1.0,
                prompt_kwargs={"health_check_output": "ok", "exit_code": 0, "iteration": 1},
            )
        ]
        val = [
            TrajectoryExample(
                trajectory_file="/t.json",
                run_id="r",
                phase="deployment",
                call_id=2,
                prompt="p",
                response="r",
                success=True,
                iterations=1,
                tool_calls=[],
                duration_seconds=1.0,
                prompt_kwargs={"health_check_output": "ok", "exit_code": 0, "iteration": 1},
            )
        ]

        from app_operator.dspy_integration.metrics import CompositeMetric

        metric = CompositeMetric()

        optimizer._optimize_single_prompt("monitor_analyze_health", train, val, metric)

        # Verify compile was called with eval_kwargs
        call_kwargs = mock_optimizer_instance.compile.call_args[1]
        assert "eval_kwargs" in call_kwargs
        assert call_kwargs["eval_kwargs"] == {"num_threads": 4}

    @patch("app_operator.dspy_integration.optimizer.dspy")
    def test_optimize_single_prompt_zero_traces_returns_failure(self, mock_dspy, dspy_config, tmp_path):
        """compile() that never invokes the metric is reported as failure.

        BootstrapFewShot populates demos from the training set even when the
        teacher LM fails on every example.  The reliable signal is whether the
        metric was ever called — if not, no real bootstrapping occurred.
        """
        mock_optimizer_instance = Mock()
        # compile returns a module but never calls the metric
        mock_optimizer_instance.compile.return_value = Mock()
        mock_dspy.BootstrapFewShot.return_value = mock_optimizer_instance
        mock_dspy.Example = __import__("dspy").Example
        mock_dspy.Module = __import__("dspy").Module
        mock_dspy.Predict = __import__("dspy").Predict

        optimizer = PromptOptimizer(dspy_config, tmp_path)

        train = [
            TrajectoryExample(
                trajectory_file="/t.json",
                run_id="r",
                phase="deployment",
                call_id=1,
                prompt="p",
                response="r",
                success=True,
                iterations=1,
                tool_calls=[],
                duration_seconds=1.0,
                prompt_kwargs={"health_check_output": "ok", "exit_code": 0, "iteration": 1},
            )
        ]

        from app_operator.dspy_integration.metrics import CompositeMetric

        metric = CompositeMetric()

        result = optimizer._optimize_single_prompt("monitor_analyze_health", train, [], metric)

        assert result["success"] is False
        assert "0 successful traces" in result["error"]

    @patch("app_operator.dspy_integration.optimizer.dspy")
    def test_optimize_single_prompt_with_traces_returns_success(self, mock_dspy, dspy_config, tmp_path):
        """compile() that invokes the metric at least once reports success."""
        mock_optimizer_instance = Mock()
        compiled_module = Mock()

        # Simulate compile calling the metric (teacher LM succeeded on one example)
        def fake_compile(*args, **kwargs):
            metric_fn = mock_dspy.BootstrapFewShot.call_args[1]["metric"]
            example = Mock(
                success=True,
                iterations=1,
                token_usage={"input": 10, "output": 5, "total": 15},
                health_check_script=None,
            )
            metric_fn(example, "prediction")  # one successful evaluation
            return compiled_module

        mock_optimizer_instance.compile.side_effect = fake_compile
        mock_dspy.BootstrapFewShot.return_value = mock_optimizer_instance
        mock_dspy.Example = __import__("dspy").Example
        mock_dspy.Module = __import__("dspy").Module
        mock_dspy.Predict = __import__("dspy").Predict

        optimizer = PromptOptimizer(dspy_config, tmp_path)

        train = [
            TrajectoryExample(
                trajectory_file="/t.json",
                run_id="r",
                phase="deployment",
                call_id=1,
                prompt="p",
                response="r",
                success=True,
                iterations=1,
                tool_calls=[],
                duration_seconds=1.0,
                prompt_kwargs={"health_check_output": "ok", "exit_code": 0, "iteration": 1},
            )
        ]

        from app_operator.dspy_integration.metrics import CompositeMetric

        metric = CompositeMetric()

        result = optimizer._optimize_single_prompt("monitor_analyze_health", train, [], metric)

        assert result["success"] is True
        assert result["optimized_module"] is compiled_module

    @patch("app_operator.dspy_integration.optimizer.dspy")
    def test_configure_dspy_lm_with_provider_prefix(self, mock_dspy, tmp_path):
        """Test LM configuration preserves provider/model format."""
        test_cases = [
            # input_model, expected_model_str
            ("vertex_ai/gemini-2.5-pro", "vertex_ai/gemini-2.5-pro"),
            ("anthropic/claude-sonnet-4-5", "anthropic/claude-sonnet-4-5"),
            ("openai/gpt-4", "openai/gpt-4"),
            ("gemini/gemini-pro", "gemini/gemini-pro"),
        ]

        for input_model, expected_model_str in test_cases:
            config = DSPyConfig(
                optimization=DSPyOptimizationConfig(
                    optimizer="BootstrapFewShot",
                    teacher_model=input_model,
                    num_examples=10,
                    validation_split=0.2,
                )
            )
            optimizer = PromptOptimizer(config, tmp_path)

            mock_dspy.LM.return_value = Mock()
            optimizer._configure_dspy_lm()

            # Verify model and cache kwargs — ignore vertex_location forwarded from env
            call_kwargs = mock_dspy.LM.call_args[1]
            assert call_kwargs.get("model") == expected_model_str
            assert call_kwargs.get("cache") is False

    @patch("app_operator.dspy_integration.optimizer.dspy")
    def test_configure_dspy_lm_without_provider_prefix(self, mock_dspy, tmp_path):
        """Test LM configuration adds provider prefix when missing."""
        test_cases = [
            # input_model, expected_model_str
            ("claude-sonnet-4-5", "anthropic/claude-sonnet-4-5"),
            ("gpt-4", "openai/gpt-4"),
            ("o1-preview", "openai/o1-preview"),
            ("gemini-pro", "gemini/gemini-pro"),
        ]

        for input_model, expected_model_str in test_cases:
            config = DSPyConfig(
                optimization=DSPyOptimizationConfig(
                    optimizer="BootstrapFewShot",
                    teacher_model=input_model,
                    num_examples=10,
                    validation_split=0.2,
                )
            )
            optimizer = PromptOptimizer(config, tmp_path)

            mock_dspy.LM.return_value = Mock()
            optimizer._configure_dspy_lm()

            # Verify model and cache kwargs — ignore vertex_location forwarded from env
            call_kwargs = mock_dspy.LM.call_args[1]
            assert call_kwargs.get("model") == expected_model_str
            assert call_kwargs.get("cache") is False
