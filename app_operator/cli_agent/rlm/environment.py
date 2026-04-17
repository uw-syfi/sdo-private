"""RLM Environment - REPL wrapper for context management.

Implements the core RLM paradigm: context stored as variables in a REPL
that the LLM can programmatically query, filter, and recursively process.
"""

import inspect
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, cast

from app_operator.core import logger

RecursiveLLMFunction = Callable[[str], str] | Callable[[str, dict[str, Any] | None], str]


def _estimate_tokens(text: str) -> int:
    """Estimate the number of tokens in a text string.

    Uses tiktoken (cl100k_base) when available for accurate counting.
    Falls back to a character-ratio heuristic that accounts for text type:
    - Code/logs tend to tokenize at ~3.5 chars/token (more symbols)
    - Natural language tends to tokenize at ~4.5 chars/token
    - Mixed content averages ~4 chars/token

    This is more accurate than a flat chars//4 for heterogeneous content.
    """
    if not text:
        return 0

    try:
        import tiktoken

        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except (ImportError, ValueError):
        pass

    # Fallback: character-ratio heuristic
    # Count non-alphanumeric characters as a proxy for code/log density
    non_alnum = sum(1 for c in text[:1000] if not c.isalnum() and not c.isspace())
    sample_len = min(len(text), 1000)

    if sample_len == 0:
        return 0

    symbol_ratio = non_alnum / sample_len

    # Higher symbol ratio = more code-like = fewer chars per token
    if symbol_ratio > 0.25:
        chars_per_token = 3.5  # Code/logs
    elif symbol_ratio > 0.10:
        chars_per_token = 4.0  # Mixed
    else:
        chars_per_token = 4.5  # Natural language

    return int(len(text) / chars_per_token)


class ActionType(str, Enum):
    """Types of actions an RLM agent can take."""

    EXECUTE_CODE = "execute_code"  # Run Python code to query/filter context
    RECURSIVE_CALL = "recursive_call"  # Make a recursive sub-LLM call
    SPECIALIST_CALL = "specialist_call"  # Run a named specialist analysis
    FINAL_ANSWER = "final_answer"  # Provide final response


