import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Callable, Dict, List
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen
from datetime import datetime

from langchain_core.tools import tool

from app_operator.command_validation import DangerousCommandError, validate_command
from app_operator.filesystem import FileSystemInterface, RealFilesystem


class ToolContext:
    def __init__(self, repo_root: Path, filesystem: FileSystemInterface):
        self.repo_root = repo_root
        self.filesystem = filesystem

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
        except ValueError:
            raise ValueError(f"Path escapes repository root: {path}")

        return candidate


def _build_ls(context: ToolContext) -> Callable[[str], str]:
    @tool("LS")
    def ls(path: str = ".") -> str:
        """List files in the specified directory."""
        try:
            target = context.resolve_path(path)
            if not context.filesystem.exists(target):
                return f"Error: Path does not exist: {path}"
            if context.filesystem.exists(target) and not context.filesystem.is_dir(
                target
            ):
                result = target.name
                return result
            entries = sorted(p.name for p in target.iterdir())
            result = "\n".join(entries)
            return result
        except Exception as e:
            return f"Error: {str(e)}"

    return ls


def _build_glob(context: ToolContext) -> Callable[[str], List[str]]:
    @tool("Glob")
    def glob(pattern: str) -> List[str]:
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
        except Exception as e:
            return [f"Error: {str(e)}"]

    return glob


def _build_read(context: ToolContext) -> Callable[[str], str]:
    @tool("Read")
    def read(path: str) -> str:
        """Read the content of a file."""
        try:
            target = context.resolve_path(path)
            content = context.filesystem.read_text(target)
            return content
        except Exception as e:
            return f"Error: {str(e)}"

    return read


def _build_grep(context: ToolContext) -> Callable[[str, str], List[str]]:
    @tool("Grep")
    def grep(pattern: str, path: str = ".") -> List[str]:
        """Search for a regex pattern in files."""
        try:
            target = context.resolve_path(path)
            regex = re.compile(pattern)
            matches: List[str] = []

            if context.filesystem.exists(target) and not context.filesystem.is_dir(
                target
            ):
                matches.extend(_grep_file(regex, target, context.repo_root))
                return matches

            for file_path in target.rglob("*"):
                if file_path.is_file():
                    matches.extend(_grep_file(regex, file_path, context.repo_root))
            return matches
        except Exception as e:
            return [f"Error: {str(e)}"]

    return grep


def _grep_file(regex: re.Pattern, file_path: Path, repo_root: Path) -> List[str]:
    results = []
    try:
        content = file_path.read_text(errors="ignore")
    except Exception:
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
        except Exception as e:
            return f"Error: {str(e)}"

    return write_file


