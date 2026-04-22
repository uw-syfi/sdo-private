"""Minimal SRE Gym agent that wraps a ``agentshim`` CLI agent.

Per stage, spawns one ``CodingAgent`` from ``agentshim.AGENT_REGISTRY``
with the sregym ``/submit`` MCP server wired in. The wrapped CLI itself
calls the ``submit`` tool when it has reached a conclusion — this driver
just builds the prompt, launches the CLI, and after it returns checks
whether the conductor advanced past the current stage.

No judge, no knowledge base, no deferred cleanup — this agent is a
baseline / smoke test. Contrast with ``sregym_agents.crucible.driver``.

Provider support: submission is done by the wrapped CLI through the MCP
server, so only providers whose ``agentshim`` class accepts
``mcp_servers`` are usable — currently ``claude`` and ``codex``. Gemini
and Opencode raise ``ValueError`` inside their constructor when
``mcp_servers`` is non-empty (see
``agentshim/gemini.py`` and ``agentshim/opencode.py``).
"""

from __future__ import annotations

import argparse
import functools
import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from libs.sregym_lib.conductor import (
    get_api_base,
    get_app_info,
    get_current_stage_sync,
    get_planned_stages,
    get_problem_id,
    poll_stage_sync,
    wait_for_stages_or_last_seen_sync,
)
from libs.sregym_lib.schema import READY_STAGES, TERMINAL_STAGES

if TYPE_CHECKING:
    from collections.abc import Callable

    from agentshim import CodingAgent

logger = logging.getLogger(__name__)

# How long to wait after each stage for the conductor to finish grading
# and advance. Overridable via monkeypatch in tests so the
# "never-submitted" path doesn't block a unit test for 5 minutes.
_POST_STAGE_TIMEOUT_S = 300

_SUBMIT_MCP_SERVER_NAME = "sregym"
_MEMORY_MCP_SERVER_NAME = "incident_memory"


# --- Pure helpers ----------------------------------------------------------


@functools.lru_cache(maxsize=1)
def _jinja_env() -> Environment:
    """Lazily build the Jinja2 environment for prompt templates.

    Cached as a module-level singleton via ``lru_cache(maxsize=1)`` so the
    ``FileSystemLoader`` and template parsing only happen on first use,
    not on every prompt build.
    """
    return Environment(
        loader=FileSystemLoader(str(Path(__file__).parent / "prompts")),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
    )


def _build_prompt(
    planned_stages: list[str],
    app_info: dict[str, Any],
    *,
    autonomous: bool = False,
    memory_mcp_server_name: str | None = None,
    memory_store_only: bool = False,
) -> str:
    """Render the single-session prompt handed to the wrapped CLI agent.

    One CLI session handles every planned stage. In the default mode, the
    agent calls the single ``submit`` tool once per stage and the conductor
    routes each call to whichever stage is currently active — the tool
    response carries the grading verdict. In autonomous mode (``autonomous=True``),
    the agent instead calls per-stage ``submit_diagnosis`` / ``submit_mitigation``
    tools that return only a neutral acknowledgement, so the agent must
    self-verify by inspecting the cluster before submitting.

    The prompt deliberately omits the benchmark ``problem_id``: SREGym
    problem IDs are descriptive (``incorrect_image``,
    ``missing_env_variable_astronomy_shop``, ``liveness_probe_misconfiguration_*``)
    and including them would leak the answer to the agent. App name and
    namespace are kept because they're observable from the cluster anyway.
    """
    template_name = "session_autonomous.j2" if autonomous else "session.j2"
    return (
        _jinja_env()
        .get_template(template_name)
        .render(
            planned_stages=list(planned_stages),
            app_name=app_info.get("app_name", "<unknown>"),
            namespace=app_info.get("namespace", "<unknown>"),
            submit_mcp_server_name=_SUBMIT_MCP_SERVER_NAME,
            memory_mcp_server_name=memory_mcp_server_name,
            memory_store_only=memory_store_only,
        )
    )


