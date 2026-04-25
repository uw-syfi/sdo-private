"""Minimal SRE Gym agent that wraps a ``agentshim`` CLI agent.

Per stage, instantiates one provider-routed ``CodingAgent`` from `agentshim`
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
``agentshim/gemini/agent.py`` and ``agentshim/opencode/agent.py``).
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

    from agentshim import BaseCodingAgent

logger = logging.getLogger(__name__)

# How long to wait after each stage for the conductor to finish grading
# and advance. Overridable via monkeypatch in tests so the
# "never-submitted" path doesn't block a unit test for 5 minutes.
_POST_STAGE_TIMEOUT_S = 300

_SUBMIT_MCP_SERVER_NAME = "sregym"
_AUTONOMOUS_PROMPT_PROFILES = frozenset({"sds", "direct"})
_DEFAULT_AUTONOMOUS_PROMPT_PROFILE = "sds"


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
    autonomous_prompt_profile: str = _DEFAULT_AUTONOMOUS_PROMPT_PROFILE,
    submit_done_returns_feedback: bool = False,
) -> str:
    """Render the single-session prompt handed to the wrapped CLI agent.

    One CLI session handles every planned stage. In the default mode, the
    agent calls the single ``submit`` tool once per stage and the conductor
    routes each call to whichever stage is currently active — the tool
    response carries the grading verdict. In autonomous mode (``autonomous=True``),
    the agent instead calls per-stage ``submit_diagnosis`` / ``submit_mitigation``
    tools that return only a neutral acknowledgement, so the agent must
    self-verify by inspecting the cluster before submitting. Autonomous
    prompt instructions are further selected by ``autonomous_prompt_profile``.

    The prompt deliberately omits the benchmark ``problem_id``: SREGym
    problem IDs are descriptive (``incorrect_image``,
    ``missing_env_variable_astronomy_shop``, ``liveness_probe_misconfiguration_*``)
    and including them would leak the answer to the agent. App name and
    namespace are kept because they're observable from the cluster anyway.
    """
    template_name = "session.j2"
    if autonomous:
        profile = _normalize_autonomous_prompt_profile(autonomous_prompt_profile)
        template_name = {
            "sds": "session_autonomous.j2",
            "direct": "session_autonomous_direct.j2",
        }[profile]
    return (
        _jinja_env()
        .get_template(template_name)
        .render(
            planned_stages=list(planned_stages),
            app_name=app_info.get("app_name", "<unknown>"),
            namespace=app_info.get("namespace", "<unknown>"),
            submit_mcp_server_name=_SUBMIT_MCP_SERVER_NAME,
            submit_done_returns_feedback=submit_done_returns_feedback,
        )
    )


def _normalize_autonomous_prompt_profile(profile: Any) -> str:
    """Normalize and validate the autonomous prompt-profile selector."""
    if profile is None:
        return _DEFAULT_AUTONOMOUS_PROMPT_PROFILE
    normalized = str(profile).strip().lower()
    if not normalized:
        return _DEFAULT_AUTONOMOUS_PROMPT_PROFILE
    if normalized not in _AUTONOMOUS_PROMPT_PROFILES:
        allowed = ", ".join(sorted(_AUTONOMOUS_PROMPT_PROFILES))
        raise ValueError(f"Unknown autonomous prompt profile {profile!r}. Expected one of: {allowed}")
    return normalized


# --- Conductor I/O ---------------------------------------------------------
# Conductor HTTP client lives in libs/sregym_lib/conductor.py — this driver
# only owns the cli_agent-specific orchestration below.


def _default_agent_factory(
    provider: str,
    model: str,
    submit_mcp_url: str,
    extra_mcp_servers: list[Any] | None = None,
) -> BaseCodingAgent:
    """Build a ``CodingAgent`` with the sregym submit MCP server wired in.

    Raises ``ValueError`` if the requested provider does not support MCP
    (e.g. ``gemini``/``opencode``) — the underlying class raises it from
    its ``__init__`` when ``mcp_servers`` is non-empty.
    """
    # Deferred imports keep `--help` fast and avoid triggering heavy
    # provider imports until the driver actually needs a backend.
    from agentshim import CodingAgent
    from agentshim.mcp_config import HttpMcpServer

    mcp_servers: list[Any] = [HttpMcpServer(name=_SUBMIT_MCP_SERVER_NAME, url=submit_mcp_url)]
    if extra_mcp_servers:
        mcp_servers.extend(extra_mcp_servers)
    try:
        return CodingAgent(provider=provider, model=model, mcp_servers=mcp_servers)
    except ValueError as exc:
        raise ValueError(f"Unknown cli_agent provider {provider!r}: {exc}") from exc


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
            "CLI provider from agentshim.CodingAgent "
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
        "--autonomous-prompt-profile",
        choices=sorted(_AUTONOMOUS_PROMPT_PROFILES),
        default=_normalize_autonomous_prompt_profile(toml_cfg.get("autonomous_prompt_profile")),
        help=(
            "Autonomous prompt variant to use when SREGYM_AUTONOMOUS_SUBMIT=1. "
            "'sds' keeps the repo-local .sds operational-tooling workflow; "
            "'direct' removes .sds/script-writing expectations and focuses on direct "
            "source + cluster investigation (default: sds)."
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
    agent_factory: Callable[[str, str, str], BaseCodingAgent] | None = None,
) -> None:
    logger.info("cli_agent driver starting (provider=%s, model=%s)", args.provider, args.model)

    api_base = get_api_base()
    mcp_port = os.getenv("MCP_SERVER_PORT", "9954")
    submit_mcp_url = f"http://localhost:{mcp_port}/submit/sse"

    agent_workdir = os.getenv("SREGYM_AGENT_WORKDIR") or os.getenv("SREGYM_EXP_ENV")
    if agent_workdir:
        if not os.path.isdir(agent_workdir):
            logger.error("Agent workdir %s is not a valid directory", agent_workdir)
            sys.exit(1)
        os.chdir(agent_workdir)
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

    extra_mcp_servers = []

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
    submit_done_returns_feedback = os.getenv("SREGYM_SUBMIT_DONE_RETURNS_FEEDBACK", "").strip() == "1"
    prompt = _build_prompt(
        planned_stages,
        app_info,
        autonomous=autonomous,
        autonomous_prompt_profile=args.autonomous_prompt_profile,
        submit_done_returns_feedback=submit_done_returns_feedback,
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
