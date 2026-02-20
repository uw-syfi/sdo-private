"""RLM execution environment and context types."""

from dataclasses import dataclass, field


@dataclass
class RLMContext:
    """Deployment artifacts exposed as REPL variables in the RLM loop.

    Each field is a string containing the content of a deployment artifact.
    Empty string means the artifact is unavailable.
    """

    error_log: str = ""
    deployment_script: str = ""
    health_check_output: str = ""
    dockerfile: str = ""
    docker_compose: str = ""
    readme: str = ""
    analysis_report: str = ""