@dataclass
class RLMContext:
    """Context available to RLM agent in the REPL environment.

    This is what gets stored as variables that the LLM can query.
    """

    # Deployment context
    error_log: str = ""
    deployment_script: str = ""
    health_check_output: str = ""

    # Historical context
    previous_attempts: list[dict[str, Any]] = field(default_factory=list)  # pyright: ignore[reportUnknownVariableType]
    trajectory_data: dict[str, Any] = field(default_factory=dict)  # pyright: ignore[reportUnknownVariableType]

    # Code analysis context
    dockerfile: str = ""
    docker_compose: str = ""
    readme: str = ""
    analysis_report: str = ""

    # Backtracking support: deploy.sh content before any fixes were applied
    # this session, so the agent can identify and revert regressions.
    original_script: str = ""

    # Pre-computed subagent summaries (populated by HybridCodingAgent).
    # When non-empty, these are available as REPL variables alongside the raw
    # context fields so the LLM can consult them without reading raw logs.
    trajectory_summary: str = ""
    error_summary: str = ""
    script_summary: str = ""
    repo_summary: str = ""

    # Additional ad hoc variables to expose in nested recursive subcalls when a
    # filtered context contains names outside the fixed deployment schema.
    extra_variables: dict[str, Any] = field(default_factory=dict)  # pyright: ignore[reportUnknownVariableType]

    # Metadata
    attempt_number: int = 0
    total_tokens_used: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "error_log": self.error_log,
            "deployment_script": self.deployment_script,
            "health_check_output": self.health_check_output,
            "previous_attempts": self.previous_attempts,
            "trajectory_data": self.trajectory_data,
            "dockerfile": self.dockerfile,
            "docker_compose": self.docker_compose,
            "readme": self.readme,
            "analysis_report": self.analysis_report,
            "original_script": self.original_script,
            "trajectory_summary": self.trajectory_summary,
            "error_summary": self.error_summary,
            "script_summary": self.script_summary,
            "repo_summary": self.repo_summary,
            "extra_variables": self.extra_variables,
            "attempt_number": self.attempt_number,
            "total_tokens_used": self.total_tokens_used,
        }

    def get_summary(self) -> str:
        """Get a summary of available context (for LLM prompt)."""
        original_note = (
            f"- original_script: str ({len(self.original_script)} chars) "
            "← deploy.sh before any fixes this session; use for backtracking"
            if self.original_script
            else "- original_script: str (0 chars) ← not available"
        )

        has_summaries = any(
            [
                self.trajectory_summary,
                self.error_summary,
                self.script_summary,
                self.repo_summary,
            ]
        )
        summaries_section = ""
        if has_summaries:
            summaries_section = (
                "\nPre-computed subagent summaries (read these first — they "
                "distil the raw context into actionable insights):\n"
                f"- trajectory_summary: str ({len(self.trajectory_summary)} chars)"
                " ← what has been tried, recurring error patterns\n"
                f"- error_summary: str ({len(self.error_summary)} chars)"
                " ← key errors and root cause from logs\n"
                f"- script_summary: str ({len(self.script_summary)} chars)"
                " ← what is likely wrong in the deploy script\n"
                f"- repo_summary: str ({len(self.repo_summary)} chars)"
                " ← deployment constraints from Dockerfile/compose/README\n"
            )

        extra_variables_section = ""
        if self.extra_variables:
            lines: list[str] = []
            for key, value in sorted(self.extra_variables.items()):
                typename = type(value).__name__
                size_hint = f"{len(str(value))} chars" if isinstance(value, str) else typename
                lines.append(f"- {key}: {typename} ({size_hint})")
            extra_variables_section = (
                "\nExtra context variables loaded for this recursive task:\n" + "\n".join(lines) + "\n"
            )

        return f"""Available context variables in REPL environment:

- error_log: str ({len(self.error_log)} chars)
- deployment_script: str ({len(self.deployment_script)} chars)
- health_check_output: str ({len(self.health_check_output)} chars)
- previous_attempts: list[Dict] ({len(self.previous_attempts)} attempts)
- trajectory_data: Dict (run_id: {self.trajectory_data.get("metadata", {}).get("run_id", "N/A")})
- dockerfile: str ({len(self.dockerfile)} chars)
- docker_compose: str ({len(self.docker_compose)} chars)
- readme: str ({len(self.readme)} chars)
- analysis_report: str ({len(self.analysis_report)} chars)
{original_note}
{summaries_section}
{extra_variables_section}
Metadata:
- attempt_number: {self.attempt_number}
- total_tokens_used: {self.total_tokens_used}

You can query these variables using Python code, e.g.:
- re.search(r'Error: (.*)', error_log)
- [a for a in previous_attempts if a.get('exit_code') != 0]
- error_log.split('\\n')[-50:]  # Last 50 lines
- validate_file_refs(deployment_script, cwd)  # Check for missing file paths
"""


