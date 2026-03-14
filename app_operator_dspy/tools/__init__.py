"""DSPy tool functions for the SDS operator.

Each tool is a plain Python function with type hints and docstrings.
DSPy auto-wraps them as ``dspy.Tool`` instances when passed to
``dspy.ReAct``. Core implementations (read_file, list_files, etc.) are
used directly; wrappers in agent_tools.py are only kept where they add
behaviour (write_file_tool catches OSError; run_shell_tool injects cwd).
"""

from app_operator_dspy.tools import agent_tools
from app_operator_dspy.tools.filesystem import list_files, read_file, write_file
from app_operator_dspy.tools.health_check import run_health_check
from app_operator_dspy.tools.shell import ShellResult, run_shell

DEPLOYER_TOOLS = [
    agent_tools.run_shell_tool,
    read_file,
    agent_tools.write_file_tool,
    run_health_check,
    list_files,
]

CODE_ANALYZER_TOOLS = [
    read_file,
    list_files,
    agent_tools.run_shell_tool,
]

__all__ = [
    "CODE_ANALYZER_TOOLS",
    "DEPLOYER_TOOLS",
    "agent_tools",
    "list_files",
    "read_file",
    "run_health_check",
    "run_shell",
    "ShellResult",
    "write_file",
]
