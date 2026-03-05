from __future__ import annotations

import hashlib
import importlib
import logging
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from jinja2 import Environment, FileSystemLoader, StrictUndefined

if TYPE_CHECKING:
    from app_operator.trajectory import TrajectoryRecorderProtocol


@runtime_checkable
class DSPyConfigProtocol(Protocol):
    """Protocol describing the DSPy configuration attributes used by PromptLoader.

    This allows prompts to depend on an abstract interface rather than the
    concrete DSPyConfig class from dspy_integration, keeping the dependency
    arrow flowing dspy_integration -> prompts.
    """

    use_optimized: bool
    use_seeds: bool
    optimized_version: str
    runtime_model: str | None
    vertex_location: str | None
    canary_deployment: bool
    canary_percentage: float


logger = logging.getLogger(__name__)

# GEPA-style seed prompts: minimal starting points for optimization
# Prompts that produce Jinja2 instructions for agent.generate() to execute.
# They must never be routed through DSPy because:
#   - The DSPy module generates the final script content (output: deployment_script)
#     rather than an instruction, so it returns a bash script as "full_prompt"
#   - agent.generate() receives that bash script, _FILE_GEN_RE fails to match,
#     and the RLM loop runs on a bash script as its task → infinite explore loop
# These prompts rely on Jinja2 → _FILE_GEN_RE match → _generate_files() to work.
_AGENT_INSTRUCTION_PROMPTS: frozenset[str] = frozenset(
    {
        "deployer_generate_deploy_script",
        "deployer_generate_health_check",
        "deployer_generate_script",
    }
)

SEED_TEMPLATE_MAP = {
    "deployer_system": "seeds/deployer_system.jinja2",
    "deployer_fix_error": "seeds/deployer_fix_error.jinja2",
    "deployer_summarize": "seeds/deployer_summarize.jinja2",
    "deployer_generate_script": "seeds/deployer_generate_script.jinja2",
    "deployer_generate_deploy_script": "seeds/deployer_generate_deploy_script.jinja2",
    "deployer_generate_health_check": "seeds/deployer_generate_health_check.jinja2",
    "code_analyzer_system": "seeds/code_analyzer_system.jinja2",
    "code_analyzer_user": "seeds/code_analyzer_user.jinja2",
    "monitor_analyze_health": "seeds/monitor_analyze_health.jinja2",
    "agentflow_system": "seeds/agentflow_system.jinja2",
    "agentflow_user": "seeds/agentflow_user.jinja2",
    "agentflow_repair": "seeds/agentflow_repair.jinja2",
    "subagent_trajectory_analyst": "seeds/subagent_trajectory_analyst.jinja2",
    "subagent_error_log_analyst": "seeds/subagent_error_log_analyst.jinja2",
    "subagent_script_analyst": "seeds/subagent_script_analyst.jinja2",
    "subagent_repo_analyst": "seeds/subagent_repo_analyst.jinja2",
    "subagent_root_synthesis": "seeds/subagent_root_synthesis.jinja2",
    "rlm_deployer_fix_error": "seeds/rlm_deployer_fix_error.jinja2",
}


def _resolve_optimized_version(optimized_dir: Path, version: str) -> str | None:
    """Resolve version string to actual version directory.

    Handles the 'latest' special case by scanning for the highest-numbered
    version directory.  Pure filesystem logic — no dspy dependency.

    Args:
        optimized_dir: Base directory containing versioned subdirectories.
        version: Version string (e.g., 'v1', 'latest').

    Returns:
        Resolved version string (e.g., 'v3') or None if not found.
    """
    if not optimized_dir.exists():
        logger.warning(f"Optimized prompts directory not found: {optimized_dir}")
        return None

    if version == "latest":
        version_dirs = [
            d for d in optimized_dir.iterdir() if d.is_dir() and d.name.startswith("v") and d.name[1:].isdigit()
        ]
        if not version_dirs:
            logger.warning(f"No versioned directories found in {optimized_dir}")
            return None
        version_dirs.sort(key=lambda d: int(d.name[1:]))
        resolved = version_dirs[-1].name
        logger.debug(f"Resolved 'latest' to {resolved}")
        return resolved

    version_dir = optimized_dir / version
    if version_dir.exists():
        return version
    logger.warning(f"Version directory not found: {version_dir}")
    return None


