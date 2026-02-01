from jinja2 import Environment, FileSystemLoader, StrictUndefined
from pathlib import Path
from typing import Any, Optional, TYPE_CHECKING
import hashlib
import logging

if TYPE_CHECKING:
    from app_operator.dspy_integration.config import DSPyConfig

logger = logging.getLogger(__name__)


class PromptLoader:
    """Helper class to load and render Jinja2 templates or DSPy modules for prompts."""

    def __init__(
        self,
        templates_dir: str | Path = None,
        dspy_config: Optional['DSPyConfig'] = None
    ):
        """Initialize the loader.

        Args:
            templates_dir: Path to the directory containing templates.
                           Defaults to the 'templates' directory in this package.
            dspy_config: Optional DSPy configuration for optimized prompts.
        """
        if templates_dir is None:
            # Assumes this file is in app_operator/prompts/__init__.py
            # and templates/ is in app_operator/prompts/templates
            self.templates_dir = Path(__file__).resolve().parent / "templates"
        else:
            self.templates_dir = Path(templates_dir)

        if not self.templates_dir.exists():
            raise FileNotFoundError(
                f"Prompts directory not found: {self.templates_dir}"
            )

        self.env = Environment(
            loader=FileSystemLoader(str(self.templates_dir)),
            undefined=StrictUndefined,  # Raise error on missing variables
            trim_blocks=True,
            lstrip_blocks=True,
            keep_trailing_newline=True,
        )

        # DSPy configuration
        self.dspy_config = dspy_config
        self.optimized_dir = Path(__file__).resolve().parent / "optimized"
        self._dspy_configured = False  # Track if DSPy LM has been configured

    def _configure_dspy_runtime(self):
        """Configure DSPy with runtime LM (called once on first use)."""
        if self._dspy_configured or not self.dspy_config:
            return

        if not self.dspy_config.runtime_model:
            logger.warning("DSPy enabled but runtime_model not configured")
            return

        try:
            import dspy

            # Configure DSPy with the runtime model
            lm = dspy.LM(model=self.dspy_config.runtime_model)
            dspy.settings.configure(lm=lm)
            self._dspy_configured = True
            logger.info(f"Configured DSPy runtime with model: {self.dspy_config.runtime_model}")

        except Exception as e:
            logger.error(f"Failed to configure DSPy runtime: {e}")
            # Don't raise - will fallback to Jinja2

    def render(self, template_name: str, **kwargs: Any) -> str:
        """Render a template with the given context.

        Automatically routes to DSPy or Jinja2 based on configuration.

        Args:
            template_name: Name of the template file (relative to prompts dir).
            **kwargs: Variables to pass to the template.

        Returns:
            The rendered string.
        """
        # Convert template name to prompt name
        prompt_name = self._template_to_prompt_name(template_name)

        # Check if we should use DSPy
        if self._should_use_dspy(prompt_name, kwargs):
            try:
                result = self._render_dspy(prompt_name, kwargs)
                logger.info(f"Successfully rendered {prompt_name} using DSPy")
                return result
            except Exception as e:
                logger.warning(
                    f"DSPy rendering failed for {prompt_name}: {e}. "
                    f"Falling back to Jinja2."
                )
                # Record fallback in trajectory if available
                self._record_fallback(kwargs)

        # Fall back to Jinja2 (or default path)
        return self._render_jinja2(template_name, kwargs)

    def _template_to_prompt_name(self, template_name: str) -> str:
        """Convert template file name to prompt name.

        Args:
            template_name: Template file path (e.g., 'deployer/fix_error.jinja2')

        Returns:
            Prompt name (e.g., 'deployer_fix_error')

        Examples:
            >>> _template_to_prompt_name('deployer/fix_error.jinja2')
            'deployer_fix_error'
            >>> _template_to_prompt_name('monitor/analyze_health.jinja2')
            'monitor_analyze_health'
        """
        # Remove .jinja2 extension and convert path separators to underscores
        name = template_name.replace('.jinja2', '').replace('/', '_')
        return name

    def _should_use_dspy(self, prompt_name: str, kwargs: dict) -> bool:
        """Determine if DSPy should be used for this prompt.

        Handles canary deployment routing based on deterministic hashing.

        Args:
            prompt_name: Name of the prompt
            kwargs: Template context

        Returns:
            True if DSPy should be used, False otherwise
        """
        if self.dspy_config is None:
            return False

        if not self.dspy_config.use_optimized:
            return False

        # Canary deployment - deterministic routing based on repo_path
        if self.dspy_config.canary_deployment:
            # Use repo_path for deterministic routing
            repo_path = kwargs.get('repo_path', '')
            if repo_path:
                try:
                    # Hash repo_path to get deterministic percentage
                    hash_val = int(hashlib.md5(str(repo_path).encode()).hexdigest(), 16)
                    percentage = (hash_val % 100) / 100.0
                    canary_pct = float(self.dspy_config.canary_percentage)
                    use_dspy = percentage < canary_pct
                    logger.debug(
                        f"Canary routing for {repo_path}: "
                        f"hash={percentage:.2f}, threshold={canary_pct:.2f}, "
                        f"use_dspy={use_dspy}"
                    )
                    return use_dspy
                except (TypeError, ValueError, AttributeError):
                    # If canary_percentage is not numeric (e.g., in tests with mocks)
                    logger.warning(
                        "Canary deployment enabled but canary_percentage is invalid. "
                        "Falling back to Jinja2."
                    )
                    return False
            else:
                logger.warning(
                    "Canary deployment enabled but repo_path not in context. "
                    "Falling back to Jinja2."
                )
                return False

        # Normal mode - use DSPy for all prompts
        return True

    def _render_dspy(self, prompt_name: str, kwargs: dict) -> str:
        """Render using DSPy optimized module.

        Args:
            prompt_name: Name of the prompt
            kwargs: Template context

        Returns:
            Rendered prompt string

        Raises:
            Exception: If DSPy rendering fails
        """
        from app_operator.dspy_integration.loader import load_optimized_module
        from app_operator.dspy_integration.field_mappings import (
            map_kwargs_to_fields,
            get_output_field_name
        )

        # Configure DSPy runtime LM (once on first use)
        self._configure_dspy_runtime()

        # Load the optimized module
        module = load_optimized_module(
            prompt_name,
            self.optimized_dir,
            self.dspy_config.optimized_version
        )

        if module is None:
            raise RuntimeError(f"Failed to load DSPy module for {prompt_name}")

        # Map kwargs to DSPy fields
        fields = map_kwargs_to_fields(prompt_name, kwargs)

        # Invoke the module
        logger.debug(f"Invoking DSPy module for {prompt_name} with fields: {list(fields.keys())}")
        result = module(**fields)

        # Extract the primary output field
        output_field = get_output_field_name(prompt_name)
        if not hasattr(result, output_field):
            raise RuntimeError(
                f"DSPy result missing expected field '{output_field}' for {prompt_name}"
            )

        output = getattr(result, output_field)

        # Record prompt version in trajectory if available
        self._record_prompt_version(kwargs, f"dspy_{self.dspy_config.optimized_version}")

        return output

    def _render_jinja2(self, template_name: str, kwargs: dict) -> str:
        """Render using Jinja2 template.

        Args:
            template_name: Template file name
            kwargs: Template context

        Returns:
            Rendered prompt string

        Raises:
            RuntimeError: If Jinja2 rendering fails
        """
        try:
            template = self.env.get_template(template_name)
            result = template.render(**kwargs)

            # Record prompt version in trajectory
            self._record_prompt_version(kwargs, "jinja2")

            return result
        except Exception as e:
            # Wrap Jinja2 errors for clearer debugging context
            raise RuntimeError(
                f"Failed to render template '{template_name}': {e}"
            ) from e

    def _record_prompt_version(self, kwargs: dict, version: str) -> None:
        """Record which prompt version was used in trajectory.

        Args:
            kwargs: Template context (may contain trajectory_recorder)
            version: Version identifier (e.g., 'jinja2', 'dspy_v1')
        """
        # Check if trajectory recorder is passed in kwargs
        recorder = kwargs.get('_trajectory_recorder')
        if recorder and hasattr(recorder, 'set_prompt_version'):
            recorder.set_prompt_version(version)

    def _record_fallback(self, kwargs: dict) -> None:
        """Record that a fallback to Jinja2 occurred.

        Args:
            kwargs: Template context (may contain trajectory_recorder)
        """
        recorder = kwargs.get('_trajectory_recorder')
        if recorder and hasattr(recorder, 'record_fallback'):
            recorder.record_fallback()


# Global instance for easy access
_loader = None


def get_loader(dspy_config: Optional['DSPyConfig'] = None) -> PromptLoader:
    """Get or create the global PromptLoader instance.

    Args:
        dspy_config: Optional DSPy configuration. If provided and differs from
                     current loader's config, resets the loader.

    Returns:
        PromptLoader instance
    """
    global _loader

    # Reset loader if config changed
    if _loader is not None and dspy_config is not None:
        if _loader.dspy_config != dspy_config:
            _loader = None

    if _loader is None:
        _loader = PromptLoader(dspy_config=dspy_config)

    return _loader


def reset_loader() -> None:
    """Reset the loader singleton (for testing).

    This function allows tests to reset the global loader instance,
    ensuring test isolation when different tests need different
    loader configurations.
    """
    global _loader
    _loader = None
