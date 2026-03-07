"""Tests for DSPy optimizer save/load round-trip."""

import json
from unittest.mock import Mock

from app_operator.dspy_integration._loader import load_optimized_module, reset_cache
from app_operator.dspy_integration.config import DSPyConfig
from app_operator.dspy_integration.optimizer import PromptOptimizer


class TestOptimizerSaveModule:
    """Tests for saving DSPy modules."""

    def test_save_dspy_module_creates_file(self, tmp_path):
        """Should create a .dspy.json file."""
        config = DSPyConfig()
        optimizer = PromptOptimizer(config, tmp_path)

        # Create a mock module with demos
        mock_module = Mock()
        mock_module.predictor = Mock(spec=["demos"])
        mock_module.predictor.demos = [
            {"repo_path": "/repo1", "error_context": "Error 1", "fix_summary": "Fix 1"},
            {"repo_path": "/repo2", "error_context": "Error 2", "fix_summary": "Fix 2"},
        ]

        output_file = tmp_path / "deployer_fix_error.dspy.json"

        optimizer._save_dspy_module(mock_module, output_file, "deployer_fix_error")

        assert output_file.exists()

    def test_save_dspy_module_includes_demos(self, tmp_path):
        """Saved module should include demonstrations."""
        config = DSPyConfig()
        optimizer = PromptOptimizer(config, tmp_path)

        mock_module = Mock()
        mock_module.predictor = Mock(spec=["demos"])
        mock_module.predictor.demos = [
            {"repo_path": "/repo1", "fix_summary": "Fix 1"},
        ]

        output_file = tmp_path / "deployer_fix_error.dspy.json"

        optimizer._save_dspy_module(mock_module, output_file, "deployer_fix_error")

        # Read and verify
        with open(output_file) as f:
            data = json.load(f)

        assert "demos" in data
        assert len(data["demos"]) == 1
        assert data["demos"][0]["repo_path"] == "/repo1"

    def test_save_dspy_module_no_demos_raises(self, tmp_path):
        """Should raise RuntimeError when module has no demos and no optimized instruction."""
        import pytest

        config = DSPyConfig()
        optimizer = PromptOptimizer(config, tmp_path)

        mock_module = Mock()
        mock_module.predictor = Mock(spec=["demos"])
        mock_module.predictor.demos = []

        output_file = tmp_path / "deployer_fix_error.dspy.json"

        with pytest.raises(RuntimeError, match="produced no demos"):
            optimizer._save_dspy_module(mock_module, output_file, "deployer_fix_error")

    def test_save_dspy_module_includes_metadata(self, tmp_path):
        """Saved module should include metadata."""
        config = DSPyConfig()
        optimizer = PromptOptimizer(config, tmp_path)

        mock_module = Mock()
        mock_module.predictor = Mock(spec=["demos"])
        mock_module.predictor.demos = [{"input": "err", "output": "fix"}]

        output_file = tmp_path / "deployer_fix_error.dspy.json"

        optimizer._save_dspy_module(mock_module, output_file, "deployer_fix_error")

        with open(output_file) as f:
            data = json.load(f)

        assert data["prompt_name"] == "deployer_fix_error"
        assert data["signature"] == "DeployerFixErrorSignature"


class TestOptimizerSaveOptimizedPrompts:
    """Tests for _save_optimized_prompts method."""

    def test_save_optimized_prompts_creates_directory(self, tmp_path):
        """Should create output directory."""
        config = DSPyConfig()
        optimizer = PromptOptimizer(config, tmp_path)

        results = {
            "deployer_fix_error": {
                "success": True,
                "validation_score": 0.85,
                "optimized_module": Mock(predictor=Mock(spec=["demos"], demos=[{"input": "err", "output": "fix"}])),
            }
        }

        output_dir = tmp_path / "optimized" / "v1"

        optimizer._save_optimized_prompts(results, output_dir)

        assert output_dir.exists()

    def test_save_optimized_prompts_creates_module_file(self, tmp_path):
        """Should create .dspy.json file for each prompt."""
        config = DSPyConfig()
        optimizer = PromptOptimizer(config, tmp_path)

        mock_module = Mock()
        mock_module.predictor = Mock(spec=["demos"])
        mock_module.predictor.demos = [{"input": "err", "output": "fix"}]

        results = {
            "deployer_fix_error": {
                "success": True,
                "validation_score": 0.85,
                "optimized_module": mock_module,
            }
        }

        output_dir = tmp_path / "optimized" / "v1"

        optimizer._save_optimized_prompts(results, output_dir)

        module_file = output_dir / "deployer_fix_error.dspy.json"
        assert module_file.exists()

    def test_save_optimized_prompts_creates_metadata(self, tmp_path):
        """Should create metadata.json."""
        config = DSPyConfig()
        optimizer = PromptOptimizer(config, tmp_path)

        mock_module = Mock()
        mock_module.predictor = Mock(spec=["demos"])
        mock_module.predictor.demos = [{"input": "err", "output": "fix"}]

        results = {
            "deployer_fix_error": {
                "success": True,
                "validation_score": 0.85,
                "optimized_module": mock_module,
            }
        }

        output_dir = tmp_path / "optimized" / "v1"

        optimizer._save_optimized_prompts(results, output_dir)

        metadata_file = output_dir / "metadata.json"
        assert metadata_file.exists()

        with open(metadata_file) as f:
            metadata = json.load(f)

        assert metadata["version"] == "v1"
        assert "deployer_fix_error" in metadata["prompts"]
        assert metadata["prompts"]["deployer_fix_error"]["optimized"] is True
        assert metadata["prompts"]["deployer_fix_error"]["module_file"] == "deployer_fix_error.dspy.json"

    def test_save_optimized_prompts_handles_failures(self, tmp_path):
        """Should record failures in metadata."""
        config = DSPyConfig()
        optimizer = PromptOptimizer(config, tmp_path)

        results = {
            "deployer_fix_error": {
                "success": False,
                "error": "Optimization failed",
            }
        }

        output_dir = tmp_path / "optimized" / "v1"

        optimizer._save_optimized_prompts(results, output_dir)

        metadata_file = output_dir / "metadata.json"
        with open(metadata_file) as f:
            metadata = json.load(f)

        assert metadata["prompts"]["deployer_fix_error"]["optimized"] is False
        assert "error" in metadata["prompts"]["deployer_fix_error"]


