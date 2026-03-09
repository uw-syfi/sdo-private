import json
import os
import re
import subprocess
import threading
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from langchain_core.tools import tool

from app_operator.command_validation import DangerousCommandError, validate_command
from app_operator.filesystem import FileSystemInterface, RealFilesystem

SUBPROCESS_TIMEOUT_SECS = 120  # seconds before a subprocess command times out
OUTPUT_SPILL_THRESHOLD = 10_000  # chars; total stdout+stderr above this triggers file spill


class ToolContext:
    def __init__(self, repo_root: Path, filesystem: FileSystemInterface):
        self.repo_root = repo_root
        self.filesystem = filesystem
        self._tool_call_counter = 0
        self._counter_lock = threading.Lock()

    def next_tool_call_id(self) -> int:
        with self._counter_lock:
            self._tool_call_counter += 1
            return self._tool_call_counter

    def resolve_path(self, path: str) -> Path:
        # Construct path without checking filesystem (for InMemoryFilesystem support)
        # Use absolute() which constructs path without filesystem checks
        path_obj = Path(path)
        if path_obj.is_absolute():
            candidate = path_obj
        else:
            candidate = self.repo_root / path

        # Normalize .. and . without filesystem access
        parts = []
        for part in candidate.parts:
            if part == "..":
                if parts:
                    parts.pop()
            elif part != ".":
                parts.append(part)

        if parts:
            candidate = Path(*parts)
        else:
            candidate = Path("/")

        # Check if path escapes repository root
        try:
            candidate.relative_to(self.repo_root)
        except ValueError as err:
            raise ValueError(f"Path escapes repository root: {path}") from err

        return candidate


def _build_ls(context: ToolContext) -> Callable[[str], str]:
    @tool("LS")
    def ls(path: str = ".") -> str:
        """List files in the specified directory."""
        try:
            target = context.resolve_path(path)
            if not context.filesystem.exists(target):
                return f"Error: Path does not exist: {path}"
            if context.filesystem.exists(target) and not context.filesystem.is_dir(target):
                result = target.name
                return result
            entries = sorted(p.name for p in target.iterdir())
            result = "\n".join(entries)
            return result
        except (ValueError, OSError) as e:
            return f"Error: {e!s}"

    return ls  # type: ignore[reportReturnType]


def _build_glob(context: ToolContext) -> Callable[[str], list[str]]:
    @tool("Glob")
    def glob(pattern: str) -> list[str]:
        """Find files matching the pattern."""
        try:
            if Path(pattern).is_absolute():
                try:
                    pattern = str(Path(pattern).relative_to(context.repo_root))
                except ValueError:
                    return [f"Error: Pattern escapes repository root: {pattern}"]

            results = []
            for path in context.repo_root.glob(pattern):
                if path.is_file() or path.is_dir():
                    try:
                        relative = path.relative_to(context.repo_root)
                        results.append(str(relative))
                    except ValueError:
                        continue
            results = sorted(results)
            return results
        except (ValueError, OSError) as e:
            return [f"Error: {e!s}"]

    return glob  # type: ignore[reportReturnType]


def _build_read(context: ToolContext) -> Callable[[str, int, int], str]:
    @tool("Read")
    def read(path: str, start_line: int, end_line: int) -> str:
        """Read a range of lines from a file (1-based, inclusive).

        Args:
            path: Path to the file.
            start_line: First line to return (1-based).
            end_line: Last line to return (1-based, inclusive).
        """
        try:
            target = context.resolve_path(path)
            content = context.filesystem.read_text(target)
            lines = content.splitlines(keepends=True)
            selected = lines[start_line - 1 : end_line]
            return "".join(selected)
        except (ValueError, OSError) as e:
            return f"Error: {e!s}"

    return read  # type: ignore[reportReturnType]


def _build_grep(context: ToolContext) -> Callable[[str, str], list[str]]:
    @tool("Grep")
    def grep(pattern: str, path: str = ".") -> list[str]:
        """Search for a regex pattern in files."""
        try:
            target = context.resolve_path(path)
            regex = re.compile(pattern)
            matches: list[str] = []

            if context.filesystem.exists(target) and not context.filesystem.is_dir(target):
                matches.extend(_grep_file(regex, target, context.repo_root))
                return matches

            for file_path in target.rglob("*"):
                if file_path.is_file():
                    matches.extend(_grep_file(regex, file_path, context.repo_root))
            return matches
        except (ValueError, OSError, re.error) as e:
            return [f"Error: {e!s}"]

    return grep  # type: ignore[reportReturnType]


