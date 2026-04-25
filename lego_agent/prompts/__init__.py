from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined


class PromptLoader:
    """Helper class to load and render Jinja2 templates for prompts."""

    def __init__(self, templates_dir: str | Path | None = None):
        """Initialize the loader.

        Args:
            templates_dir: Path to the directory containing templates.
                           Defaults to the 'templates' directory in this package.
        """
        if templates_dir is None:
            # Assumes this file is in lego_agent/prompts/__init__.py
            # and templates/ is in lego_agent/prompts/templates
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

    def render(self, template_name: str, **kwargs: Any) -> str:
        """Render a template with the given context.

        Args:
            template_name: Name of the template file (relative to prompts dir).
            **kwargs: Variables to pass to the template.

        Returns:
            The rendered string.
        """
        try:
            template = self.env.get_template(template_name)
            return template.render(**kwargs)
        except Exception as e:
            # Wrap Jinja2 errors for clearer debugging context
            raise RuntimeError(f"Failed to render template '{template_name}': {e}") from e


# Global instance for easy access
_loader = None


def get_loader() -> PromptLoader:
    global _loader
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


__all__ = ["PromptLoader", "get_loader", "reset_loader"]
