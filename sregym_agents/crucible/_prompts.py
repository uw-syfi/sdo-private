"""Jinja2 prompt rendering for Crucible agents."""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

_CONFIG_DIR = Path(__file__).parent / "configs"
_PROMPTS_DIR = _CONFIG_DIR / "prompts"

_jinja_env: Environment | None = None


def configure(prompt_version: str) -> None:
    """Set the active prompt version directory (e.g. ``"v1"``).

    Must be called once before any :func:`_render` call.  The version
    string selects the subdirectory ``configs/prompts/<prompt_version>/``
    that contains the Jinja2 template files.
    """
    global _jinja_env
    prompts_dir = _PROMPTS_DIR / prompt_version
    if not prompts_dir.is_dir():
        raise ValueError(
            f"Prompt version {prompt_version!r} not found at {prompts_dir}"
        )
    _jinja_env = Environment(
        loader=FileSystemLoader(str(prompts_dir)),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
    )


def _render(template_name: str, **kwargs: object) -> str:
    """Render a Jinja2 prompt template from the configured version directory."""
    if _jinja_env is None:
        raise RuntimeError(
            "Prompts not configured. Call configure(prompt_version) first."
        )
    return _jinja_env.get_template(f"{template_name}.j2").render(**kwargs)
