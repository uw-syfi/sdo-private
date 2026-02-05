"""Tests for PromptLoader with DSPy integration."""

from unittest.mock import Mock, patch

from app_operator.prompts import PromptLoader, reset_loader, get_loader
from app_operator.dspy_integration.config import DSPyConfig


class TestPromptLoaderDSPyInit:
    """Tests for PromptLoader initialization with DSPy config."""

    def setup_method(self):
        """Reset loader before each test."""
        reset_loader()

    def test_init_without_dspy_config(self):
        """Should initialize without DSPy config."""
        loader = PromptLoader()
        assert loader.dspy_config is None

    def test_init_with_dspy_config(self):
        """Should store DSPy config."""
        config = DSPyConfig(use_optimized=True, optimized_version="v1")
        loader = PromptLoader(dspy_config=config)
        assert loader.dspy_config is config


class TestTemplateToPromptName:
    """Tests for template name to prompt name conversion."""

    def setup_method(self):
        """Reset loader before each test."""
        reset_loader()

    def test_convert_deployer_template(self):
        """Should convert deployer template paths."""
        loader = PromptLoader()
        assert loader._template_to_prompt_name('deployer/system.jinja2') == 'deployer_system'
        assert loader._template_to_prompt_name('deployer/fix_error.jinja2') == 'deployer_fix_error'

    def test_convert_monitor_template(self):
        """Should convert monitor template paths."""
        loader = PromptLoader()
        assert loader._template_to_prompt_name(
            'monitor/analyze_health.jinja2') == 'monitor_analyze_health'

    def test_convert_agentflow_template(self):
        """Should convert agentflow template paths."""
        loader = PromptLoader()
        assert loader._template_to_prompt_name('agentflow/system.jinja2') == 'agentflow_system'


class TestShouldUseDSPy:
    """Tests for DSPy usage decision."""

    def setup_method(self):
        """Reset loader before each test."""
        reset_loader()

    def test_no_config_returns_false(self):
        """Without config, should return False."""
        loader = PromptLoader()
        result = loader._should_use_dspy('deployer_fix_error', {})
        assert result is False

    def test_use_optimized_false_returns_false(self):
        """With use_optimized=False, should return False."""
        config = DSPyConfig(use_optimized=False)
        loader = PromptLoader(dspy_config=config)
        result = loader._should_use_dspy('deployer_fix_error', {})
        assert result is False

    def test_use_optimized_true_without_canary(self):
        """With use_optimized=True and no canary, should return True."""
        config = DSPyConfig(use_optimized=True, canary_deployment=False)
        loader = PromptLoader(dspy_config=config)
        result = loader._should_use_dspy('deployer_fix_error', {})
        assert result is True

    def test_canary_deployment_deterministic_routing(self):
        """Canary deployment should route deterministically based on repo_path."""
        config = DSPyConfig(
            use_optimized=True,
            canary_deployment=True,
            canary_percentage=0.5
        )
        loader = PromptLoader(dspy_config=config)

        # Same repo_path should always give same result
        kwargs1 = {'repo_path': '/repo/test1'}
        result1a = loader._should_use_dspy('deployer_fix_error', kwargs1)
        result1b = loader._should_use_dspy('deployer_fix_error', kwargs1)
        assert result1a == result1b

        # Different repo_paths should give different results (with high probability)
        results = []
        for i in range(100):
            kwargs = {'repo_path': f'/repo/test{i}'}
            result = loader._should_use_dspy('deployer_fix_error', kwargs)
            results.append(result)

        # Should have roughly 50% True (within tolerance)
        true_count = sum(results)
        assert 35 <= true_count <= 65  # Allow ±15% variance

    def test_canary_without_repo_path_falls_back(self):
        """Canary without repo_path should fall back to Jinja2."""
        config = DSPyConfig(
            use_optimized=True,
            canary_deployment=True,
            canary_percentage=0.5
        )
        loader = PromptLoader(dspy_config=config)
        result = loader._should_use_dspy('deployer_fix_error', {})
        assert result is False


