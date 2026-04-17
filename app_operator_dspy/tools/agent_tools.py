"""DSPy tool wrappers for ReAct agents.

run_shell_tool and write_file_tool are thin wrappers that add behaviour
(context injection and error-to-string conversion respectively).  All
other tools are used directly from their implementation modules.
"""

import functools
import os

from app_operator_dspy.tools.context import get_task_repo
from app_operator_dspy.tools.filesystem import write_file
from app_operator_dspy.tools.shell import DEFAULT_TIMEOUT, run_shell

# Limit tool output to keep ReAct trajectory within context window.
_MAX_TOOL_OUTPUT_CHARS = 10_000


def _resolve_path(path: str) -> str:
    """Resolve relative paths against the task repo to prevent writes to repo root."""
    if not os.path.isabs(path):
        repo = get_task_repo()
        if repo:
            return os.path.join(repo, path)
    return path


def _truncate_output(text: str) -> str:
    """Truncate keeping the tail (errors are usually at the end)."""
    if len(text) <= _MAX_TOOL_OUTPUT_CHARS:
        return text
    return f"[truncated, showing last {_MAX_TOOL_OUTPUT_CHARS} chars]\n..." + text[-_MAX_TOOL_OUTPUT_CHARS:]


@functools.wraps(write_file)
def write_file_tool(path: str, content: str) -> str:
    """Write content to file. Returns success message or error string."""
    path = _resolve_path(path)
    try:
        return write_file(path, content)
    except OSError as exc:
        return f"Error writing {path}: {exc}"


def run_shell_tool(command: str, timeout: int = DEFAULT_TIMEOUT) -> str:
    """Execute shell command. Returns formatted output string.

    Runs with cwd set to the task's repo path (from operator context).
    Raises RuntimeError if task repo context is not set.
    """
    cwd = get_task_repo()
    if cwd is None:
        raise RuntimeError(
            "run_shell_tool called without task repo context; "
            "operator must call set_task_repo(repo_path) before agent invocation"
        )
    result = run_shell(command, cwd=cwd, timeout=timeout)
    return _truncate_output(result.output)


def read_file_tool(path: str) -> str:
    """Read and return the contents of a file (truncated to fit context)."""
    from app_operator_dspy.tools.filesystem import read_file

    return _truncate_output(read_file(_resolve_path(path)))


def list_files_tool(path: str, pattern: str = "*") -> str:
    """List files matching a glob pattern (truncated to fit context)."""
    from app_operator_dspy.tools.filesystem import list_files

    return _truncate_output(list_files(_resolve_path(path), pattern))


def run_health_check_tool(repo_path: str, timeout: int = DEFAULT_TIMEOUT) -> str:
    """Run health check script (truncated to fit context)."""
    from app_operator_dspy.tools.health_check import run_health_check

    return _truncate_output(run_health_check(repo_path, timeout))


def validate_compose_tool(compose_path: str) -> str:
    """Validate a docker-compose file and check if build context paths exist.

    Parses the compose file, checks that referenced build contexts and
    Dockerfiles exist on disk, and reports any structural issues.
    """
    import yaml

    compose_path = _resolve_path(compose_path)
    try:
        with open(compose_path) as f:
            content = f.read()
    except OSError as exc:
        return f"Error reading {compose_path}: {exc}"

    try:
        data = yaml.safe_load(content)
    except yaml.YAMLError as exc:
        return f"Invalid YAML in {compose_path}: {exc}"

    if not isinstance(data, dict):
        return f"Invalid compose file: expected mapping, got {type(data).__name__}"

    issues = []
    services = data.get("services", {})
    if not services:
        issues.append("No 'services' key found in compose file")

    compose_dir = os.path.dirname(os.path.abspath(compose_path))
    for name, svc in (services or {}).items():
        if not isinstance(svc, dict):
            issues.append(f"Service '{name}': expected mapping, got {type(svc).__name__}")
            continue
        build = svc.get("build")
        if isinstance(build, str):
            ctx = os.path.join(compose_dir, build)
            if not os.path.isdir(ctx):
                issues.append(f"Service '{name}': build context '{build}' does not exist")
        elif isinstance(build, dict):
            ctx_path = build.get("context", ".")
            ctx = os.path.join(compose_dir, ctx_path)
            if not os.path.isdir(ctx):
                issues.append(f"Service '{name}': build context '{ctx_path}' does not exist")
            dockerfile = build.get("dockerfile")
            if dockerfile:
                df_path = os.path.join(ctx, dockerfile)
                if not os.path.isfile(df_path):
                    issues.append(f"Service '{name}': dockerfile '{dockerfile}' not found in '{ctx_path}'")

    if not issues:
        return f"Compose file {compose_path} is valid ({len(services)} services)"
    return "Compose validation issues:\n" + "\n".join(f"- {i}" for i in issues)


# Expose stable names for ReAct (LLM expects these from signatures)
write_file_tool.__name__ = "write_file"
run_shell_tool.__name__ = "run_shell"
read_file_tool.__name__ = "read_file"
list_files_tool.__name__ = "list_files"
run_health_check_tool.__name__ = "run_health_check"
validate_compose_tool.__name__ = "validate_compose"
