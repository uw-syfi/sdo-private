"""Jinja2 prompt rendering for Crucible agents."""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

_CONFIG_DIR = Path(__file__).parent / "configs"
_PROMPTS_DIR = _CONFIG_DIR / "prompts"


def _make_env(prompt_version: str) -> Environment:
    """Create a Jinja2 environment for the given prompt version directory."""
    prompts_dir = _PROMPTS_DIR / prompt_version
    if not prompts_dir.is_dir():
        raise ValueError(f"Prompt version {prompt_version!r} not found at {prompts_dir}")
    return Environment(
        loader=FileSystemLoader(str(prompts_dir)),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
    )


class PromptRenderer:
    """Renders Jinja2 prompt templates from a versioned directory.

    Create once with a prompt version string and pass the instance through
    the call chain wherever prompt rendering is needed.
    """

    def __init__(self, prompt_version: str) -> None:
        self._version = prompt_version
        self._env = _make_env(prompt_version)

    def render(self, template_name: str, **kwargs: object) -> str:
        """Render a Jinja2 template from the configured version directory."""
        return self._env.get_template(f"{template_name}.j2").render(**kwargs)