# --- Conductor I/O ---------------------------------------------------------
# Conductor HTTP client lives in libs/sregym_lib/conductor.py — this driver
# only owns the cli_agent-specific orchestration below.


# --- Agent construction ----------------------------------------------------


def _build_memory_mcp_server(store_path: str, merge_model: str | None = None) -> Any:
    """Build a ``StdioMcpServer`` that launches the incident memory server as a subprocess."""
    from agentshim.mcp_config import StdioMcpServer

    args = [
        "run",
        "python",
        "-m",
        "sregym_agents.cli_agent.memory_server",
        "--store-path",
        store_path,
    ]
    if merge_model:
        args += ["--merge-model", merge_model]
    return StdioMcpServer(name=_MEMORY_MCP_SERVER_NAME, command="uv", args=args, env={})


def _build_memory_mcp_server_http(port: int, store_only: bool = False) -> Any:
    """Build an ``HttpMcpServer`` pointing to the shared memory daemon."""
    from agentshim.mcp_config import HttpMcpServer

    url = f"http://localhost:{port}/sse"
    if store_only:
        url += "?store_only=1"
    return HttpMcpServer(name=_MEMORY_MCP_SERVER_NAME, url=url)


def _default_agent_factory(
    provider: str,
    model: str,
    submit_mcp_url: str,
    extra_mcp_servers: list[Any] | None = None,
) -> CodingAgent:
    """Build a ``CodingAgent`` with the sregym submit MCP server wired in.

    Raises ``ValueError`` if the requested provider does not support MCP
    (e.g. ``gemini``/``opencode``) — the underlying class raises it from
    its ``__init__`` when ``mcp_servers`` is non-empty.
    """
    # Deferred imports: keeps `--help` fast and avoids triggering heavy
    # litellm/claude-sdk loads when the driver is imported by tests.
    # Importing `agentshim.base` transitively runs `agentshim/__init__.py`,
    # which imports each CLI subclass and populates AGENT_REGISTRY as a side effect.
    from agentshim.base import AGENT_REGISTRY
    from agentshim.mcp_config import HttpMcpServer

    try:
        cls = AGENT_REGISTRY[provider.lower()]
    except KeyError as exc:
        raise ValueError(
            f"Unknown cli_agent provider {provider!r}. Available: {sorted(AGENT_REGISTRY.keys())}"
        ) from exc
    mcp_servers: list[Any] = [HttpMcpServer(name=_SUBMIT_MCP_SERVER_NAME, url=submit_mcp_url)]
    if extra_mcp_servers:
        mcp_servers.extend(extra_mcp_servers)
    return cls(model=model, mcp_servers=mcp_servers)


# --- Entry point -----------------------------------------------------------


