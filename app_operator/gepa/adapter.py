"""Adapter bridging SDS's Jinja2 prompt system with GEPA's optimization loop."""

from dataclasses import dataclass
from pathlib import Path

from jinja2 import Environment, meta

from app_operator.logger import logger


@dataclass(frozen=True)
class TemplateInfo:
    """Metadata for an optimizable Jinja2 template."""

    agent_type: str
    description: str


class SDSPromptAdapter:
    """Maps SDS Jinja2 templates to GEPA candidate prompts.

    Responsibilities:
    1. Extract the optimizable text from Jinja2 templates
    2. Inject optimized text back into templates (preserving Jinja2 variables)
    3. Manage the mapping between template names and GEPA modules
    """

    OPTIMIZABLE_TEMPLATES: dict[str, TemplateInfo] = {
        "deployer/system.jinja2": TemplateInfo(
            agent_type="deployer",
            description="Core deployment instructions",
        ),
        "deployer/system_degraded.jinja2": TemplateInfo(
            agent_type="deployer",
            description="Degraded deployment instructions for GEPA optimization",
        ),
        "deployer/fix_error.jinja2": TemplateInfo(
            agent_type="deployer",
            description="Error diagnosis and fix instructions",
        ),
        "deployer/generate_script.jinja2": TemplateInfo(
            agent_type="deployer",
            description="Script generation task prompt",
        ),
        "deployer/summarize.jinja2": TemplateInfo(
            agent_type="deployer",
            description="Output summarization",
        ),
        "monitor/analyze_health.jinja2": TemplateInfo(
            agent_type="monitor",
            description="Health check analysis instructions",
        ),
        "code_analyzer/system.jinja2": TemplateInfo(
            agent_type="code_analyzer",
            description="Code analysis system prompt",
        ),
        "code_analyzer/user.jinja2": TemplateInfo(
            agent_type="code_analyzer",
            description="Code analysis user prompt",
        ),
    }

    def __init__(self, templates_dir: Path | None = None) -> None:
        if templates_dir is None:
            self.templates_dir = Path(__file__).resolve().parent.parent / "prompts" / "templates"
        else:
            self.templates_dir = Path(templates_dir)

    @staticmethod
    def _extract_variables(content: str) -> set[str]:
        """Extract undeclared Jinja2 variables from template content."""
        env = Environment()
        parsed = env.parse(content)
        return meta.find_undeclared_variables(parsed)

    def read_template(self, template_name: str) -> str:
        """Read the raw Jinja2 template text."""
        template_path = self.templates_dir / template_name
        return template_path.read_text()

    def write_template(self, template_name: str, content: str) -> None:
        """Write optimized text back to the template file."""
        template_path = self.templates_dir / template_name
        template_path.write_text(content)

    def validate_template(self, template_name: str, content: str) -> bool:
        """Validate that the optimized template still renders correctly.

        Checks:
        1. Template is in the known registry
        2. All variables from the original template are still present
        3. Template parses without errors
        """
        template_info = self.OPTIMIZABLE_TEMPLATES.get(template_name)
        if not template_info:
            return False

        try:
            original_content = self.read_template(template_name)
            original_vars = self._extract_variables(original_content)
        except FileNotFoundError:
            logger.warning(f"Original template not found for variable extraction: {template_name}")
            return False

        try:
            new_vars = self._extract_variables(content)
        except Exception as e:
            logger.warning(f"Template parse failed for {template_name}: {e}")
            return False

        missing_vars = original_vars - new_vars
        if missing_vars:
            logger.warning(
                f"Template validation failed: variable(s) "
                f"{', '.join(sorted(missing_vars))} "
                f"not found in {template_name}"
            )
            return False

        return True

    def get_templates_for_agent(self, agent_type: str) -> list[str]:
        """Get all template names for a given agent type."""
        return [name for name, info in self.OPTIMIZABLE_TEMPLATES.items() if info.agent_type == agent_type]

    def get_template_info(self, template_name: str) -> TemplateInfo | None:
        """Get metadata for a template."""
        return self.OPTIMIZABLE_TEMPLATES.get(template_name)