class TestRenderWithDSPy:
    """Tests for rendering with DSPy integration."""

    def setup_method(self):
        """Reset loader before each test."""
        reset_loader()

    def test_render_jinja2_fallback(self, tmp_path):
        """Should fall back to Jinja2 when DSPy disabled."""
        # Create a simple template
        templates_dir = tmp_path / "templates"
        templates_dir.mkdir()
        template_file = templates_dir / "test.jinja2"
        template_file.write_text("Hello {{ name }}!")

        loader = PromptLoader(templates_dir=templates_dir)
        result = loader.render('test.jinja2', name='World')

        assert result == "Hello World!"

    def test_render_dspy_not_found_falls_back(self, tmp_path):
        """Should fall back to Jinja2 if DSPy module not found."""
        # Create template
        templates_dir = tmp_path / "templates"
        templates_dir.mkdir()
        deployer_dir = templates_dir / "deployer"
        deployer_dir.mkdir()
        template_file = deployer_dir / "system.jinja2"
        template_file.write_text("System prompt for {{ repo_path }}")

        # Create optimized dir but no modules
        optimized_dir = tmp_path / "optimized"
        optimized_dir.mkdir()
        (optimized_dir / "v1").mkdir()

        config = DSPyConfig(use_optimized=True, optimized_version="v1")
        loader = PromptLoader(templates_dir=templates_dir, dspy_config=config)
        loader.optimized_dir = optimized_dir

        result = loader.render('deployer/system.jinja2', repo_path='/repo')

        # Should fall back to Jinja2
        assert result == "System prompt for /repo"


class TestGetLoaderWithDSPy:
    """Tests for get_loader with DSPy config."""

    def setup_method(self):
        """Reset loader before each test."""
        reset_loader()

    def test_get_loader_without_config(self):
        """Should create loader without config."""
        loader = get_loader()
        assert loader.dspy_config is None

    def test_get_loader_with_config(self):
        """Should create loader with config."""
        config = DSPyConfig(use_optimized=True, optimized_version="v1")
        loader = get_loader(dspy_config=config)
        assert loader.dspy_config == config

    def test_get_loader_resets_on_config_change(self):
        """Should reset loader if config changes."""
        config1 = DSPyConfig(use_optimized=True, optimized_version="v1")
        loader1 = get_loader(dspy_config=config1)

        config2 = DSPyConfig(use_optimized=True, optimized_version="v2")
        loader2 = get_loader(dspy_config=config2)

        # Should be different instances due to config change
        assert loader1 is not loader2
        assert loader2.dspy_config == config2

    def test_get_loader_same_config_returns_same_instance(self):
        """Should return same instance if config unchanged."""
        config = DSPyConfig(use_optimized=True, optimized_version="v1")
        loader1 = get_loader(dspy_config=config)
        loader2 = get_loader(dspy_config=config)

        assert loader1 is loader2


class TestTrajectoryIntegration:
    """Tests for trajectory recording integration."""

    def setup_method(self):
        """Reset loader before each test."""
        reset_loader()

    def test_record_prompt_version_jinja2(self, tmp_path):
        """Should record Jinja2 version in trajectory."""
        templates_dir = tmp_path / "templates"
        templates_dir.mkdir()
        template_file = templates_dir / "test.jinja2"
        template_file.write_text("Hello {{ name }}!")

        mock_recorder = Mock()
        loader = PromptLoader(templates_dir=templates_dir)

        loader.render('test.jinja2', name='World', _trajectory_recorder=mock_recorder)

        mock_recorder.set_prompt_version.assert_called_once_with('jinja2')

    def test_record_rendered_prompt_jinja2(self, tmp_path):
        """Should record the rendered prompt string in trajectory."""
        templates_dir = tmp_path / "templates"
        templates_dir.mkdir()
        template_file = templates_dir / "test.jinja2"
        template_file.write_text("Hello {{ name }}!")

        mock_recorder = Mock()
        loader = PromptLoader(templates_dir=templates_dir)

        loader.render('test.jinja2', name='World', _trajectory_recorder=mock_recorder)

        mock_recorder.record_rendered_prompt.assert_called_once_with("Hello World!")

    @patch('app_operator.dspy_integration.loader.load_optimized_module')
    def test_record_fallback(self, mock_load, tmp_path):
        """Should record fallback when DSPy module exists but invocation fails."""
        templates_dir = tmp_path / "templates"
        templates_dir.mkdir()
        deployer_dir = templates_dir / "deployer"
        deployer_dir.mkdir()
        template_file = deployer_dir / "system.jinja2"
        template_file.write_text("System {{ repo_path }}")

        # Set up optimized dir with the module file so _optimized_module_exists
        # returns True; patch load_optimized_module to return None to trigger
        # the fallback path inside _render_dspy.
        optimized_dir = tmp_path / "optimized"
        v1_dir = optimized_dir / "v1"
        v1_dir.mkdir(parents=True)
        (v1_dir / "deployer_system.dspy.json").write_text("{}")

        mock_load.return_value = None  # Simulate load failure

        config = DSPyConfig(use_optimized=True, optimized_version="v1")
        loader = PromptLoader(templates_dir=templates_dir, dspy_config=config)
        loader.optimized_dir = optimized_dir

        mock_recorder = Mock()
        loader.render(
            'deployer/system.jinja2',
            repo_path='/repo',
            _trajectory_recorder=mock_recorder
        )

        # DSPy was attempted (module file existed) but load returned None →
        # fallback to Jinja2 and fallback event recorded
        mock_recorder.record_fallback.assert_called_once()