def _load_toml_agent_config() -> dict[str, Any]:
    """Decode the ``[agent.cli_agent]`` TOML block forwarded by the launcher.

    The sregym launcher (``scripts/run_sregym.py`` → ``config_to_env``)
    serializes the per-agent TOML block as a JSON string in
    ``SREGYM_EXPERIMENT_AGENT_CONFIG``. An empty/missing var is a valid
    "use defaults" signal; we don't error.
    """
    raw = os.getenv("SREGYM_EXPERIMENT_AGENT_CONFIG")
    if not raw:
        return {}
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning("SREGYM_EXPERIMENT_AGENT_CONFIG is not valid JSON (%s); ignoring", exc)
        return {}
    if not isinstance(decoded, dict):
        logger.warning(
            "SREGYM_EXPERIMENT_AGENT_CONFIG must decode to an object; got %s — ignoring",
            type(decoded).__name__,
        )
        return {}
    return cast("dict[str, Any]", decoded)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI args, using ``[agent.cli_agent]`` TOML values as defaults.

    Precedence: explicit CLI flag > TOML (``SREGYM_EXPERIMENT_AGENT_CONFIG``)
    > env var (``MODEL_ID``) > hardcoded default.
    """
    toml_cfg = _load_toml_agent_config()

    parser = argparse.ArgumentParser(
        description="Minimal SRE Gym agent that wraps a agentshim CLI.",
    )
    parser.add_argument(
        "--provider",
        default=toml_cfg.get("provider", "claude"),
        help=(
            "CLI provider from agentshim.AGENT_REGISTRY "
            "(must support mcp_servers: currently claude or codex; default: claude)"
        ),
    )
    parser.add_argument(
        "--model",
        default=toml_cfg.get("model", os.getenv("MODEL_ID", "claude-sonnet-4-6")),
        help="Model identifier passed to the CLI (default: $MODEL_ID or claude-sonnet-4-6)",
    )
    parser.add_argument(
        "--logs-dir",
        default=toml_cfg.get("logs_dir"),
        help="Directory to write a per-run results JSON (optional)",
    )
    parser.add_argument(
        "--timeout-sec",
        type=int,
        default=int(toml_cfg.get("timeout_sec", 2000)),
        help=(
            "Whole-session CLI timeout in seconds. One CLI run covers all "
            "planned stages (diagnosis + mitigation), so this is the budget "
            "for the entire problem (default: 2000)"
        ),
    )
    parser.add_argument(
        "--memory-store",
        default=toml_cfg.get("memory_store"),
        help=(
            "Path to the SQLite incident memory store. "
            "When set, the agent gains recall_incident / store_incident MCP tools "
            "backed by the incident_memory server (optional)."
        ),
    )
    parser.add_argument(
        "--memory-merge-model",
        default=toml_cfg.get("memory_merge_model"),
        help=(
            "LLM model id (litellm) used to merge duplicate incidents when storing. "
            "Requires --memory-store. If omitted, duplicate incidents are skipped without merging."
        ),
    )
    parser.add_argument(
        "--memory-port",
        type=int,
        default=toml_cfg.get("memory_port"),
        help=(
            "Port of the shared incident memory HTTP/SSE daemon (started by run_sregym.py "
            "before_benchmark hook). When set, agents connect to the daemon instead of "
            "spawning a per-agent stdio subprocess. Takes precedence over --memory-store."
        ),
    )
    parser.add_argument(
        "--memory-store-only",
        action="store_true",
        default=bool(toml_cfg.get("memory_store_only", False)),
        help=(
            "When set, only store_incident is available — recall_incident is disabled. "
            "Useful for KB-building stages where agents should not read from prior memory."
        ),
    )
    # No-op flags accepted for compatibility with sregym's agent launcher
    # (`bench/sregym/main.py` ~L1314-L1330), which appends these to every
    # agent's argv depending on the experiment config — cli_agent has no
    # summary/knowledge-base feature to control, so they are ignored.
    parser.add_argument("--no-inject-summary", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--enable-summary", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--summary-dir", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--summary-model", default=None, help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def _run(
    args: argparse.Namespace,
    *,
    agent_factory: Callable[[str, str, str], CodingAgent] | None = None,
) -> None:
    logger.info("cli_agent driver starting (provider=%s, model=%s)", args.provider, args.model)

    api_base = get_api_base()
    mcp_port = os.getenv("MCP_SERVER_PORT", "9954")
    submit_mcp_url = f"http://localhost:{mcp_port}/submit/sse"

    exp_env = os.getenv("SREGYM_EXP_ENV")
    if exp_env:
        if not os.path.isdir(exp_env):
            logger.error("SREGYM_EXP_ENV=%s is not a valid directory", exp_env)
            sys.exit(1)
        os.chdir(exp_env)
        logger.info("Working directory: %s", os.getcwd())
    else:
        logger.warning("SREGYM_EXP_ENV is not set — running in cwd: %s", os.getcwd())

    poll_stage_sync(api_base, wait_for=READY_STAGES, timeout=300, on_timeout="raise")

    app_info = get_app_info(api_base)
    problem_id = get_problem_id(api_base)
    planned_stages = get_planned_stages(api_base)

    logger.info(
        "Problem: %s | App: %s | Stages: %s",
        problem_id,
        app_info.get("app_name", "?"),
        planned_stages,
    )

    memory_port = getattr(args, "memory_port", None)
    memory_store = getattr(args, "memory_store", None)
    memory_merge_model = getattr(args, "memory_merge_model", None)
    memory_store_only = getattr(args, "memory_store_only", False)
    if memory_port:
        extra_mcp_servers = [_build_memory_mcp_server_http(memory_port, store_only=memory_store_only)]
        memory_server_name = _MEMORY_MCP_SERVER_NAME
    elif memory_store:
        extra_mcp_servers = [_build_memory_mcp_server(memory_store, memory_merge_model)]
        memory_server_name = _MEMORY_MCP_SERVER_NAME
    else:
        extra_mcp_servers = []
        memory_server_name = None

    if agent_factory is not None:
        factory = agent_factory
    else:
        factory = functools.partial(_default_agent_factory, extra_mcp_servers=extra_mcp_servers)

    # Single CLI session for the whole problem. In the default mode the
    # agent calls a stage-routing `submit` tool and reads the oracle verdict
    # from its response. In autonomous mode (SREGYM_AUTONOMOUS_SUBMIT=1) the
    # agent instead calls per-stage `submit_diagnosis` / `submit_mitigation`
    # tools that return a neutral ack, and must self-verify via kubectl.
    autonomous = os.getenv("SREGYM_AUTONOMOUS_SUBMIT", "").strip() == "1"
    prompt = _build_prompt(
        planned_stages,
        app_info,
        autonomous=autonomous,
        memory_mcp_server_name=memory_server_name,
        memory_store_only=memory_store_only,
    )
    started = time.monotonic()
    crashed_with: str | None = None
    agent = None
    try:
        agent = factory(args.provider, args.model, submit_mcp_url)
        agent.generate(prompt, cwd=os.getcwd(), timeout=args.timeout_sec)
    except Exception as exc:
        logger.exception("CLI agent raised")
        crashed_with = f"{type(exc).__name__}: {exc}"
    elapsed = time.monotonic() - started

    if autonomous:
        # Autonomous mode: submit_autonomous grades synchronously and does not
        # advance the sequential state machine, so the conductor will never
        # reach a terminal stage on its own — the worker's force_cleanup runs
        # after the agent process exits. Report the current stage without
        # blocking for a transition that will never happen.
        final_stage = get_current_stage_sync(api_base)
        completed = final_stage is not None
    else:
        # Brief grace wait so the conductor can finish the last
        # `(verifying)` window if the agent's final submit returned just
        # before the oracle finished grading.
        expected: set[str] = set(TERMINAL_STAGES)
        final_stage = wait_for_stages_or_last_seen_sync(api_base, expected=expected, timeout=_POST_STAGE_TIMEOUT_S)
        completed = final_stage in expected
    logger.info(
        "session done; elapsed=%.1fs final_stage=%r completed=%s crashed=%s",
        elapsed,
        final_stage,
        completed,
        bool(crashed_with),
    )

    if args.logs_dir:
        logs_dir = Path(args.logs_dir)
        logs_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out = logs_dir / f"cli_agent_results_{problem_id}_{ts}.json"
        last_usage = getattr(agent, "last_usage", None) if agent is not None else None
        usage_metrics: dict[str, Any] | None = {"total": last_usage.to_dict()} if last_usage is not None else None
        with open(out, "w") as f:
            json.dump(
                {
                    "problem_id": problem_id,
                    "provider": args.provider,
                    "model": args.model,
                    "planned_stages": planned_stages,
                    "elapsed_s": elapsed,
                    "completed": completed,
                    "final_stage": final_stage,
                    "crashed_with": crashed_with,
                    "usage_metrics": usage_metrics,
                },
                f,
                indent=2,
            )
        logger.info("Saved results to %s", out)

    logger.info("cli_agent driver complete.")


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    _run(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