def _grep_file(regex: re.Pattern, file_path: Path, repo_root: Path) -> list[str]:
    results = []
    try:
        content = file_path.read_text(errors="ignore")
    except OSError:
        return results

    for idx, line in enumerate(content.splitlines(), start=1):
        if regex.search(line):
            relative = file_path.relative_to(repo_root)
            results.append(f"{relative}:{idx}:{line.strip()}")
    return results


def _build_write_file(context: ToolContext) -> Callable[[str, str], str]:
    @tool("write_file")
    def write_file(path: str, content: str) -> str:
        """Write content to a file."""
        try:
            target = context.resolve_path(path)
            if context.filesystem.is_dir(target):
                return f"Error: Path is a directory: {path}"
            if not context.filesystem.exists(target.parent):
                context.filesystem.mkdir(target.parent, parents=True, exist_ok=True)
            context.filesystem.write_text(target, content)
            result = f"Wrote {len(content)} bytes to {path}"
            return result
        except (ValueError, OSError) as e:
            return f"Error: {e!s}"

    return write_file  # type: ignore[reportReturnType]


def _build_bash(context: ToolContext) -> Callable[[str, int], dict[str, Any]]:
    @tool("bash")
    def bash(command: str, timeout: int = SUBPROCESS_TIMEOUT_SECS) -> dict[str, Any]:
        """Execute a bash command."""
        try:
            validate_command(command)
            result = subprocess.run(  # noqa: S602 — shell=True required for agent commands
                command,
                cwd=str(context.repo_root),
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
            )

            stdout = result.stdout
            stderr = result.stderr

            if len(stdout) + len(stderr) > OUTPUT_SPILL_THRESHOLD:
                call_id = context.next_tool_call_id()
                spill_dir = context.repo_root / ".sds" / "logs" / "tools" / f"{call_id:04d}"
                context.filesystem.mkdir(spill_dir, parents=True, exist_ok=True)

                stdout_path = f".sds/logs/tools/{call_id:04d}/stdout.txt"
                context.filesystem.write_text(spill_dir / "stdout.txt", stdout)
                stdout = f"(output too large for context; use Read tool: {stdout_path})"

                if stderr:
                    stderr_path = f".sds/logs/tools/{call_id:04d}/stderr.txt"
                    context.filesystem.write_text(spill_dir / "stderr.txt", stderr)
                    stderr = f"(output too large for context; use Read tool: {stderr_path})"

            return {
                "success": result.returncode == 0,
                "exit_code": result.returncode,
                "stdout": stdout,
                "stderr": stderr,
            }
        except DangerousCommandError as e:
            error_msg = str(e)
            return {
                "success": False,
                "exit_code": -1,
                "stdout": "",
                "stderr": error_msg,
            }
        except subprocess.TimeoutExpired:
            error_msg = f"Command timed out after {timeout} seconds"
            return {
                "success": False,
                "exit_code": -1,
                "stdout": "",
                "stderr": error_msg,
            }
        except OSError as e:
            error_msg = f"Error: {e!s}"
            return {
                "success": False,
                "exit_code": -1,
                "stdout": "",
                "stderr": error_msg,
            }

    return bash  # type: ignore[reportReturnType]


