"""Tests that all Jinja2 templates render without UndefinedError when given their expected variables."""

from __future__ import annotations

from pathlib import Path

import jinja2
import jinja2.meta
import pytest

_PROMPTS_ROOT = Path(__file__).resolve().parents[3] / "sregym_agents" / "crucible" / "configs" / "prompts"
# Default version used by tests — adjust when adding new versions.
_DEFAULT_VERSION = "v1"
PROMPTS_DIR = _PROMPTS_ROOT / _DEFAULT_VERSION


def _get_all_templates() -> list[tuple[str, Path]]:
    """Return (template_name, template_path) for all .j2 files."""
    results = []
    for p in sorted(PROMPTS_DIR.rglob("*.j2")):
        name = str(p.relative_to(PROMPTS_DIR)).removesuffix(".j2")
        results.append((name, p))
    return results


def _get_required_variables(template_path: Path) -> set[str]:
    """Extract undeclared variables from a Jinja2 template."""
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(PROMPTS_DIR)),
        undefined=jinja2.StrictUndefined,
    )
    source = template_path.read_text()
    ast = env.parse(source)
    return jinja2.meta.find_undeclared_variables(ast)


_ALL_TEMPLATES = _get_all_templates()


@pytest.mark.parametrize(
    ("template_name", "template_path"),
    _ALL_TEMPLATES,
    ids=[t[0] for t in _ALL_TEMPLATES],
)
def test_template_renders_with_all_variables(template_name: str, template_path: Path) -> None:
    """Each template must render without UndefinedError when all its variables are provided."""
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(PROMPTS_DIR)),
        undefined=jinja2.StrictUndefined,
        keep_trailing_newline=True,
    )
    required_vars = _get_required_variables(template_path)
    dummy_kwargs = dict.fromkeys(required_vars, "dummy_value")
    # Jinja2's find_undeclared_variables misses ``namespace`` because it
    # shadows the built-in namespace() constructor.  Always supply it so the
    # render test covers templates that reference {{ namespace }}.
    dummy_kwargs.setdefault("namespace", "dummy_value")
    template = env.get_template(f"{template_name}.j2")
    # Should not raise UndefinedError
    result = template.render(**dummy_kwargs)
    assert isinstance(result, str)


def test_render_function_uses_strict_undefined() -> None:
    """Verify that PromptRenderer uses StrictUndefined so missing variables raise errors."""
    from sregym_agents.crucible._prompts import PromptRenderer

    renderer = PromptRenderer(_DEFAULT_VERSION)
    with pytest.raises(jinja2.UndefinedError):
        # diagnosis_agent_user.j2 requires many variables — omit most to trigger the error.
        renderer.render("diagnosis_agent_user", app_name="test", namespace="test")


# Map of template_name -> set of variables that MUST be passed.
# This is the contract between templates and callers.
# Note: Jinja2's find_undeclared_variables does not report ``namespace`` because
# it collides with the built-in ``namespace()`` function.  The parametrized
# render test (test_template_renders_with_all_variables) still catches missing
# ``namespace`` at render time because StrictUndefined is active.
TEMPLATE_VARIABLE_CONTRACT: dict[str, set[str]] = {
    "diagnosis_agent_system": set(),
    "diagnosis_agent_user": {
        "app_name",
        "descriptions",
        "iteration",
        "shared_content",
        "shared_file",
        "architecture_content",
        "lessons_content",
        "lt_summary_content",
    },
    "diagnosis_judge_system": set(),
    "diagnosis_judge_user": {
        "app_name",
        "iteration",
        "shared_content",
        "shared_file",
        "architecture_content",
        "lessons_content",
        "lt_summary_content",
    },
    "mitigation_agent_system": set(),
    "mitigation_agent_user": {
        "app_name",
        "descriptions",
        "iteration",
        "shared_content",
        "shared_file",
        "architecture_content",
        "lessons_content",
        "lt_summary_content",
    },
    "mitigation_judge_system": set(),
    "mitigation_judge_user": {
        "app_name",
        "iteration",
        "shared_content",
        "shared_file",
        "architecture_content",
        "lessons_content",
        "lt_summary_content",
    },
    "search_prior_incidents": {
        "stage",
        "observed_symptoms",
        "triage_context",
        "lt_summary_file",
        "incidents_dir",
    },
    "triage_cluster": set(),
    "ltm_verify_candidate": {
        "stage",
        "observed_symptoms",
        "triage_context",
        "candidate_index",
        "root_cause_class",
        "root_cause",
        "distinguishing_check",
        "mitigation_hint",
    },
    "kb/extract_lessons": {"long_term_summary"},
    "kb/summarize_session": {"content", "include_benchmark_results"},
    "kb/merge_summary": {"prior_summary", "session_summary", "incident_ref"},
    "kb/extract_triage_checklist": {"operational_lessons", "long_term_summary"},
}


@pytest.mark.parametrize(
    ("template_name", "expected_vars"),
    list(TEMPLATE_VARIABLE_CONTRACT.items()),
    ids=list(TEMPLATE_VARIABLE_CONTRACT.keys()),
)
def test_template_contract_matches_actual_variables(template_name: str, expected_vars: set[str]) -> None:
    """The variable contract must match what the template actually references."""
    template_path = PROMPTS_DIR / f"{template_name}.j2"
    assert template_path.exists(), f"Template {template_name}.j2 not found"
    actual_vars = _get_required_variables(template_path)
    assert actual_vars == expected_vars, (
        f"Template {template_name} expects {actual_vars} but contract says {expected_vars}"
    )
