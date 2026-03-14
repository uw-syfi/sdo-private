"""DSPy tool functions for the SDS operator.

Each tool is a plain Python function with type hints and docstrings.
DSPy auto-wraps them as ``dspy.Tool`` instances when passed to
``dspy.ReAct``. Tool wrappers live in agent_tools.py with consistent
_tool suffix; core implementations are in filesystem, shell, health_check.
"""

from app_operator_dspy.tools import agent_tools
from app_operator_dspy.tools.filesystem import list_files, read_file, write_file
from app_operator_dspy.tools.health_check import run_health_check
from app_operator_dspy.tools.shell import ShellResult, run_shell

DEPLOYER_TOOLS = [
    agent_tools.run_shell_tool,
    agent_tools.read_file_tool,
    agent_tools.write_file_tool,
    agent_tools.run_health_check_tool,
    agent_tools.list_files_tool,
]

__all__ = [
    "DEPLOYER_TOOLS",
    "agent_tools",
    "list_files",
    "read_file",
    "run_health_check",
    "run_shell",
    "ShellResult",
    "write_file",
    "write_file_tool",
]

# Re-export write_file_tool for backward compatibility
write_file_tool = agent_tools.write_file_tool
