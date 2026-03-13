"""DSPy tool functions for the SDS operator.

Each tool is a plain Python function with type hints and docstrings.
DSPy auto-wraps them as ``dspy.Tool`` instances when passed to
``dspy.ReAct``.
"""

from app_operator_dspy.tools.docker import docker_compose_up, docker_logs, docker_ps
from app_operator_dspy.tools.filesystem import list_files, read_file, write_file
from app_operator_dspy.tools.health_check import run_health_check
from app_operator_dspy.tools.shell import run_shell

DEPLOYER_TOOLS = [run_shell, read_file, write_file, run_health_check, list_files]

__all__ = [
    "DEPLOYER_TOOLS",
    "docker_compose_up",
    "docker_logs",
    "docker_ps",
    "list_files",
    "read_file",
    "run_health_check",
    "run_shell",
    "write_file",
]
