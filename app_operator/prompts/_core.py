"""Core prompt loading infrastructure."""

from __future__ import annotations

import contextlib
import logging
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined

if TYPE_CHECKING:
    from app_operator.trajectory import TrajectoryRecorderProtocol


logger = logging.getLogger(__name__)


class PromptLoader:
    """Helper class to load and render Jinja2 templates for prompts."""

    def __init__(
        self,
        templates_dir: str | Path | None = None,
    ):
        """Initialize the loader.

        Args:
            templates_dir: Path to the directory containing templates.
                           Defaults to the 'templates' directory in this package.
        """
        if templates_dir is None:
            # templates/ is in app_operator/prompts/templates (parent of _core.py)
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

    def render(
        self,
        template_name: str,
        recorder: TrajectoryRecorderProtocol | None = None,
        **kwargs: Any,
    ) -> str:
        """Render a template with the given context.

        Args:
            template_name: Name of the template file (relative to prompts dir).
            recorder: Optional trajectory recorder for tracking prompt metadata.
            **kwargs: Variables to pass to the template.

        Returns:
            The rendered string.
        """
        self._notify_recorder(recorder, "record_prompt_kwargs", kwargs)
        result = self._render_jinja2(template_name, kwargs, recorder=recorder)
        self._notify_recorder(recorder, "record_rendered_prompt", result)
        return result

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


def get_loader() -> PromptLoader:
    """Get or create the global PromptLoader instance.

    Returns:
        PromptLoader instance
    """
    global _loader

    with _loader_lock:
        if _loader is None:
            _loader = PromptLoader()

        return _loader


def reset_loader() -> None:
    """Reset the loader singleton (for testing).

    This function allows tests to reset the global loader instance,
    ensuring test isolation when different tests need different
    loader configurations.
    """
    global _loader
    _loader = None


@contextlib.contextmanager
def override_loader(templates_dir: Path):
    """Temporarily replace the global PromptLoader with one using *templates_dir*.

    The original loader is restored when the context manager exits.
    """
    global _loader
    with _loader_lock:
        saved = _loader
        _loader = PromptLoader(templates_dir=templates_dir)
    try:
        yield
    finally:
        with _loader_lock:
            _loader = saved