@dataclass
class RLMCall:
    """Record of a single RLM action (code execution or recursive call)."""

    action_type: ActionType
    depth: int
    input_prompt: str
    code_or_subtask: str
    output: str
    tokens_saved: int = 0  # Est. tokens saved vs feeding full context
    timestamp: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for trajectory recording."""
        return {
            "action_type": self.action_type.value,
            "depth": self.depth,
            "input_prompt": self.input_prompt,
            "code_or_subtask": self.code_or_subtask,
            "output": self.output,
            "tokens_saved": self.tokens_saved,
            "timestamp": self.timestamp,
        }


def _validate_file_refs(script: str, cwd: str) -> str:
    """Check that file paths referenced in shell commands actually exist.

    Scans ``chmod``, ``cp``/``mv`` source, ``source``/``.`` built-in, and
    ``cat`` operands in *script* and reports any that are missing under *cwd*.
    Variables (``$VAR``), glob wildcards, flags (``-x``), and ``/dev/`` paths
    are skipped.

    Returns a human-readable report listing missing paths, or a single line
    confirming all references are valid.
    """
    import os as _os

    # Each tuple is (pattern, capture-group-index).
    _PATTERNS = [
        (re.compile(r"chmod\s+\S+\s+(\S+)"), 1),
        (re.compile(r"\b(?:cp|mv)\s+(?:-\S+\s+)*(\S+)\s+\S+"), 1),
        (re.compile(r"\bsource\s+(\S+)"), 1),
        (re.compile(r"(?<!\w)\.\s+(\S+)"), 1),
        (re.compile(r"\bcat\s+(\S+)"), 1),
    ]

    missing: list[str] = []
    for line in script.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        for pat, grp in _PATTERNS:
            for m in pat.finditer(stripped):
                path = m.group(grp)
                if path.startswith(("$", "-", "/dev/")) or "*" in path or "?" in path:
                    continue
                full = _os.path.join(cwd, path) if not _os.path.isabs(path) else path
                if not _os.path.exists(full):
                    missing.append(f"MISSING: {path!r}  (line: {stripped!r})")

    if not missing:
        return "All referenced file paths exist."
    return "\n".join(missing)


# Public alias for consumers outside this module.
validate_file_refs = _validate_file_refs


class RLMEnvironment:
    """REPL environment for RLM agents.

    Stores context as variables that the LLM can programmatically query.
    Supports code execution and recursive sub-calls.
    """

    def __init__(
        self,
        context: RLMContext,
        max_recursion_depth: int = 5,
        record_callback: Callable[[RLMCall], None] | None = None,
        cwd: str = "",
        max_result_chars: int = 20_000,
        available_specialists: dict[str, str] | None = None,
        task_prompt: str = "",
        sub_rlm_fn: RecursiveLLMFunction | None = None,
        initial_depth: int = 0,
        shared_call_history: list[RLMCall] | None = None,
    ):
        """Initialize RLM environment.

        Args:
            context: Context to store in REPL variables
            max_recursion_depth: Maximum allowed recursion depth
            record_callback: Optional callback to record RLM calls in trajectory
            cwd: Working directory exposed to the REPL as ``cwd`` variable so the
                model can read/write files (e.g. ``open(cwd + "/.sds/deploy.sh", "w")``)
            max_result_chars: Maximum characters for code execution results before
                truncation. Prevents 500-line logs from filling the next LLM prompt.
            task_prompt: The full task loaded as a REPL variable so the LLM can
                query it programmatically rather than receiving it in the context
                window directly. Faithful to Algorithm 1 in the RLM paper.
            sub_rlm_fn: Callable injected as ``sub_rlm(prompt, context=None)``
                in the REPL namespace so the LLM can invoke sub-LLM calls
                programmatically from within code, e.g. inside loops.
        """
        self.context = context
        self.max_recursion_depth = max_recursion_depth
        self.current_depth = initial_depth
        self.record_callback = record_callback
        self.cwd = cwd
        self.max_result_chars = max_result_chars
        self.available_specialists = dict(available_specialists or {})
        self.task_prompt = task_prompt
        self.sub_rlm_fn = sub_rlm_fn

        # Track all RLM calls for analysis
        self.call_history: list[RLMCall] = shared_call_history if shared_call_history is not None else []

        # Safe namespace for code execution
        self._namespace = self._create_safe_namespace()

        # Names that must never be clobbered by LLM-generated code
        self._reserved_names = frozenset(
            {
                "validate_file_refs",
                "re",
                "json",
                "os",
                "Path",
                "open",
                "len",
                "str",
                "list",
                "dict",
                "print",
                "sub_rlm",
                "task_prompt",
                "last_result",
                "last_recursive_result",
                "last_specialist_result",
                "last_specialist_name",
            }
        )

    def _create_safe_namespace(self) -> dict[str, Any]:
        """Create a restricted namespace for code execution.

        Provides access to context variables, common utilities, and
        restricted filesystem operations so the model can read/write files
        only within the working directory (self.cwd).

        Security restrictions:
        - ``os`` is replaced with a restricted wrapper exposing only path
          utilities (os.path.join, os.path.exists, etc.), and restricted
          versions of os.listdir, os.makedirs, os.walk, os.chmod, and
          os.getcwd that only allow access within the working directory.
        - ``open`` is replaced with a wrapper that validates the resolved
          path is within self.cwd before allowing file operations.
        - ``Path`` is available for path manipulation but not for arbitrary
          I/O outside the working directory.
        """
        import os
        from pathlib import Path
        from types import SimpleNamespace

        # -- Restricted open: only allows access within self.cwd --
        _allowed_root = os.path.realpath(self.cwd) if self.cwd else ""

        def _resolve_in_allowed_root(path: str) -> str:
            """Resolve *path* relative to cwd and reject escapes."""
            resolved: str = os.path.realpath(os.path.join(_allowed_root, str(path)))
            if not _allowed_root or (not resolved.startswith(_allowed_root + os.sep) and resolved != _allowed_root):
                raise PermissionError(f"Access denied: {path!r} resolves outside the working directory")
            return resolved

        def _safe_open(path: str, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
            """open() wrapper that restricts file access to the working directory."""
            resolved = _resolve_in_allowed_root(path)
            return open(resolved, mode, *args, **kwargs)  # pyright: ignore[reportAny]

        def _safe_listdir(path: str = ".") -> list[str]:
            """os.listdir() wrapper restricted to the working directory."""
            resolved = _resolve_in_allowed_root(path)
            return os.listdir(resolved)

        def _safe_makedirs(path: str, *args: Any, **kwargs: Any) -> None:
            """os.makedirs() wrapper restricted to the working directory."""
            resolved = _resolve_in_allowed_root(path)
            return os.makedirs(resolved, *args, **kwargs)

        def _safe_walk(top: str = ".", *args: Any, **kwargs: Any) -> Any:
            """os.walk() wrapper restricted to the working directory."""
            resolved_top = _resolve_in_allowed_root(top)
            for root, dirs, files in os.walk(resolved_top, *args, **kwargs):
                # Prevent traversal via symlinks pointing outside the root.
                dirs[:] = [
                    d
                    for d in dirs
                    if (
                        (candidate := os.path.realpath(os.path.join(root, d))).startswith(_allowed_root + os.sep)
                        or candidate == _allowed_root
                    )
                ]
                yield root, dirs, files

        def _safe_chmod(path: str, mode: int, *args: Any, **kwargs: Any) -> None:
            """os.chmod() wrapper restricted to the working directory."""
            resolved = _resolve_in_allowed_root(path)
            return os.chmod(resolved, mode, *args, **kwargs)

        # -- Restricted os: only safe path utilities and file operations --
        _safe_os = SimpleNamespace(
            path=os.path,
            listdir=_safe_listdir,
            makedirs=_safe_makedirs,
            walk=_safe_walk,
            chmod=_safe_chmod,
            getcwd=lambda: _allowed_root,
            sep=os.sep,
        )

        namespace: dict[str, Any] = {
            # Working directory — use this to build absolute file paths
            "cwd": self.cwd,
            # Context variables (what LLM can query)
            "error_log": self.context.error_log,
            "deployment_script": self.context.deployment_script,
            "health_check_output": self.context.health_check_output,
            "previous_attempts": self.context.previous_attempts,
            "trajectory_data": self.context.trajectory_data,
            "dockerfile": self.context.dockerfile,
            "docker_compose": self.context.docker_compose,
            "readme": self.context.readme,
            "analysis_report": self.context.analysis_report,
            "original_script": self.context.original_script,
            "trajectory_summary": self.context.trajectory_summary,
            "error_summary": self.context.error_summary,
            "script_summary": self.context.script_summary,
            "repo_summary": self.context.repo_summary,
            "extra_variables": self.context.extra_variables,
            "attempt_number": self.context.attempt_number,
            # Utilities — restricted versions to limit filesystem access
            "re": re,
            "json": json,
            "os": _safe_os,
            "Path": Path,
            "open": _safe_open,
            "len": len,
            "str": str,
            "list": list,
            "dict": dict,
            "print": print,
            "enumerate": enumerate,
            "range": range,
            "sorted": sorted,
            "filter": filter,
            "map": map,
            # File validation helper — call this before writing a fixed deploy.sh
            "validate_file_refs": _validate_file_refs,
            # Task prompt as REPL variable (RLM paper Algorithm 1: P loaded into state)
            "task_prompt": self.task_prompt,
            # Holds the full output of the most recent execute_code call
            "last_result": None,
            # Holds full outputs of the most recent recursive/specialist calls
            "last_recursive_result": None,
            "last_specialist_result": None,
            "last_specialist_name": None,
        }

        for key, value in self.context.extra_variables.items():
            namespace[key] = value

        # Sub-RLM callable: inject as a REPL function so the LLM can invoke
        # sub-LLM calls programmatically from within code (e.g. inside loops),
        # matching the RLM paper's design of sub_RLM_M as a REPL-registered fn.
        if self.sub_rlm_fn is not None:
            _fn = self.sub_rlm_fn
            _self = self

            def _sub_rlm(prompt: str, context: dict[str, Any] | None = None) -> str:
                return _self.recursive_call(prompt, filtered_context=context, llm_function=_fn)

            namespace["sub_rlm"] = _sub_rlm
        else:
            namespace["sub_rlm"] = None

        return namespace

    def update_summary(self, specialist: str, summary: str) -> None:
        """Cache a specialist summary into context and the REPL namespace."""
        field_map = {
            "trajectory": "trajectory_summary",
            "error_log": "error_summary",
            "script": "script_summary",
            "repo": "repo_summary",
        }
        field_name = field_map.get(specialist)
        if not field_name:
            raise ValueError(f"Unknown specialist: {specialist}")
        setattr(self.context, field_name, summary)
        self._namespace[field_name] = summary

    def set_repl_value(self, name: str, value: Any) -> None:
        """Persist a value into the REPL namespace for later steps."""
        self._namespace[name] = value

    def get_repl_value(self, name: str) -> Any:
        """Return a value from the REPL namespace."""
        return self._namespace.get(name)

    def describe_value(self, variable_name: str, label: str) -> str:
        """Return compact metadata about a REPL value without inlining it fully."""
        value = self._namespace.get(variable_name)
        value_type = type(value).__name__
        rendered = str(value)
        char_count = len(rendered)
        line_count = rendered.count("\n") + 1 if rendered else 0
        preview_limit = 240
        preview = rendered[:preview_limit]
        if len(rendered) > preview_limit:
            preview += "... [preview truncated]"
        lines = [
            f"{label} stored in `{variable_name}`.",
            f"Type: {value_type}",
            f"Size: {char_count} chars, {line_count} lines"
            if isinstance(value, str)
            else f"Rendered size: {char_count} chars",
            f"Preview:\n{preview}" if preview else "Preview: <empty>",
            f"Use `{variable_name}` in execute_code to inspect the full value.",
        ]
        return "\n".join(lines)

    def execute_code(self, code: str, description: str = "") -> str:
        """Execute Python code in the REPL environment.

        Args:
            code: Python code to execute (e.g., regex query, filtering)
            description: Human-readable description of what the code does

        Returns:
            String representation of execution result

        Raises:
            RuntimeError: If code execution fails
        """
        import time

        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")

        try:
            # Execute code in the REPL namespace. The working directory is
            # available as the ``cwd`` variable (and via the restricted
            # ``os.getcwd()``). We do NOT call os.chdir() because it is
            # process-global and therefore not thread-safe. Subprocess calls
            # in LLM-generated code should use ``cwd=cwd`` instead.
            exec_globals = self._namespace.copy()
            exec_globals["cwd"] = self.cwd  # ensure cwd is always current
            _SAFE_MODULES = frozenset(
                {
                    "re",
                    "json",
                    "math",
                    "string",
                    "textwrap",
                    "collections",
                    "itertools",
                    "functools",
                    "pathlib",
                    "posixpath",
                    "ntpath",
                    "datetime",
                    "time",
                    "copy",
                    "hashlib",
                }
            )

            _PREBOUND_MODULES = {
                "os": exec_globals["os"],
            }

            def _safe_import(name: str, *args: Any, **kwargs: Any) -> Any:
                root_name = name.split(".", 1)[0]
                if root_name in _PREBOUND_MODULES:
                    return _PREBOUND_MODULES[root_name]
                if root_name not in _SAFE_MODULES:
                    raise ImportError(
                        f"Import of {name!r} is not allowed in the sandbox. "
                        "Use prebound modules/objects directly (os, re, json)."
                    )
                return __import__(name, *args, **kwargs)

            exec_globals["__builtins__"] = {
                "__import__": _safe_import,
                "True": True,
                "False": False,
                "None": None,
                "int": int,
                "float": float,
                "str": str,
                "bool": bool,
                "list": list,
                "dict": dict,
                "tuple": tuple,
                "set": set,
                "len": len,
                "range": range,
                "enumerate": enumerate,
                "sorted": sorted,
                "filter": filter,
                "map": map,
                "zip": zip,
                "min": min,
                "max": max,
                "sum": sum,
                "abs": abs,
                "round": round,
                "any": any,
                "all": all,
                "isinstance": isinstance,
                "type": type,
                "hasattr": hasattr,
                "getattr": getattr,
                "setattr": setattr,
                "ValueError": ValueError,
                "TypeError": TypeError,
                "KeyError": KeyError,
                "IndexError": IndexError,
                "RuntimeError": RuntimeError,
                "PermissionError": PermissionError,
                "FileNotFoundError": FileNotFoundError,
                "OSError": OSError,
                "Exception": Exception,
                "StopIteration": StopIteration,
                "print": print,
                "repr": repr,
                "iter": iter,
                "next": next,
                "reversed": reversed,
                "chr": chr,
                "ord": ord,
            }
            exec(code, exec_globals)
            # Restore reserved names the LLM may have clobbered, then
            # write back only non-reserved keys so results persist.
            for name in self._reserved_names:
                if name in self._namespace:
                    exec_globals[name] = self._namespace[name]
            self._namespace.update({k: v for k, v in exec_globals.items() if k not in self._reserved_names})

            # Get the result (last expression value or None)
            # For simplicity, we'll look for a 'result' variable
            result = exec_globals.get("result", "Code executed successfully (no result variable)")

            # Store raw result as REPL variable so it remains accessible in
            # subsequent iterations (RLM paper: data stays in state, not hist).
            self._namespace["last_result"] = result

            # Convert result to string
            output = str(result)

            # Truncate oversized results; full output remains in `last_result`
            if len(output) > self.max_result_chars:
                truncated = len(output) - self.max_result_chars
                output = (
                    output[: self.max_result_chars]
                    + f"\n... [{truncated} chars truncated — full output accessible as `last_result`]"
                )

            # Estimate tokens saved: context filtered programmatically
            # instead of sending full context to LLM
            full_context_tokens = (
                _estimate_tokens(self.context.error_log)
                + _estimate_tokens(self.context.deployment_script)
                + _estimate_tokens(self.context.health_check_output)
            )
            output_tokens = _estimate_tokens(output)
            tokens_saved = max(0, full_context_tokens - output_tokens)

            # Record this call
            call = RLMCall(
                action_type=ActionType.EXECUTE_CODE,
                depth=self.current_depth,
                input_prompt=description,
                code_or_subtask=code,
                output=output,
                tokens_saved=tokens_saved,
                timestamp=timestamp,
            )
            self.call_history.append(call)

            if self.record_callback:
                self.record_callback(call)

            logger.info(f"RLM code execution (depth={self.current_depth}): {description[:100]}")
            logger.debug(f"Code: {code[:200]}")
            logger.debug(f"Result: {output[:200]}")

            return output

        except Exception as e:
            # Broad catch required: exec() runs LLM-generated code that may raise any exception type
            error_msg = f"Code execution failed: {e!s}"
            logger.error(error_msg)

            # Still record the failed call
            call = RLMCall(
                action_type=ActionType.EXECUTE_CODE,
                depth=self.current_depth,
                input_prompt=description,
                code_or_subtask=code,
                output=f"ERROR: {error_msg}",
                tokens_saved=0,
                timestamp=timestamp,
            )
            self.call_history.append(call)

            if self.record_callback:
                self.record_callback(call)

            raise RuntimeError(error_msg) from e

    def recursive_call(
        self,
        sub_prompt: str,
        filtered_context: dict[str, Any] | None = None,
        llm_function: RecursiveLLMFunction | None = None,
    ) -> str:
        """Make a recursive LLM sub-call with filtered context.

        Args:
            sub_prompt: Prompt for the recursive call
            filtered_context: Optional filtered context (if None, uses full context)
            llm_function: Function to call the LLM (must be provided by agent)

        Returns:
            Result from the recursive LLM call

        Raises:
            RuntimeError: If max recursion depth exceeded or no LLM function provided
        """
        import time

        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")

        if self.current_depth >= self.max_recursion_depth:
            error_msg = f"Max recursion depth ({self.max_recursion_depth}) exceeded"
            logger.warning(error_msg)
            return f"[Recursion limit reached] {error_msg}"

        if llm_function is None:
            raise RuntimeError("llm_function must be provided for recursive calls")

        # Increment depth
        self.current_depth += 1

        try:
            # Make the recursive LLM call
            logger.info(f"RLM recursive call (depth={self.current_depth}): {sub_prompt[:100]}")

            llm_params = inspect.signature(llm_function).parameters
            if len(llm_params) >= 2:
                llm_function_with_context = cast("Callable[[str, dict[str, Any] | None], str]", llm_function)
                result = llm_function_with_context(sub_prompt, filtered_context)
            else:
                llm_function_simple = cast("Callable[[str], str]", llm_function)
                result = llm_function_simple(sub_prompt)

            # Estimate tokens saved by using filtered context
            if filtered_context:
                filtered_tokens = sum(_estimate_tokens(str(v)) for v in filtered_context.values())
                full_tokens = sum(_estimate_tokens(str(v)) for v in self.context.to_dict().values())
                tokens_saved = max(0, full_tokens - filtered_tokens)
            else:
                tokens_saved = 0

            # Record this call
            call = RLMCall(
                action_type=ActionType.RECURSIVE_CALL,
                depth=self.current_depth,
                input_prompt=sub_prompt,
                code_or_subtask=json.dumps(filtered_context) if filtered_context else "{}",
                output=result,
                tokens_saved=tokens_saved,
                timestamp=timestamp,
            )
            self.call_history.append(call)

            if self.record_callback:
                self.record_callback(call)

            return result

        finally:
            # Always decrement depth
            self.current_depth -= 1

    def get_statistics(self) -> dict[str, Any]:
        """Get statistics about RLM usage.

        Returns baseline_context_tokens — the estimated prompt-token cost of
        sending the full context in a single LLM call — so that callers with
        access to real litellm token counts (e.g. RecursiveDeploymentAgent) can
        compute accurate savings as ``baseline_context_tokens - actual_prompt_tokens``.
        """
        total_calls = len(self.call_history)
        code_calls = sum(1 for c in self.call_history if c.action_type == ActionType.EXECUTE_CODE)
        recursive_calls = sum(1 for c in self.call_history if c.action_type == ActionType.RECURSIVE_CALL)
        max_depth_reached = max((c.depth for c in self.call_history), default=0)

        # Estimate what a single-call baseline would have spent on prompt tokens.
        baseline_context_tokens = (
            _estimate_tokens(self.context.error_log)
            + _estimate_tokens(self.context.deployment_script)
            + _estimate_tokens(self.context.health_check_output)
        )

        return {
            "total_calls": total_calls,
            "code_executions": code_calls,
            "recursive_calls": recursive_calls,
            "baseline_context_tokens": baseline_context_tokens,
            "max_depth_reached": max_depth_reached,
        }

    def get_system_prompt(self) -> str:
        """Get the RLM system prompt explaining the environment."""
        cwd_line = f"\nWorking directory: {self.cwd}" if self.cwd else ""
        specialist_section = ""
        specialist_format = ""
        if self.available_specialists:
            lines = "\n".join(
                f"  - {name}: {description}" for name, description in sorted(self.available_specialists.items())
            )
            specialist_section = (
                "\nLazy specialists are available for focused analysis. "
                "When you need one, request it explicitly instead of reading large raw context.\n"
                f"{lines}\n"
            )
            specialist_format = f"""
