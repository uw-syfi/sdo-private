"""Jinja2 prompt rendering for Crucible agents."""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

_CONFIG_DIR = Path(__file__).parent / "configs"
_PROMPTS_DIR = _CONFIG_DIR / "prompts"

_jinja_env = Environment(
    loader=FileSystemLoader(str(_PROMPTS_DIR)),
    undefined=StrictUndefined,
    keep_trailing_newline=True,
)


def _render(template_name: str, **kwargs: object) -> str:
    """Render a Jinja2 prompt template from configs/prompts/<template_name>.j2."""
    return _jinja_env.get_template(f"{template_name}.j2").render(**kwargs)
