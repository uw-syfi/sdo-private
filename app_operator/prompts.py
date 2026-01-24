from jinja2 import Environment, FileSystemLoader, StrictUndefined
from pathlib import Path
from typing import Any


class PromptLoader:
    """Helper class to load and render Jinja2 templates for prompts."""

    def __init__(self, templates_dir: str | Path = None):
        """Initialize the loader.

        Args:
            templates_dir: Path to the directory containing templates.
                           Defaults to the 'prompts' directory at the project root.
        """
        if templates_dir is None:
            # Assumes this file is in app_operator/prompts.py
            # and prompts/ is in the project root.
            root_dir = Path(__file__).resolve().parents[1]
            self.templates_dir = root_dir / "prompts"
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
            raise RuntimeError(
                f"Failed to render template '{template_name}': {e}"
            ) from e


# Global instance for easy access
_loader = None


def get_loader() -> PromptLoader:
    global _loader
    if _loader is None:
        _loader = PromptLoader()
    return _loader