════════════════════════════════════════
FORMAT 3 — Request a named specialist analysis:
════════════════════════════════════════
ACTION: specialist_call
SPECIALIST: <one of: {", ".join(sorted(self.available_specialists))}>
TASK: <focused question for that specialist>
"""
        return f"""You are operating in a Recursive Language Model (RLM) environment.

Instead of receiving the full context directly, the context is stored as variables
in a REPL environment that you can programmatically query and explore.
{cwd_line}
{self.context.get_summary()}
{specialist_section}
The following variables and functions are available in your REPL:
  cwd         — absolute path to the working directory (str)
  open        — Python built-in open() for reading and writing files
  os          — restricted os helper (os.path, os.listdir, os.makedirs, os.walk, os.chmod, os.getcwd)
  Path        — pathlib.Path
  re, json    — standard library modules
  task_prompt — the full task ({len(self.task_prompt)} chars); query programmatically, e.g. task_prompt[:500]
  last_result — full output of the most recent execute_code call (Python object, not just string)
  last_recursive_result — full output of the most recent recursive subcall
  last_specialist_result — full output of the most recent specialist call
  last_specialist_name — name of the most recent specialist used
  sub_rlm(prompt, context=None) — invoke a focused sub-LLM call and return its response as a string;
                call from within code to process slices programmatically, e.g. inside a loop
  All context variables listed above (error_log, deployment_script, etc.)

