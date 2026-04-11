"""AgentCLIDriver — ``AgentDriver`` implementation backed by CLI coding agents.

Always uses ``CLICodingAgent`` (e.g. Claude Code) — no litellm fallback.
KB-specific tools go through the crucible MCP server (stdio transport);
native tools (bash, file, grep) are omitted since the CLI agent has them
built-in.

Structured output for main agents uses a ``submit_answer`` MCP tool that
writes to a result file.  Subagents use prompt-instructed JSON with
``<json>`` tags.

Short-circuit interrupts are delivered via a Unix domain socket: the MCP
server connects and sends a JSON signal; the driver's concurrent monitor
kills the CLI process group immediately.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import os
import re
import shutil
import signal as signal_module
import socket
import tempfile
from typing import Any, TypeVar

from sregym_agents.crucible.agents.base import AgentDriver, AgentResult

T = TypeVar("T")
logger = logging.getLogger(__name__)

# Tools that the CLI agent has natively — don't expose via MCP.
_NATIVE_TOOL_NAMES = frozenset(
    {
        "exec_bash",
        "exec_bash_any",
        "read_file",
        "grep",
        "write_file",
        "str_replace_file",
    }
)


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
    ) -> None:
        if provider not in ("claude", "claude-code", "anthropic"):
            raise ValueError(
                f"AgentCLIDriver currently only supports Claude Code (provider='claude'), got {provider!r}"
            )
        self._provider = provider
        self._model = model
        self._cwd = cwd
        self._binary_path: str | None = None
        self._env: dict[str, str] | None = None

    def _ensure_binary(self) -> tuple[str, dict[str, str]]:
        """Lazily resolve the CLI binary path and environment."""
        if self._binary_path is not None and self._env is not None:
            return self._binary_path, self._env

        from libs.agent_cli.utils import get_interactive_env

        env = get_interactive_env()
        binary = shutil.which("claude", path=env.get("PATH")) or shutil.which("claude")
        if not binary:
            raise RuntimeError("claude binary not found in PATH. Please ensure Claude Code CLI is installed.")
        self._binary_path = binary
        self._env = env
        return binary, env

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
            str(getattr(deps, "shared_file", "")),
            "--model",
            str(getattr(deps, "model_id", self._model)),
            "--iteration",
            str(getattr(deps, "iteration", 1)),
            "--prompt-version",
            prompt_version,
        ]

        if role == "sre":
            if getattr(deps, "lt_summary_file", None):
                args.extend(["--lt-summary-file", str(deps.lt_summary_file)])
            if getattr(deps, "incidents_dir", None):
                args.extend(["--incidents-dir", str(deps.incidents_dir)])
            if getattr(deps, "playbooks_dir", None):
                args.extend(["--playbooks-dir", str(deps.playbooks_dir)])
            if getattr(deps, "mitigation_playbooks_dir", None):
                args.extend(["--mitigation-playbooks-dir", str(deps.mitigation_playbooks_dir)])
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

    def _build_mcp_config_json(self, mcp_server_args: list[str]) -> str:
        """Build the ``--mcp-config`` JSON string for Claude Code."""
        return json.dumps(
            {
                "mcpServers": {
                    "crucible-tools": {
                        "command": "uv",
                        "args": mcp_server_args,
                    },
                },
            }
        )

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
                    "causal_chain, and reflection."
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
    def _extract_result_from_stream_json(output: str) -> str:
        """Extract the final result from Claude Code's stream-json output."""
        result_text: str | None = None
        text_parts: list[str] = []

        for line in output.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
                etype = event.get("type")
                if etype == "result":
                    result_text = event.get("result", "")
                elif etype == "text":
                    text_parts.append(event.get("text", ""))
            except json.JSONDecodeError:
                text_parts.append(line)

        if result_text is not None:
            return result_text
        return "".join(text_parts)

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

        # Fallback: find the largest JSON object in the text
        for m in re.finditer(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", text):
            try:
                data = json.loads(m.group())
                if hasattr(output_type, "model_validate"):
                    return output_type.model_validate(data)  # type: ignore[return-value]
                return output_type(**data)  # type: ignore[return-value]
            except Exception:
                continue
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
        from sregym_agents.crucible.tools._kb_tools import (
            LTMMitigationShortCircuit,
            LTMShortCircuit,
        )

        if signal_data.get("short_circuit"):
            if "confirmed" in signal_data:
                return LTMShortCircuit(
                    confirmed=signal_data["confirmed"],
                    iteration=signal_data.get("iteration", 0),
                    confirmed_slugs=signal_data.get("confirmed_slugs", []),
                )
            if "applied" in signal_data:
                return LTMMitigationShortCircuit(
                    applied=signal_data["applied"],
                    iteration=signal_data.get("iteration", 0),
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
    # Subprocess + signal socket
    # ------------------------------------------------------------------

    def _build_command(
        self,
        binary: str,
        prompt: str,
        mcp_config_json: str | None,
    ) -> list[str]:
        """Build the Claude Code CLI command."""
        cmd = [
            binary,
            "-p",
            "--dangerously-skip-permissions",
            "--output-format",
            "stream-json",
            "--verbose",
            prompt,
        ]
        if self._model:
            cmd.extend(["--model", self._model])
        if mcp_config_json:
            cmd.extend(["--mcp-config", mcp_config_json, "--strict-mcp-config"])
        return cmd

    @staticmethod
    def _kill_process_group(process: asyncio.subprocess.Process) -> None:
        """Send SIGTERM to the subprocess's process group."""
        if process.returncode is not None:
            return
        try:
            pgid = os.getpgid(process.pid)  # type: ignore[arg-type]
            os.killpg(pgid, signal_module.SIGTERM)
        except (ProcessLookupError, OSError):
            pass

    async def _run_cli_process(
        self,
        cmd: list[str],
        timeout: int,
        env: dict[str, str],
        signal_socket_path: str | None = None,
    ) -> tuple[str, dict[str, Any] | None]:
        """Run CLI subprocess with optional signal socket monitoring.

        Returns ``(stdout_text, signal_data_or_none)``.  When a signal
        is received the CLI process group is killed immediately.
        """
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self._cwd,
            env=env,
            start_new_session=True,
        )

        signal_data: dict[str, Any] | None = None
        monitor_task: asyncio.Task[None] | None = None

        if signal_socket_path:

            async def _monitor_signal() -> None:
                nonlocal signal_data
                server_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                try:
                    server_sock.setblocking(False)
                    server_sock.bind(signal_socket_path)
                    server_sock.listen(1)
                    loop = asyncio.get_running_loop()
                    conn, _ = await loop.sock_accept(server_sock)
                    chunks: list[bytes] = []
                    while True:
                        chunk = await loop.sock_recv(conn, 4096)
                        if not chunk:
                            break
                        chunks.append(chunk)
                    conn.close()
                    if chunks:
                        signal_data = json.loads(b"".join(chunks).decode())
                        self._kill_process_group(process)
                except asyncio.CancelledError:
                    pass
                except Exception as exc:
                    logger.debug("Signal monitor error: %s", exc)
                finally:
                    server_sock.close()

            monitor_task = asyncio.create_task(_monitor_signal())

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            self._kill_process_group(process)
            await process.wait()
            if monitor_task:
                monitor_task.cancel()
            raise

        if monitor_task:
            # Give the monitor a moment to finish if a signal just arrived
            await asyncio.sleep(0.05)
            monitor_task.cancel()
            try:
                await monitor_task
            except asyncio.CancelledError:
                pass

        output = stdout.decode() if stdout else ""

        if process.returncode != 0 and not signal_data:
            stderr_text = stderr.decode()[:500] if stderr else ""
            logger.warning(
                "CLI agent exited with code %d: %s",
                process.returncode,
                stderr_text,
            )

        return output, signal_data

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

        binary, env = self._ensure_binary()

        # ----- MCP server setup -----
        signal_socket_path: str | None = None
        result_file_path: str | None = None
        mcp_config_json: str | None = None

        role = self._determine_tool_role(deps)
        if role and deps is not None:
            result_fd, result_file_path = tempfile.mkstemp(
                suffix=".json",
                prefix="crucible_result_",
            )
            os.close(result_fd)

            signal_dir = tempfile.mkdtemp(prefix="crucible_signal_")
            signal_socket_path = os.path.join(signal_dir, "signal.sock")

            mcp_args = self._build_mcp_server_args(
                deps,
                role,
                signal_socket_path=signal_socket_path,
                result_file_path=result_file_path,
            )
            mcp_config_json = self._build_mcp_config_json(mcp_args)

        # ----- Prompt -----
        full_prompt = self._build_prompt(
            prompt,
            system_prompt,
            output_type,
            has_result_file=result_file_path is not None,
        )

        # ----- Command -----
        cmd = self._build_command(binary, full_prompt, mcp_config_json)
        effective_timeout = timeout or 900

        # ----- Execute -----
        try:
            raw_output, signal_data = await self._run_cli_process(
                cmd,
                effective_timeout,
                env,
                signal_socket_path=signal_socket_path,
            )
        except (asyncio.TimeoutError, RuntimeError) as exc:
            logger.warning("CLI agent failed: %s", exc)
            return AgentResult(completed=False)
        finally:
            if signal_socket_path:
                import shutil as _shutil

                try:
                    _shutil.rmtree(os.path.dirname(signal_socket_path), ignore_errors=True)
                except OSError:
                    pass

        # ----- Signal check -----
        if signal_data and signal_data.get("short_circuit"):
            if result_file_path:
                try:
                    os.unlink(result_file_path)
                except OSError:
                    pass
            return AgentResult(
                completed=False,
                interrupt_data=self._reconstruct_interrupt(signal_data),
            )

        # ----- Result file check -----
        if result_file_path:
            try:
                result_data = self._read_result_file(result_file_path)
            finally:
                try:
                    os.unlink(result_file_path)
                except OSError:
                    pass

            if result_data:
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
                if result_data.get("type") == "verdict":
                    # Judge verdict — state is communicated via result file.
                    # The orchestrator checks deps.state; here we pass
                    # through since JudgeAgent will inspect result_data.
                    pass

        # ----- Parse from response text -----
        response_text = self._extract_result_from_stream_json(raw_output)

        if output_type is not str:
            output = self._parse_json_from_text(response_text, output_type)
            if output is not None:
                return AgentResult(output=output, completed=True)
            return AgentResult(completed=True, output=None)

        return AgentResult(
            output=response_text,  # type: ignore[arg-type]
            completed=True,
        )