def _build_bash(context: ToolContext) -> Callable[[str, int], Dict[str, Any]]:
    @tool("bash")
    def bash(command: str, timeout: int = 120) -> Dict[str, Any]:
        """Execute a bash command."""
        try:
            validate_command(command)
            result = subprocess.run(
                command,
                cwd=str(context.repo_root),
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
            )

            return {
                "success": result.returncode == 0,
                "exit_code": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
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
        except Exception as e:
            error_msg = f"Error: {str(e)}"
            return {
                "success": False,
                "exit_code": -1,
                "stdout": "",
                "stderr": error_msg,
            }

    return bash


def _build_make_change_on_remote_copy(
    context: ToolContext,
) -> Callable[[str, str, str | None, str | None], Dict[str, Any]]:
    @tool("make_change_on_remote_copy")
    def make_change_on_remote_copy(
        branch_name: str,
        commit_message: str,
        mr_title: str | None = None,
        mr_description: str | None = None,
    ) -> Dict[str, Any]:
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
        # Ensure branch name is unique per run and valid for git
        # - Append a timestamp (no ':' characters, which git disallows)
        # - Sanitize any remaining invalid characters to '-'
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        base_name = re.sub(r"[^A-Za-z0-9._/-]", "-", branch_name.strip())
        base_name = re.sub(r"-+", "-", base_name).strip("-")
        if not base_name:
            base_name = "sds-change"
        branch_name = f"{base_name}-{timestamp}"

        def _run_git(args: list[str]) -> Dict[str, Any]:
            try:
                result = subprocess.run(
                    ["git", *args],
                    cwd=str(context.repo_root),
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
            except Exception as e:  # pragma: no cover - unexpected system errors
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

        git_outputs: list[str] = []

        # Ensure we are in a git repository
        status = _run_git(["rev-parse", "--is-inside-work-tree"])
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
        checkout = _run_git(["checkout", "-b", branch_name])
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

        # Stage and commit changes
        add_result = _run_git(["add", "-A"])
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
        sds_dir = context.repo_root / ".sds"
        if sds_dir.exists():
            add_sds = _run_git(["add", "-f", str(sds_dir)])
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

        # Ensure there is something staged to commit
        staged = _run_git(["diff", "--cached", "--name-only"])
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

        commit_result = _run_git(["commit", "-m", commit_message])
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

        # Push branch to origin
        push_result = _run_git(["push", "-u", "origin", branch_name])
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
        remote_result = _run_git(["remote", "get-url", "origin"])
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

        # Infer host and project path from remote URL
        gitlab_url_env = os.getenv("GITLAB_URL")
        host = ""
        project_path = ""

        if remote_url.startswith("git@"):
            # e.g. git@gitlab.com:group/project.git
            try:
                _, host_and_path = remote_url.split("@", 1)
                host, path = host_and_path.split(":", 1)
            except ValueError:
                host = ""
                path = ""
        elif remote_url.startswith("http://") or remote_url.startswith("https://"):
            try:
                without_scheme = remote_url.split("://", 1)[1]
                host, path = without_scheme.split("/", 1)
            except ValueError:
                host = ""
                path = ""
        else:
            host = ""
            path = ""

        if host and path:
            if path.endswith(".git"):
                path = path[: -len(".git")]
            project_path = path

        # Determine GitLab base URL
        if gitlab_url_env:
            gitlab_base = gitlab_url_env.rstrip("/")
        elif host:
            gitlab_base = f"https://{host}".rstrip("/")
        else:
            gitlab_base = "https://gitlab.com"

        # Validate that we have a project path
        if not project_path:
            return {
                "success": False,
                "branch": branch_name,
                "target_branch": None,
                "merge_request_url": None,
                "git_log": "".join(git_outputs),
                "error": (
                    f"Unable to infer GitLab project path from origin URL '{remote_url}'. "
                    f"Parsed host='{host}', path='{path}'. "
                    "Ensure origin points to a GitLab repo with format: git@host:group/project.git or https://host/group/project.git"
                ),
            }

        # Determine default target branch (origin HEAD -> refs/remotes/origin/<branch>)
        target_branch_result = _run_git(
            ["symbolic-ref", "refs/remotes/origin/HEAD"],
        )
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

        # Create merge request via GitLab API
        # URL-encode the project path (GitLab API requires group%2Fproject format)
        project_id_encoded = quote(project_path, safe="")
        api_url = f"{gitlab_base}/api/v4/projects/{project_id_encoded}/merge_requests"

        payload: Dict[str, Any] = {
            "source_branch": branch_name,
            "target_branch": target_branch,
            "title": mr_title or commit_message,
        }
        if mr_description:
            payload["description"] = mr_description

        data_bytes = json.dumps(payload).encode("utf-8")
        headers = {
            "PRIVATE-TOKEN": gitlab_token,
            "Content-Type": "application/json",
        }

        try:
            request = Request(api_url, data=data_bytes, headers=headers, method="POST")
            with urlopen(request, timeout=30) as response:
                status_code = response.getcode()
                body = response.read().decode("utf-8")
        except HTTPError as e:
            error_body = ""
            try:
                error_body = e.read().decode("utf-8")
            except Exception:
                error_body = str(e)
            # Include more context in error message
            return {
                "success": False,
                "branch": branch_name,
                "target_branch": target_branch,
                "merge_request_url": None,
                "git_log": "".join(git_outputs),
                "error": (
                    f"GitLab API error (HTTP {e.code}): {error_body[:500]}. "
                    f"API URL: {api_url}. Project path: {project_path}"
                ),
            }
        except URLError as e:
            return {
                "success": False,
                "branch": branch_name,
                "target_branch": target_branch,
                "merge_request_url": None,
                "git_log": "".join(git_outputs),
                "error": f"Network error talking to GitLab ({api_url}): {e}",
            }
        except Exception as e:  # pragma: no cover - unexpected network errors
            return {
                "success": False,
                "branch": branch_name,
                "target_branch": target_branch,
                "merge_request_url": None,
                "git_log": "".join(git_outputs),
                "error": f"Unexpected error talking to GitLab ({api_url}): {e}",
            }

        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            parsed = {}

        if not (200 <= status_code < 300):
            return {
                "success": False,
                "branch": branch_name,
                "target_branch": target_branch,
                "merge_request_url": None,
                "git_log": "".join(git_outputs),
                "error": (
                    f"GitLab API returned status {status_code}: {body[:500]}. "
                    f"API URL: {api_url}. Project path: {project_path}"
                ),
            }

        mr_url = parsed.get("web_url")
        if not mr_url:
            return {
                "success": False,
                "branch": branch_name,
                "target_branch": target_branch,
                "merge_request_url": None,
                "git_log": "".join(git_outputs),
                "error": (
                    f"GitLab API response did not include 'web_url'. "
                    f"Status: {status_code}, Response: {body[:500]}"
                ),
            }

        return {
            "success": True,
            "branch": branch_name,
            "target_branch": target_branch,
            "merge_request_url": mr_url,
            "git_log": "".join(git_outputs),
            "error": None,
        }

    return make_change_on_remote_copy


def build_tools(
    repo_path: Path,
    filesystem: FileSystemInterface = None,
    git_integration: bool = False,
) -> List[Callable[..., Any]]:
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