def _run_git(args: list[str], cwd: str) -> dict[str, Any]:
    """Run a git subprocess and return a result dict."""
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "exit_code": -1,
            "stdout": "",
            "stderr": f"git {' '.join(args)} timed out after 120 seconds",
        }
    except OSError as e:  # pragma: no cover - unexpected system errors
        return {
            "success": False,
            "exit_code": -1,
            "stdout": "",
            "stderr": f"Error running git {' '.join(args)}: {e}",
        }
    return {
        "success": result.returncode == 0,
        "exit_code": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def _sanitize_branch_name(branch_name: str) -> str:
    """Sanitize a branch name and append a timestamp to make it unique."""
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    base_name = re.sub(r"[^A-Za-z0-9._/-]", "-", branch_name.strip())
    base_name = re.sub(r"-+", "-", base_name).strip("-")
    if not base_name:
        base_name = "sds-change"
    return f"{base_name}-{timestamp}"


def _stage_and_commit(
    cwd: str,
    repo_root: Path,
    commit_message: str,
    branch_name: str,
    git_outputs: list[str],
) -> dict[str, Any] | None:
    """Stage all changes and commit them. Returns an error dict on failure, None on success."""
    add_result = _run_git(["add", "-A"], cwd)
    git_outputs.append(add_result["stdout"] + add_result["stderr"])
    if not add_result["success"]:
        return {
            "success": False,
            "branch": branch_name,
            "target_branch": None,
            "merge_request_url": None,
            "git_log": "".join(git_outputs),
            "error": "Failed to stage changes.",
        }

    # Force-add .sds directory (ignored by .gitignore) so generated scripts are captured
    sds_dir = repo_root / ".sds"
    if sds_dir.exists():
        add_sds = _run_git(["add", "-f", str(sds_dir)], cwd)
        git_outputs.append(add_sds["stdout"] + add_sds["stderr"])
        if not add_sds["success"]:
            return {
                "success": False,
                "branch": branch_name,
                "target_branch": None,
                "merge_request_url": None,
                "git_log": "".join(git_outputs),
                "error": "Failed to stage .sds directory.",
            }

    staged = _run_git(["diff", "--cached", "--name-only"], cwd)
    git_outputs.append(staged["stdout"] + staged["stderr"])
    if not staged["success"]:
        return {
            "success": False,
            "branch": branch_name,
            "target_branch": None,
            "merge_request_url": None,
            "git_log": "".join(git_outputs),
            "error": "Failed to inspect staged changes.",
        }

    if not staged["stdout"].strip():
        return {
            "success": False,
            "branch": branch_name,
            "target_branch": None,
            "merge_request_url": None,
            "git_log": "".join(git_outputs),
            "error": (
                "No staged changes to commit. "
                "Note: files under .sds are normally ignored by git; "
                "ensure you expect them to be committed."
            ),
        }

    commit_result = _run_git(["commit", "-m", commit_message], cwd)
    git_outputs.append(commit_result["stdout"] + commit_result["stderr"])
    if not commit_result["success"]:
        return {
            "success": False,
            "branch": branch_name,
            "target_branch": None,
            "merge_request_url": None,
            "git_log": "".join(git_outputs),
            "error": "Failed to commit changes.",
        }

    return None


def _parse_remote_url(remote_url: str) -> tuple[str, str]:
    """Parse a git remote URL into (host, project_path). Returns ('', '') on failure."""
    host = ""
    path = ""
    if remote_url.startswith("git@"):
        # e.g. git@gitlab.com:group/project.git
        try:
            _, host_and_path = remote_url.split("@", 1)
            host, path = host_and_path.split(":", 1)
        except ValueError:
            host = ""
            path = ""
    elif remote_url.startswith(("http://", "https://")):
        try:
            without_scheme = remote_url.split("://", 1)[1]
            host, path = without_scheme.split("/", 1)
        except ValueError:
            host = ""
            path = ""

    if host and path:
        path = path.removesuffix(".git")

    return host, path


def _get_gitlab_info(remote_url: str, gitlab_url_env: str | None) -> tuple[str, str]:
    """Derive (gitlab_base_url, project_path) from a remote URL and optional env override."""
    host, path = _parse_remote_url(remote_url)
    project_path = path if (host and path) else ""

    if gitlab_url_env:
        gitlab_base = gitlab_url_env.rstrip("/")
    elif host:
        gitlab_base = f"https://{host}".rstrip("/")
    else:
        gitlab_base = "https://gitlab.com"

    return gitlab_base, project_path


def _create_gitlab_mr(
    gitlab_base: str,
    project_path: str,
    branch_name: str,
    target_branch: str,
    token: str,
    title: str,
    description: str | None,
) -> dict[str, Any]:
    """Call the GitLab API to create a merge request. Returns result dict."""
    project_id_encoded = quote(project_path, safe="")
    api_url = f"{gitlab_base}/api/v4/projects/{project_id_encoded}/merge_requests"

    payload: dict[str, Any] = {
        "source_branch": branch_name,
        "target_branch": target_branch,
        "title": title,
    }
    if description:
        payload["description"] = description

    data_bytes = json.dumps(payload).encode("utf-8")
    headers = {"PRIVATE-TOKEN": token, "Content-Type": "application/json"}

    try:
        request = Request(api_url, data=data_bytes, headers=headers, method="POST")
        with urlopen(request, timeout=30) as response:
            status_code = response.getcode()
            body = response.read().decode("utf-8")
    except HTTPError as e:
        error_body = ""
        try:
            error_body = e.read().decode("utf-8")
        except OSError:
            error_body = str(e)
        return {
            "success": False,
            "mr_url": None,
            "error": (
                f"GitLab API error (HTTP {e.code}): {error_body[:500]}. "
                f"API URL: {api_url}. Project path: {project_path}"
            ),
        }
    except URLError as e:
        return {
            "success": False,
            "mr_url": None,
            "error": f"Network error talking to GitLab ({api_url}): {e}",
        }
    except (OSError, ValueError) as e:  # pragma: no cover - unexpected network errors
        return {
            "success": False,
            "mr_url": None,
            "error": f"Unexpected error talking to GitLab ({api_url}): {e}",
        }

    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        parsed = {}

    if not (200 <= status_code < 300):
        return {
            "success": False,
            "mr_url": None,
            "error": (
                f"GitLab API returned status {status_code}: {body[:500]}. "
                f"API URL: {api_url}. Project path: {project_path}"
            ),
        }

    mr_url = parsed.get("web_url")
    if not mr_url:
        return {
            "success": False,
            "mr_url": None,
            "error": (f"GitLab API response did not include 'web_url'. Status: {status_code}, Response: {body[:500]}"),
        }

    return {"success": True, "mr_url": mr_url, "error": None}


def _build_make_change_on_remote_copy(
    context: ToolContext,
) -> Callable[[str, str, str | None, str | None], dict[str, Any]]:
    @tool("make_change_on_remote_copy")
    def make_change_on_remote_copy(
        branch_name: str,
        commit_message: str,
        mr_title: str | None = None,
        mr_description: str | None = None,
    ) -> dict[str, Any]:
        """Create a branch, commit local changes, push to origin, and open a GitLab MR.

        This tool is intended to be called after the agent has made code changes locally.

        Args:
            branch_name: Name of the feature branch to create from the current HEAD.
            commit_message: Commit message for the changes.
            mr_title: Optional title for the merge request (defaults to commit_message).
            mr_description: Optional description/body for the merge request.

        Returns:
            A dictionary containing:
            - 'success': True if all steps succeeded, False otherwise.
            - 'branch': The created branch name (if successful).
            - 'target_branch': The detected default branch on origin (if available).
            - 'merge_request_url': URL of the created merge request (if successful).
            - 'git_log': Combined stdout/stderr from git commands.
            - 'error': Error message if something failed.

        Environment:
            - Requires 'git' CLI available.
            - Requires GitLab access token in GITLAB_TOKEN.
            - Optionally uses GITLAB_URL; if not set, inferred from 'origin' remote.
        """
        cwd = str(context.repo_root)
        branch_name = _sanitize_branch_name(branch_name)

        git_outputs: list[str] = []

        # Ensure we are in a git repository
        status = _run_git(["rev-parse", "--is-inside-work-tree"], cwd)
        git_outputs.append(status["stdout"] + status["stderr"])
        if not status["success"]:
            return {
                "success": False,
                "branch": None,
                "target_branch": None,
                "merge_request_url": None,
                "git_log": "".join(git_outputs),
                "error": "Not inside a git repository or git is not available.",
            }

        # Create and switch to the new branch
        checkout = _run_git(["checkout", "-b", branch_name], cwd)
        git_outputs.append(checkout["stdout"] + checkout["stderr"])
        if not checkout["success"]:
            return {
                "success": False,
                "branch": None,
                "target_branch": None,
                "merge_request_url": None,
                "git_log": "".join(git_outputs),
                "error": f"Failed to create/switch to branch '{branch_name}'.",
            }

        # Stage and commit all changes
        commit_error = _stage_and_commit(cwd, context.repo_root, commit_message, branch_name, git_outputs)
        if commit_error is not None:
            return commit_error

        # Push branch to origin
        push_result = _run_git(["push", "-u", "origin", branch_name], cwd)
        git_outputs.append(push_result["stdout"] + push_result["stderr"])
        if not push_result["success"]:
            return {
                "success": False,
                "branch": branch_name,
                "target_branch": None,
                "merge_request_url": None,
                "git_log": "".join(git_outputs),
                "error": "Failed to push branch to origin.",
            }

        # Detect GitLab project and target branch
        remote_result = _run_git(["remote", "get-url", "origin"], cwd)
        git_outputs.append(remote_result["stdout"] + remote_result["stderr"])
        if not remote_result["success"]:
            return {
                "success": False,
                "branch": branch_name,
                "target_branch": None,
                "merge_request_url": None,
                "git_log": "".join(git_outputs),
                "error": "Failed to get origin remote URL.",
            }

        remote_url = remote_result["stdout"].strip()
        if not remote_url:
            return {
                "success": False,
                "branch": branch_name,
                "target_branch": None,
                "merge_request_url": None,
                "git_log": "".join(git_outputs),
                "error": "Origin remote URL is empty.",
            }

        gitlab_base, project_path = _get_gitlab_info(remote_url, os.getenv("GITLAB_URL"))

        if not project_path:
            host, path = _parse_remote_url(remote_url)
            return {
                "success": False,
                "branch": branch_name,
                "target_branch": None,
                "merge_request_url": None,
                "git_log": "".join(git_outputs),
                "error": (
                    f"Unable to infer GitLab project path from origin URL '{remote_url}'. "
                    f"Parsed host='{host}', path='{path}'. "
                    "Ensure origin points to a GitLab repo with format: "
                    "git@host:group/project.git or https://host/group/project.git"
                ),
            }

        # Determine default target branch (origin HEAD -> refs/remotes/origin/<branch>)
        target_branch_result = _run_git(["symbolic-ref", "refs/remotes/origin/HEAD"], cwd)
        git_outputs.append(target_branch_result["stdout"] + target_branch_result["stderr"])
        if target_branch_result["success"]:
            ref = target_branch_result["stdout"].strip()
            target_branch = ref.split("/")[-1] if ref else "master"
        else:
            target_branch = "master"

        gitlab_token = os.getenv("GITLAB_TOKEN")
        if not gitlab_token:
            return {
                "success": False,
                "branch": branch_name,
                "target_branch": target_branch,
                "merge_request_url": None,
                "git_log": "".join(git_outputs),
                "error": "GITLAB_TOKEN environment variable is not set.",
            }

        mr_result = _create_gitlab_mr(
            gitlab_base,
            project_path,
            branch_name,
            target_branch,
            gitlab_token,
            mr_title or commit_message,
            mr_description,
        )

        if not mr_result["success"]:
            return {
                "success": False,
                "branch": branch_name,
                "target_branch": target_branch,
                "merge_request_url": None,
                "git_log": "".join(git_outputs),
                "error": mr_result["error"],
            }

        return {
            "success": True,
            "branch": branch_name,
            "target_branch": target_branch,
            "merge_request_url": mr_result["mr_url"],
            "git_log": "".join(git_outputs),
            "error": None,
        }

    return make_change_on_remote_copy  # type: ignore[reportReturnType]


def build_tools(
    repo_path: Path,
    filesystem: FileSystemInterface | None = None,
    git_integration: bool = False,
) -> list[Callable[..., Any]]:
    if filesystem is None:
        filesystem = RealFilesystem()

    context = ToolContext(repo_root=repo_path.resolve(), filesystem=filesystem)
    tools = [
        _build_ls(context),
        _build_glob(context),
        _build_read(context),
        _build_grep(context),
        _build_write_file(context),
        _build_bash(context),
    ]
    if git_integration:
        tools.append(_build_make_change_on_remote_copy(context))
    return tools
