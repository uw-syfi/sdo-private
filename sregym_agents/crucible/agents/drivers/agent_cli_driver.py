"""AgentCLIDriver — ``AgentDriver`` backed by ``agentshim`` coding agents.

Uses a provider-agnostic ``CodingAgent`` facade and relies on it to spawn the
underlying CLI, parse the stream-json events, and drive its
``AgentEventHandler`` callbacks.  Crucible MCP tools are exposed to the
CLI via a ``StdioMcpServer`` entry.

Structured output flows through a ``submit_answer`` MCP tool that writes
to a result file; free-form ``str`` output is the generate() return
value.  Subagents without a result file use prompt-instructed JSON with
``<json>`` tags.

Short-circuit interrupts are delivered via the result file: the MCP
server writes ``{"short_circuit": true, ...}`` when an LTM interrupt
fires.  The event handler also watches tool-result events for the same
payload and, on hit, SIGTERMs the CLI's process group to abort the run
early instead of letting it burn tokens until the stage timeout.
"""

from __future__ import annotations

import asyncio
import dataclasses
import importlib
import json
import logging
import os
import re
import signal as signal_module
import subprocess
import tempfile
from pathlib import Path
from typing import Any, TypeVar

from sregym_agents.crucible.agents.base import AgentDriver, AgentResult

T = TypeVar("T")
logger = logging.getLogger(__name__)
_REPO_ROOT = Path(__file__).resolve().parents[4]

# Tools that the CLI agent has natively — don't expose via MCP.
_NATIVE_TOOL_NAMES = frozenset(
    {
        "exec_bash",
        "read_file",
        "grep",
        "write_file",
        "str_replace_file",
    }
)


# MCP tool names whose results can contain a short-circuit payload.
_SHORT_CIRCUIT_TOOLS = frozenset(
    {
        "search_prior_incidents",
        "search_prior_mitigations",
    }
)


def _shared_file_arg(sf: Any) -> str:
    """Extract the path string from a SharedFile for the MCP subprocess CLI.

    The MCP subprocess receives ``--shared-file <path>`` on the command line
    and reconstructs a ``SharedFile`` on its side. SharedFile deliberately
    does not implement ``__fspath__`` or ``__str__`` → path, so we ask for
    the path explicitly via ``display_path()``. Returns the empty string
    when the dependency has no ``shared_file`` attribute (defensive — this
    path is only hit by badly-formed test doubles).
    """
    if sf is None:
        return ""
    if hasattr(sf, "display_path"):
        return sf.display_path()
    return ""


def _coding_agent_event_handler_kwargs(handler: Any) -> dict[str, Any]:
    try:
        agentshim_mod = importlib.import_module("agentshim")
    except ImportError:
        return {"event_handler": handler}
    console_handler_cls = getattr(agentshim_mod, "ConsoleEventHandler", None)
    if console_handler_cls is None:
        return {"event_handler": handler}
    console_handler = console_handler_cls()
    return {"event_handlers": [console_handler, handler]}