class TestRoundTripSaveLoad:
    """Tests for save → load round-trip."""

    def setup_method(self):
        """Reset cache before each test."""
        reset_cache()

    def test_save_and_load_module(self, tmp_path):
        """Should be able to save and load a module."""
        config = DSPyConfig()
        optimizer = PromptOptimizer(config, tmp_path)

        # Create and save a module
        mock_module = Mock()
        mock_module.predictor = Mock(spec=["demos"])
        mock_module.predictor.demos = [
            {"repo_path": "/repo1", "error_context": "Error", "fix_summary": "Fixed"},
        ]

        output_dir = tmp_path / "optimized" / "v1"
        output_dir.mkdir(parents=True)
        output_file = output_dir / "deployer_fix_error.dspy.json"

        optimizer._save_dspy_module(mock_module, output_file, "deployer_fix_error")

        # Load the module
        loaded_module = load_optimized_module("deployer_fix_error", tmp_path / "optimized", "v1")

        assert loaded_module is not None
        assert hasattr(loaded_module, "demos")
        assert len(loaded_module.demos) == 1
        assert loaded_module.demos[0]["repo_path"] == "/repo1"

    def test_save_multiple_prompts_and_load(self, tmp_path):
        """Should save and load multiple prompts."""
        config = DSPyConfig()
        optimizer = PromptOptimizer(config, tmp_path)

        mock_module1 = Mock()
        mock_module1.predictor = Mock(spec=["demos"])
        mock_module1.predictor.demos = [{"data": "prompt1"}]

        mock_module2 = Mock()
        mock_module2.predictor = Mock(spec=["demos"])
        mock_module2.predictor.demos = [{"data": "prompt2"}]

        results = {
            "deployer_fix_error": {
                "success": True,
                "validation_score": 0.8,
                "optimized_module": mock_module1,
            },
            "monitor_analyze_health": {
                "success": True,
                "validation_score": 0.9,
                "optimized_module": mock_module2,
            },
        }

        output_dir = tmp_path / "optimized" / "v1"
        optimizer._save_optimized_prompts(results, output_dir)

        # Load both modules
        module1 = load_optimized_module("deployer_fix_error", tmp_path / "optimized", "v1")
        module2 = load_optimized_module("monitor_analyze_health", tmp_path / "optimized", "v1")

        assert module1 is not None
        assert module2 is not None

        # Check that demos were loaded
        assert len(module1.demos) == 1
        assert len(module2.demos) == 1

        # Demos should preserve the data
        if isinstance(module1.demos[0], dict):
            assert module1.demos[0]["data"] == "prompt1"

        if isinstance(module2.demos[0], dict):
            assert module2.demos[0]["data"] == "prompt2"

    def test_load_latest_version(self, tmp_path):
        """Should load 'latest' version correctly."""
        config = DSPyConfig()
        optimizer = PromptOptimizer(config, tmp_path)

        # Create v1
        mock_module = Mock()
        mock_module.predictor = Mock(spec=["demos"])
        mock_module.predictor.demos = [{"version": "v1"}]

        output_dir_v1 = tmp_path / "optimized" / "v1"
        output_dir_v1.mkdir(parents=True)
        output_file = output_dir_v1 / "deployer_fix_error.dspy.json"
        optimizer._save_dspy_module(mock_module, output_file, "deployer_fix_error")

        # Create v2
        mock_module2 = Mock()
        mock_module2.predictor = Mock(spec=["demos"])
        mock_module2.predictor.demos = [{"version": "v2"}]

        output_dir_v2 = tmp_path / "optimized" / "v2"
        output_dir_v2.mkdir(parents=True)
        output_file2 = output_dir_v2 / "deployer_fix_error.dspy.json"
        optimizer._save_dspy_module(mock_module2, output_file2, "deployer_fix_error")

        # Load latest (should be v2)
        loaded = load_optimized_module("deployer_fix_error", tmp_path / "optimized", "latest")

        assert loaded is not None
        assert loaded.demos[0]["version"] == "v2"