You MUST respond using EXACTLY one of the available formats below. Do not include any
text before the ACTION: line. Do not wrap your response in markdown code blocks.

════════════════════════════════════════
FORMAT 1 — Execute Python code (read files, write output files, analyse context):
════════════════════════════════════════
ACTION: execute_code
DESCRIPTION: <one-line description of what the code does>
CODE:
<python code; set a variable named `result` to a status string>

Examples:

Read a file from disk:
ACTION: execute_code
DESCRIPTION: Read the Dockerfile
CODE:
result = open(cwd + "/Dockerfile").read()

Write an output file:
ACTION: execute_code
DESCRIPTION: Write deploy.sh to .sds/
CODE:
os.makedirs(cwd + "/.sds", exist_ok=True)
with open(cwd + "/.sds/deploy.sh", "w") as f:
    f.write("#!/bin/bash\\ndocker compose up -d")
os.chmod(cwd + "/.sds/deploy.sh", 0o755)
result = "deploy.sh written"

List directory contents:
ACTION: execute_code
DESCRIPTION: List files in working directory
CODE:
result = os.listdir(cwd)

════════════════════════════════════════
FORMAT 2 — Delegate to a focused sub-call:
════════════════════════════════════════
ACTION: recursive_call
SUBTASK: <focused question or task for the sub-call>
CONTEXT: {{"key": "value"}}