class PromptLoader:
    """Helper class to load and render Jinja2 templates or DSPy modules for prompts."""

    def __init__(
        self,
        templates_dir: str | Path | None = None,
        dspy_config: DSPyConfigProtocol | None = None,
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
            raise FileNotFoundError(f"Prompts directory not found: {self.templates_dir}")

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
        self._module_exists_cache: dict[str, bool] = {}

    def _configure_dspy_runtime(self):
        """Configure DSPy with runtime LM (called once on first use)."""
        if self._dspy_configured or not self.dspy_config:
            return

        if not self.dspy_config.runtime_model:
            logger.warning("DSPy enabled but runtime_model not configured")
            return

        try:
            import os

            import dspy

            # Configure DSPy with the runtime model
            kwargs = {"model": self.dspy_config.runtime_model, "cache": False}
            vertex_location = self.dspy_config.vertex_location or os.environ.get("VERTEX_LOCATION")
            if vertex_location:
                kwargs["vertex_location"] = vertex_location
            lm = dspy.LM(**kwargs)
            dspy.settings.configure(lm=lm)
            self._dspy_configured = True
            logger.info(f"Configured DSPy runtime with model: {self.dspy_config.runtime_model}")

        except (ImportError, RuntimeError, ValueError) as e:
            logger.error(f"Failed to configure DSPy runtime: {e}")
            # Don't raise - will fallback to Jinja2

    def render(
        self,
        template_name: str,
        recorder: TrajectoryRecorderProtocol | None = None,
        **kwargs: Any,
    ) -> str:
        """Render a template with the given context.

        Automatically routes to DSPy or Jinja2 based on configuration.

        Args:
            template_name: Name of the template file (relative to prompts dir).
            recorder: Optional trajectory recorder for tracking prompt metadata.
            **kwargs: Variables to pass to the template.

        Returns:
            The rendered string.
        """
        # Convert template name to prompt name
        prompt_name = self._template_to_prompt_name(template_name)

        # Record structured kwargs in trajectory before routing
        self._notify_recorder(recorder, "record_prompt_kwargs", kwargs)

        # Check if we should use DSPy
        if self._should_use_dspy(prompt_name, kwargs):
            try:
                result = self._render_dspy(prompt_name, kwargs, recorder=recorder)
                logger.info(f"Successfully rendered {prompt_name} using DSPy")
                self._notify_recorder(recorder, "record_rendered_prompt", result)
                return result
            except (ImportError, RuntimeError, AttributeError, KeyError) as e:
                logger.warning(f"DSPy rendering failed for {prompt_name}: {e}. Falling back to Jinja2.")
                # Record fallback in trajectory if available
                self._notify_recorder(recorder, "record_fallback")

        # Fall back to Jinja2 (or default path)
        result = self._render_jinja2(template_name, kwargs, recorder=recorder)
        self._notify_recorder(recorder, "record_rendered_prompt", result)
        return result

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
        name = template_name.replace(".jinja2", "").replace("/", "_")
        return name

    def _should_use_dspy(self, prompt_name: str, kwargs: dict) -> bool:
        """Determine if DSPy should be used for this prompt.

        Returns False early if no optimized module exists for this prompt,
        avoiding noisy error/fallback paths for prompts that were never optimized.
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

        # Script-generation prompts must always use Jinja2. Their DSPy modules
        # generate the final script content as output (not an instruction), so
        # returning that content as full_prompt causes the RLM loop to receive
        # a bash script as its task and spiral into an explore loop.
        if prompt_name in _AGENT_INSTRUCTION_PROMPTS:
            return False

        # Check whether an optimized module actually exists for this prompt
        # before routing to DSPy. Prompts that were never optimized should
        # silently use Jinja2.
        if not self._optimized_module_exists(prompt_name):
            return False

        # Canary deployment - deterministic routing based on repo_path
        if self.dspy_config.canary_deployment:
            # Use repo_path for deterministic routing
            repo_path = kwargs.get("repo_path", "")
            if repo_path:
                try:
                    # Hash repo_path to get deterministic percentage
                    hash_val = int(hashlib.sha256(str(repo_path).encode()).hexdigest(), 16)
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
                        "Canary deployment enabled but canary_percentage is invalid. Falling back to Jinja2."
                    )
                    return False
            else:
                logger.warning("Canary deployment enabled but repo_path not in context. Falling back to Jinja2.")
                return False

        # Normal mode - use DSPy for prompts that have optimized modules
        return True

    def _optimized_module_exists(self, prompt_name: str) -> bool:
        """Check whether an optimized module file exists for this prompt.

        Args:
            prompt_name: Name of the prompt

        Returns:
            True if the module file exists in the target version directory
        """
        if prompt_name in self._module_exists_cache:
            return self._module_exists_cache[prompt_name]

        assert self.dspy_config is not None
        resolved = _resolve_optimized_version(self.optimized_dir, self.dspy_config.optimized_version)
        if resolved is None:
            result = False
        else:
            module_file = self.optimized_dir / resolved / f"{prompt_name}.dspy.json"
            result = module_file.exists()

        self._module_exists_cache[prompt_name] = result
        return result

    def _render_dspy(
        self,
        prompt_name: str,
        kwargs: dict,
        recorder: TrajectoryRecorderProtocol | None = None,
    ) -> str:
        """Render using DSPy optimized module.

        Args:
            prompt_name: Name of the prompt
            kwargs: Template context
            recorder: Optional trajectory recorder

        Returns:
            Rendered prompt string

        Raises:
            Exception: If DSPy rendering fails
        """
        _loader_mod = importlib.import_module("app_operator.dspy_integration.loader")
        load_optimized_module = _loader_mod.load_optimized_module
        _field_mod = importlib.import_module("app_operator.dspy_integration.field_mappings")
        map_kwargs_to_fields = _field_mod.map_kwargs_to_fields
        get_output_field_name = _field_mod.get_output_field_name

        # Configure DSPy runtime LM (once on first use)
        self._configure_dspy_runtime()

        assert self.dspy_config is not None
        # Load the optimized module
        module = load_optimized_module(prompt_name, self.optimized_dir, self.dspy_config.optimized_version)

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
            raise RuntimeError(f"DSPy result missing expected field '{output_field}' for {prompt_name}")

        output = getattr(result, output_field)

        # Record prompt version in trajectory if available
        self._notify_recorder(
            recorder,
            "set_prompt_version",
            f"dspy_{self.dspy_config.optimized_version}",
        )

        return output

    def render_template(self, template_name: str, kwargs: dict) -> str:
        """Render a Jinja2 template by name with the given kwargs."""
        return self._render_jinja2(template_name, kwargs)

    def _render_jinja2(
        self,
        template_name: str,
        kwargs: dict,
        recorder: TrajectoryRecorderProtocol | None = None,
    ) -> str:
        """Render using Jinja2 template.

        Args:
            template_name: Template file name
            kwargs: Template context
            recorder: Optional trajectory recorder

        Returns:
            Rendered prompt string

        Raises:
            RuntimeError: If Jinja2 rendering fails
        """
        try:
            # Check for seed override
            if self.dspy_config and self.dspy_config.use_seeds:
                prompt_name = self._template_to_prompt_name(template_name)
                if prompt_name in SEED_TEMPLATE_MAP:
                    template_name = SEED_TEMPLATE_MAP[prompt_name]
                    logger.debug(f"Using seed template for {prompt_name}: {template_name}")

            template = self.env.get_template(template_name)
            result = template.render(**kwargs)

            # Record prompt version in trajectory
            self._notify_recorder(recorder, "set_prompt_version", "jinja2")

            return result
        except (OSError, ValueError, KeyError) as e:
            # Wrap Jinja2/template errors for clearer debugging context
            raise RuntimeError(f"Failed to render template '{template_name}': {e}") from e

    @staticmethod
    def _notify_recorder(
        recorder: TrajectoryRecorderProtocol | None,
        method_name: str,
        *args: Any,
    ) -> None:
        """Dispatch a single recording call to the trajectory recorder.

        Replaces the four near-identical _record_* helpers with one
        consolidated method. Safely no-ops when recorder is None or
        does not implement the requested method.

        Args:
            recorder: Optional trajectory recorder instance.
            method_name: Name of the recorder method to call.
            *args: Positional arguments forwarded to the recorder method.
        """
        if recorder is None:
            return
        method = getattr(recorder, method_name, None)
        if method is not None:
            method(*args)


# Global instance for easy access
_loader = None
_loader_lock = threading.Lock()


def get_loader(dspy_config: DSPyConfigProtocol | None = None) -> PromptLoader:
    """Get or create the global PromptLoader instance.

    Args:
        dspy_config: Optional DSPy configuration. If provided and differs from
                     current loader's config, resets the loader.

    Returns:
        PromptLoader instance
    """
    global _loader

    with _loader_lock:
        # Reset loader if config changed (including when new config is None)
        if _loader is not None and _loader.dspy_config != dspy_config:
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