class _AgentCLIEventHandler:
    """``AgentEventHandler`` that logs events and fast-kills on short-circuit.

    Serves two roles:

    1. Real-time trajectory logging (tool calls, results, thinking) via
       the standard Python logger.
    2. Short-circuit detection.  When the CLI calls an LTM tool whose
       result is a JSON payload with ``"short_circuit": true``, the
       handler SIGTERMs the CLI's process group.  This aborts the run
       instead of letting the CLI burn tokens until the stage timeout.

    The handler must be wired to the CLI subprocess via
    ``bind_process()``, registered as ``on_process_started`` on
    ``CodingAgent.generate()``.  After a kill, ``short_circuit_fired``
    is ``True`` so callers can distinguish intentional termination from
    a genuine CLI failure.
    """

    def __init__(self, agent_name: str = "") -> None:
        self._prefix = f"[{agent_name}] " if agent_name else ""
        self._turn = 0
        self._process: subprocess.Popen[str] | None = None
        self.short_circuit_fired = False

    # ----- CodingAgent.on_process_started wiring -----

    def bind_process(self, proc: subprocess.Popen[str]) -> None:
        """Record the spawned CLI subprocess so we can kill it on short-circuit."""
        self._process = proc

    # ----- AgentEventHandler protocol -----

    def on_thinking(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        if len(text) > 300:
            text = text[:300] + "..."
        logger.info("%sassistant: %s", self._prefix, text)

    def on_tool_call(self, tool: str, args: dict[str, Any] | str | None = None) -> None:
        self._turn += 1
        params_repr = json.dumps(args, default=str) if isinstance(args, dict) else (args or "")
        if len(params_repr) > 300:
            params_repr = params_repr[:300] + "..."
        logger.info(
            "%sTurn %d: tool_use(%s) %s",
            self._prefix,
            self._turn,
            tool,
            params_repr,
        )

    def on_tool_result(
        self,
        tool: str,
        stdout: str = "",
        stderr: str = "",
        exit_code: int | None = None,
        duration: float | None = None,
    ) -> None:
        output = stdout or stderr or ""
        # Log before potentially killing, so the short-circuiting result
        # still appears in the trajectory.
        log_output = output[:300] + "..." if len(output) > 300 else output
        dur = f" ({duration:.2f}s)" if duration is not None else ""
        logger.info("%stool_result(%s)%s: %s", self._prefix, tool, dur, log_output)

        if tool in _SHORT_CIRCUIT_TOOLS and output and self._looks_like_short_circuit(output):
            self._kill_process_group()

    # ----- helpers -----

    @staticmethod
    def _looks_like_short_circuit(output: str) -> bool:
        try:
            data: Any = json.loads(output)
        except (json.JSONDecodeError, TypeError):
            return False
        return isinstance(data, dict) and bool(data.get("short_circuit"))  # pyright: ignore[reportUnknownMemberType,reportUnknownArgumentType]

    def _kill_process_group(self) -> None:
        if self.short_circuit_fired or self._process is None:
            return
        self.short_circuit_fired = True
        logger.info("%sshort-circuit detected — terminating CLI process group", self._prefix)
        if self._process.poll() is not None:
            return
        try:
            pgid = os.getpgid(self._process.pid)
            os.killpg(pgid, signal_module.SIGTERM)
        except (ProcessLookupError, OSError) as exc:
            logger.debug("Failed to kill CLI process group: %s", exc)


class AgentCLIDriver(AgentDriver):
    """``AgentDriver`` backed by a CLI coding agent (Claude Code).

    Constructor parameters:
        provider: CLI agent provider name (currently only ``"claude"``
            is supported).
        model: Model ID string passed to the CLI agent.
        cwd: Working directory for the CLI agent subprocess.
    """

    def __init__(
        self,
        provider: str = "claude",
        model: str = "claude-sonnet-4-6",
        cwd: str | None = None,
        sandbox: bool | Any = False,
    ) -> None:
        if provider not in ("claude", "claude-code", "anthropic"):
            raise ValueError(
                f"AgentCLIDriver currently only supports Claude Code (provider='claude'), got {provider!r}"
            )
        self._provider = provider
        self._model = model
        self._cwd = cwd
        self._sandbox = sandbox

    # ------------------------------------------------------------------
    # MCP server configuration
    # ------------------------------------------------------------------

    @staticmethod
    def _determine_tool_role(deps: Any) -> str | None:
        """Determine the MCP server tool role from the deps type."""
        if deps is None:
            return None
        cls_name = type(deps).__name__
        if cls_name == "SREDeps":
            return "sre"
        if cls_name == "JudgeDeps":
            return "judge"
        return None

    def _build_mcp_server_args(
        self,
        deps: Any,
        role: str,
        *,
        signal_socket_path: str | None = None,
        result_file_path: str | None = None,
    ) -> list[str]:
        """Build CLI args for the crucible MCP server subprocess."""
        prompt_version = "v2"
        renderer = getattr(deps, "renderer", None)
        if renderer is not None:
            prompt_version = getattr(renderer, "version", "v2")

        args: list[str] = [
            "--directory",
            str(_REPO_ROOT),
            "run",
            "python",
            "-m",
            "sregym_agents.crucible.tools.mcp_server",
            "--tools",
            role,
            "--namespace",
            str(getattr(deps, "namespace", "default")),
            "--stage",
            str(getattr(deps, "stage", "diagnosis")),
            "--shared-file",
            _shared_file_arg(getattr(deps, "shared_file", None)),
            "--model",
            str(getattr(deps, "model_id", self._model)),
            "--iteration",
            str(getattr(deps, "iteration", 1)),
            "--prompt-version",
            prompt_version,
        ]

        # Forward the experiment cwd so the MCP-side subagent driver can
        # match the parent's sandbox + spawn cwd. The MCP subprocess itself
        # is launched with --directory _REPO_ROOT, so it cannot recover the
        # exp cwd from os.getcwd().
        if self._cwd:
            args.extend(["--exp-cwd", str(self._cwd)])

        if role == "sre":
            if getattr(deps, "kb_view_dir", None):
                args.extend(["--kb-view-dir", str(deps.kb_view_dir)])
            ltm_budget = getattr(deps, "ltm_call_budget", None)
            if ltm_budget is not None:
                args.extend(["--ltm-call-budget", str(ltm_budget)])
            if getattr(deps, "enable_ltm_verified_direct_submit", False):
                args.append("--enable-ltm-verified-direct-submit")

        if role == "judge":
            if getattr(deps, "submit_mcp_url", None):
                args.extend(["--submit-mcp-url", str(deps.submit_mcp_url)])
            hypothesis = getattr(deps, "hypothesis_text", None)
            if hypothesis:
                args.extend(["--hypothesis-text", str(hypothesis)])

        if signal_socket_path:
            args.extend(["--signal-socket", signal_socket_path])
        if result_file_path:
            args.extend(["--result-file", result_file_path])

        args.extend(["--backend", "agent-cli", "--provider", self._provider])
        return args

    # ------------------------------------------------------------------
    # Prompt construction
    # ------------------------------------------------------------------

    def _build_prompt(
        self,
        prompt: str,
        system_prompt: str,
        output_type: type,
        has_result_file: bool,
    ) -> str:
        """Build the full prompt with system instructions and output format."""
        parts: list[str] = []
        if system_prompt:
            parts.append(f"<system>\n{system_prompt}\n</system>\n")
        parts.append(prompt)

        if output_type is not str:
            if has_result_file:
                parts.append(
                    "\n\nWhen you have completed your investigation and are "
                    "ready to submit your final answer, call the "
                    "`submit_answer` tool with your answer, justification, "
                    "and causal_chain."
                )
            else:
                schema = self._get_json_schema(output_type)
                parts.append(
                    "\n\n<output_format>\n"
                    "You MUST output your final answer as a JSON object "
                    "matching this schema:\n"
                    f"```json\n{json.dumps(schema, indent=2)}\n```\n"
                    "Output ONLY the JSON object, no other text. "
                    "Wrap it in <json> tags:\n"
                    "<json>{...}</json>\n"
                    "</output_format>"
                )
        return "\n".join(parts)

    @staticmethod
    def _get_json_schema(output_type: type) -> dict[str, Any]:
        """Get JSON schema for a pydantic model or dataclass."""
        if hasattr(output_type, "model_json_schema"):
            return output_type.model_json_schema()  # pyright: ignore[reportUnknownMemberType,reportUnknownVariableType]
        if hasattr(output_type, "__dataclass_fields__"):
            py_to_json: dict[str, str] = {
                "str": "string",
                "int": "integer",
                "float": "number",
                "bool": "boolean",
                "list": "array",
            }
            type_to_json: dict[type, str] = {
                str: "string",
                int: "integer",
                float: "number",
                bool: "boolean",
                list: "array",
            }
            fields: dict[str, Any] = {}
            for f in dataclasses.fields(output_type):  # pyright: ignore[reportUnknownMemberType,reportUnknownArgumentType]
                if isinstance(f.type, type):
                    json_type = type_to_json.get(f.type, "string")
                elif isinstance(f.type, str):
                    json_type = py_to_json.get(f.type, "string")
                else:
                    json_type = "string"
                fields[f.name] = {"type": json_type}
            return {"type": "object", "properties": fields}
        return {"type": "string"}

    # ------------------------------------------------------------------
    # Output parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_json_from_text(text: str, output_type: type[T]) -> T | None:
        """Parse structured output from response text (``<json>`` tags or raw)."""
        # Try <json>...</json> tags first
        match = re.search(r"<json>\s*(.*?)\s*</json>", text, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group(1))
                if hasattr(output_type, "model_validate"):
                    return output_type.model_validate(data)  # type: ignore[return-value]
                return output_type(**data)  # type: ignore[return-value]
            except Exception:
                pass

        # Fallback: find JSON objects via brace balancing
        i = 0
        while i < len(text):
            if text[i] == "{":
                depth = 0
                start = i
                in_string = False
                escape = False
                j = i
                for j in range(i, len(text)):
                    ch = text[j]
                    if escape:
                        escape = False
                        continue
                    if ch == "\\" and in_string:
                        escape = True
                        continue
                    if ch == '"' and not escape:
                        in_string = not in_string
                        continue
                    if in_string:
                        continue
                    if ch == "{":
                        depth += 1
                    elif ch == "}":
                        depth -= 1
                        if depth == 0:
                            candidate = text[start : j + 1]
                            try:
                                data = json.loads(candidate)
                                if hasattr(output_type, "model_validate"):
                                    return output_type.model_validate(data)  # type: ignore[return-value]
                                return output_type(**data)  # type: ignore[return-value]
                            except Exception:
                                break
                i = j + 1 if depth == 0 else i + 1
            else:
                i += 1
        return None

    @staticmethod
    def _read_result_file(path: str) -> dict[str, Any] | None:
        """Read and parse a JSON result file."""
        try:
            with open(path) as f:
                content = f.read().strip()
            if content:
                return json.loads(content)  # type: ignore[no-any-return]
        except (OSError, json.JSONDecodeError):
            pass
        return None

    @staticmethod
    def _reconstruct_interrupt(signal_data: dict[str, Any]) -> Any:
        """Reconstruct an interrupt exception from signal data."""
        from sregym_agents.crucible.tools import LTMShortCircuit

        if signal_data.get("short_circuit") and "confirmed" in signal_data:
            return LTMShortCircuit(
                confirmed=signal_data["confirmed"],
                iteration=signal_data.get("iteration", 0),
                confirmed_slugs=signal_data.get("confirmed_slugs", []),
            )
        return signal_data

    @staticmethod
    def _parse_result_data(data: dict[str, Any], output_type: type[T]) -> T:
        """Parse structured output from a result-file dict."""
        answer_data = data.get("data", data)
        if hasattr(output_type, "model_validate"):
            return output_type.model_validate(answer_data)  # type: ignore[return-value]
        return output_type(**answer_data)  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # Coding-agent instantiation
    # ------------------------------------------------------------------

    def _build_coding_agent(
        self,
        mcp_args: list[str] | None,
        handler: _AgentCLIEventHandler,
    ) -> Any:
        """Construct a provider-routed ``CodingAgent`` for this provider."""
        from agentshim.mcp_config import StdioMcpServer

        from agentshim import CodingAgent

        mcp_servers: list[StdioMcpServer] = []
        if mcp_args is not None:
            mcp_servers.append(StdioMcpServer(name="crucible-tools", command="uv", args=list(mcp_args)))

        try:
            return CodingAgent(
                provider=self._provider,
                model=self._model,
                **_coding_agent_event_handler_kwargs(handler),
                mcp_servers=mcp_servers,
                sandbox=self._sandbox,
            )
        except ValueError as exc:
            raise RuntimeError(f"No CodingAgent available for provider={self._provider!r}: {exc}") from exc

    async def run(
        self,
        *,
        prompt: str,
        system_prompt: str = "",
        tools: list[Any] | None = None,
        output_type: type[T] = str,  # type: ignore[assignment]
        agent_name: str = "",
        timeout: int | None = None,
        model_settings: dict[str, Any] | None = None,
        message_history: list[Any] | None = None,
        usage_collector: Any | None = None,
        **kwargs: Any,
    ) -> AgentResult[T]:
        """Run a CLI coding agent and return an ``AgentResult``.

        Backend-specific kwargs:
            deps: ``SREDeps`` or ``JudgeDeps`` — used to build MCP server
                args (tools run in a separate MCP server process, not
                in-process).
        """
        deps = kwargs.pop("deps", None)
        # run_ctx is pydantic-ai-specific; ignore it
        kwargs.pop("run_ctx", None)

        # ----- MCP server setup -----
        result_file_path: str | None = None
        mcp_args: list[str] | None = None

        role = self._determine_tool_role(deps)
        if role and deps is not None:
            result_fd, result_file_path = tempfile.mkstemp(
                suffix=".json",
                prefix="crucible_result_",
            )
            os.close(result_fd)
            mcp_args = self._build_mcp_server_args(
                deps,
                role,
                result_file_path=result_file_path,
            )

        # ----- Prompt -----
        full_prompt = self._build_prompt(
            prompt,
            system_prompt,
            output_type,
            has_result_file=result_file_path is not None,
        )

        # ----- Coding agent + event handler -----
        handler = _AgentCLIEventHandler(agent_name)
        coding_agent = self._build_coding_agent(mcp_args, handler)
        effective_timeout = timeout or 900

        # ----- Execute -----
        try:
            response_text: str | None = None
            try:
                response_text = await asyncio.to_thread(
                    coding_agent.generate,
                    full_prompt,
                    cwd=self._cwd,
                    timeout=effective_timeout,
                    on_process_started=handler.bind_process,
                )
            except (asyncio.TimeoutError, subprocess.TimeoutExpired, RuntimeError) as exc:
                # A short-circuit kill propagates up as RuntimeError (CLI
                # exited nonzero after SIGTERM).  Swallow it here — the
                # MCP server already wrote the short-circuit payload to
                # the result file, which the block below will pick up.
                if not handler.short_circuit_fired:
                    logger.warning("CLI agent failed: %s", exc)
                    # Still fall through to result-file check in case the
                    # MCP server wrote something useful before the crash.

            # ----- Result file check -----
            has_result = False
            if result_file_path:
                result_data = self._read_result_file(result_file_path)
                if result_data:
                    has_result = True
                    if result_data.get("short_circuit"):
                        return AgentResult(
                            completed=False,
                            interrupt_data=self._reconstruct_interrupt(result_data),
                        )
                    if result_data.get("type") == "answer" and output_type is not str:
                        try:
                            output = self._parse_result_data(result_data, output_type)
                            return AgentResult(output=output, completed=True)
                        except Exception as exc:
                            logger.warning("Failed to parse result file: %s", exc)
                    # type == "verdict" is handled by JudgeAgent via deps.state

            # No response and no result file → run really failed.
            if response_text is None:
                return AgentResult(completed=False)

            # ----- Parse from response text -----
            if output_type is not str:
                output = self._parse_json_from_text(response_text, output_type)
                if output is not None:
                    return AgentResult(output=output, completed=True)
                if not has_result:
                    logger.warning(
                        "CLI agent produced no structured output and no result file — treating as failed run."
                    )
                    return AgentResult(completed=False)
                return AgentResult(completed=True, output=None)

            return AgentResult(
                output=response_text,  # type: ignore[arg-type]
                completed=True,
            )
        finally:
            if result_file_path:
                try:
                    os.unlink(result_file_path)
                except OSError:
                    pass