{specialist_format}
════════════════════════════════════════
FORMAT {4 if self.available_specialists else 3} — Provide your final answer (after all required files are written):
════════════════════════════════════════
ACTION: final_answer
ANSWER: <your complete response>

OR

FINAL_VAR: <name of a REPL variable holding the final answer>

════════════════════════════════════════

RULES:
- Every response MUST start with exactly "ACTION: " on the very first line.
- `os`, `re`, and `json` are prebound. Do NOT write `import os`, `import yaml`, or `import difflib`.
- Start by inspecting `task_prompt` and relevant context variables programmatically.
- For long inputs, decide on a chunking or filtering strategy in code before answering.
- Keep intermediate findings in REPL variables/buffers instead of copying large text into chat.
- When semantic analysis over many slices is needed, call `sub_rlm(...)` from within code, especially inside loops.
- Use execute_code to explore the filesystem (os.listdir, open) and to write output files.
- Context variables (error_log, readme, etc.) may be empty — read from disk instead when needed.
- If a cached specialist summary exists (e.g. error_summary), prefer reading it
  before re-requesting the same specialist.
- Write all required output files via execute_code BEFORE responding with final_answer.
- Prefer FINAL_VAR when your final answer already exists in a REPL variable or buffer.

FILE VALIDATION (mandatory when fixing deploy.sh):
- Run validate_file_refs(deployment_script, cwd) early to find paths that do not exist on disk.
- Any line in deploy.sh that references a MISSING path MUST be removed — do not try to create the missing file.
- If original_script is non-empty, diff it against the current deployment_script to find lines
  added by previous fix attempts. If those added lines reference MISSING paths, they are
  regressions — remove them and restore the original lines.
- After writing the corrected script, run validate_file_refs again on the new content
  to confirm no missing references remain.

Current recursion depth: {self.current_depth}/{self.max_recursion_depth}
"""
