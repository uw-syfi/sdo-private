"""MCP server exposing crucible bespoke tools for agent_cli.

Usage::

    python -m sregym_agents.crucible.tools.mcp_server \\
        --tools sre \\
        --namespace default \\
        --stage diagnosis \\
        --shared-file /tmp/shared.md \\
        --model claude-sonnet-4-20250514

Runs a stdio-transport MCP server that agent_cli (Claude Code) connects to
via ``StdioMcpServer``.  Standard tools (bash, read/write, grep) are NOT
exposed — agent_cli provides those natively.
"""

from __future__ import annotations

import argparse
import json
import logging
import socket
from pathlib import Path
from typing import Any

from fastmcp import FastMCP

from sregym_agents.crucible.tools._deps import JudgeDeps, SharedFile, SharedState, SREDeps
from sregym_agents.crucible.tools._judge_tools import (
    reveal_agent_hypothesis_impl,
    submit_independent_findings_impl,
    submit_verdict_impl,
)
from sregym_agents.crucible.tools._kb_tools import (
    LTMMitigationShortCircuit,
    LTMShortCircuit,
    check_hypothesis_coverage_impl,
    search_prior_incidents_impl,
    search_prior_mitigations_impl,
    triage_cluster_impl,
)

logger = logging.getLogger(__name__)

mcp = FastMCP("crucible-tools")


# ---------------------------------------------------------------------------
# IPC helpers
# ---------------------------------------------------------------------------


def _send_signal(socket_path: str, data: dict[str, Any]) -> None:
    """Send a JSON signal to the driver via Unix domain socket."""
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect(socket_path)
        sock.sendall(json.dumps(data).encode())
        sock.close()
    except Exception as exc:
        logger.warning("Failed to send signal via socket %s: %s", socket_path, exc)


def _write_result_file(path: str, data: dict[str, Any]) -> None:
    """Write a JSON result to the result file (atomic)."""
    try:
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f)
        import os

        os.replace(tmp, path)
    except Exception as exc:
        logger.warning("Failed to write result file %s: %s", path, exc)


# ---------------------------------------------------------------------------
# Tool registration helpers
# ---------------------------------------------------------------------------


def register_sre_tools(
    deps: SREDeps,
    *,
    signal_socket_path: str | None = None,
    result_file_path: str | None = None,
) -> None:
    """Register SRE KB tools on the MCP server."""

    @mcp.tool(name="triage_cluster")
    async def triage_cluster() -> str:  # pyright: ignore[reportUnusedFunction]
        """Systematically audit the Kubernetes namespace for unhealthy components.

        Call this FIRST, before search_prior_incidents. Returns a structured
        triage report listing all anomalous resources.
        """
        return await triage_cluster_impl(deps)

    @mcp.tool(name="search_prior_incidents")
    async def search_prior_incidents(observed_symptoms: str) -> str:  # pyright: ignore[reportUnusedFunction]
        """Search past incidents and return verified candidate root causes.

        Call EARLY with observed symptoms from quick triage.

        Args:
            observed_symptoms: Factual description of current observations.
        """
        try:
            return await search_prior_incidents_impl(deps, observed_symptoms)
        except LTMShortCircuit as exc:
            signal = {
                "short_circuit": True,
                "confirmed": exc.confirmed,
                "confirmed_slugs": exc.confirmed_slugs,
                "iteration": exc.iteration,
            }
            if signal_socket_path:
                _send_signal(signal_socket_path, signal)
            if result_file_path:
                _write_result_file(result_file_path, signal)
            return json.dumps(signal)

    @mcp.tool(name="search_prior_mitigations")
    async def search_prior_mitigations(  # pyright: ignore[reportUnusedFunction]
        root_cause: str,
        failed_attempts: str = "",
    ) -> str:
        """Search past incidents for mitigation strategies matching a confirmed root cause.

        Args:
            root_cause: The confirmed root cause diagnosis.
            failed_attempts: Description of mitigation attempts that already failed.
        """
        try:
            return await search_prior_mitigations_impl(deps, root_cause, failed_attempts)
        except LTMMitigationShortCircuit as exc:
            signal = {
                "short_circuit": True,
                "applied": exc.applied,
                "iteration": exc.iteration,
            }
            if signal_socket_path:
                _send_signal(signal_socket_path, signal)
            if result_file_path:
                _write_result_file(result_file_path, signal)
            return json.dumps(signal)

    @mcp.tool(name="check_hypothesis_coverage")
    async def check_hypothesis_coverage(hypothesis: str) -> str:  # pyright: ignore[reportUnusedFunction]
        """Cross-check your hypothesis against the triage report.

        Call BEFORE submitting your diagnosis.

        Args:
            hypothesis: Proposed root cause (resource, misconfigured field, causal chain).
        """
        return await check_hypothesis_coverage_impl(deps, hypothesis)

    # submit_answer — structured output for AgentCLIDriver
    if result_file_path:

        @mcp.tool(name="submit_answer")
        def submit_answer(  # pyright: ignore[reportUnusedFunction]
            answer: str,
            justification: str,
            causal_chain: str = "",
            reflection: str = "",
        ) -> str:
            """Submit your final answer when you have completed your investigation.

            Args:
                answer: Concise diagnosis or description of applied mitigation.
                justification: Evidence and reasoning supporting the answer.
                causal_chain: Full causal chain (diagnosis only). Leave empty for mitigation.
                reflection: Recovery analysis (recovery only). Usually leave empty.
            """
            _write_result_file(
                result_file_path,
                {
                    "type": "answer",
                    "data": {
                        "answer": answer,
                        "justification": justification,
                        "causal_chain": causal_chain,
                        "reflection": reflection,
                    },
                },
            )
            return "Answer submitted successfully."


def register_judge_tools(
    deps: JudgeDeps,
    *,
    signal_socket_path: str | None = None,
    result_file_path: str | None = None,
) -> None:
    """Register judge tools on the MCP server."""

    @mcp.tool(name="submit_independent_findings")
    def submit_independent_findings(findings: str) -> str:  # pyright: ignore[reportUnusedFunction]
        """Record the judge's independent investigation findings.

        Args:
            findings: The judge's independent assessment of the cluster state.
        """
        return submit_independent_findings_impl(deps, findings)

    @mcp.tool(name="reveal_agent_hypothesis")
    def reveal_agent_hypothesis() -> str:  # pyright: ignore[reportUnusedFunction]
        """Reveal the agent's hypothesis after submitting independent findings."""
        return reveal_agent_hypothesis_impl(deps)

    @mcp.tool(name="submit_verdict")
    async def submit_verdict(  # pyright: ignore[reportUnusedFunction]
        verdict: bool,
        reasoning: str,
        submission_ans: str | list[str],
    ) -> str:
        """Record the judge's verdict and, if approved, submit to the benchmark.

        Args:
            verdict: True to APPROVE, False to REJECT.
            reasoning: Explanation for the verdict.
            submission_ans: The agent's answer to forward to the benchmark.
        """
        result = await submit_verdict_impl(deps, verdict, reasoning, submission_ans)
        if result_file_path:
            _write_result_file(
                result_file_path,
                {
                    "type": "verdict",
                    "submitted": True,
                    "verdict": "APPROVED" if verdict else "REJECTED",
                    "answer": submission_ans if isinstance(submission_ans, str) else list(submission_ans),
                },
            )
        return result


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Crucible MCP server — exposes bespoke tools for agent_cli.",
    )
    parser.add_argument(
        "--tools",
        choices=["sre", "judge", "all"],
        required=True,
        help="Which tool set to register.",
    )
    parser.add_argument("--namespace", default="default", help="Kubernetes namespace.")
    parser.add_argument(
        "--stage",
        choices=["diagnosis", "mitigation"],
        default="diagnosis",
        help="Current pipeline stage.",
    )
    parser.add_argument("--shared-file", required=True, help="Path to the shared session file.")
    parser.add_argument("--model", default="claude-sonnet-4-20250514", help="Model ID for subagents.")
    parser.add_argument("--iteration", type=int, default=1, help="Current iteration number.")
    parser.add_argument("--prompt-version", default="v1", help="Prompt renderer version.")
    parser.add_argument("--lt-summary-file", default=None, help="Path to long-term summary file.")
    parser.add_argument("--incidents-dir", default=None, help="Path to incidents directory.")
    parser.add_argument("--playbooks-dir", default=None, help="Path to playbooks directory.")
    parser.add_argument("--mitigation-playbooks-dir", default=None, help="Path to mitigation playbooks directory.")
    parser.add_argument("--ltm-call-budget", type=int, default=1, help="Max KB search calls per stage.")
    parser.add_argument(
        "--enable-ltm-verified-direct-submit",
        action="store_true",
        help="Enable short-circuit when KB verification confirms a candidate.",
    )
    # Judge-specific
    parser.add_argument("--submit-mcp-url", default=None, help="Benchmark MCP submission URL (judge mode).")
    parser.add_argument("--hypothesis-text", default="", help="Agent hypothesis text (judge mode).")
    # IPC
    parser.add_argument("--signal-socket", default=None, help="Unix socket path for short-circuit signaling.")
    parser.add_argument("--result-file", default=None, help="Path to write structured output / verdict state.")
    # Backend for subagent dispatch
    parser.add_argument(
        "--backend",
        default="agent-cli",
        choices=["pydantic-ai", "agent-cli"],
        help="Backend for subagent dispatch within KB tools.",
    )
    parser.add_argument("--provider", default="claude", help="CLI agent provider (agent-cli backend only).")
    return parser


def _create_run_subagent(backend: str, model: str, provider: str):
    """Create a ``run_subagent`` closure for KB tool subagent dispatch."""
    if backend == "agent-cli":
        from sregym_agents.crucible.agents.drivers.agent_cli_driver import AgentCLIDriver

        driver: Any = AgentCLIDriver(provider=provider, model=model)
    else:
        from sregym_agents.crucible.agents.drivers.pydantic_ai_driver import PydanticAIDriver

        driver = PydanticAIDriver(model)

    async def _run_subagent(
        *,
        prompt: str,
        output_type: type,
        tools: list[Any] | None = None,
        agent_name: str = "",
        model_settings: dict[str, Any] | None = None,
        usage_collector: Any | None = None,
    ) -> Any:
        result = await driver.run(
            prompt=prompt,
            output_type=output_type,
            tools=tools,
            agent_name=agent_name,
            model_settings=model_settings,
            usage_collector=usage_collector,
        )
        return result.unwrap(agent_name)

    return _run_subagent


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    from sregym_agents.crucible._prompts import PromptRenderer

    shared_file = SharedFile(Path(args.shared_file))
    shared_state = SharedState()
    renderer = PromptRenderer(args.prompt_version)

    # Create run_subagent closure for KB tool subagent dispatch
    run_subagent = _create_run_subagent(args.backend, args.model, args.provider)

    if args.tools in ("sre", "all"):
        sre_deps = SREDeps(
            namespace=args.namespace,
            shared_file=shared_file,
            iteration=args.iteration,
            stage=args.stage,
            model_id=args.model,
            renderer=renderer,
            state=shared_state,
            lt_summary_file=Path(args.lt_summary_file) if args.lt_summary_file else None,
            incidents_dir=Path(args.incidents_dir) if args.incidents_dir else None,
            playbooks_dir=Path(args.playbooks_dir) if args.playbooks_dir else None,
            mitigation_playbooks_dir=Path(args.mitigation_playbooks_dir) if args.mitigation_playbooks_dir else None,
            ltm_call_budget=args.ltm_call_budget,
            enable_ltm_verified_direct_submit=args.enable_ltm_verified_direct_submit,
            run_subagent=run_subagent,
        )
        register_sre_tools(
            sre_deps,
            signal_socket_path=args.signal_socket,
            result_file_path=args.result_file,
        )

    if args.tools in ("judge", "all"):
        if not args.submit_mcp_url:
            parser.error("--submit-mcp-url is required for judge tools")
        judge_deps = JudgeDeps(
            namespace=args.namespace,
            shared_file=shared_file,
            iteration=args.iteration,
            stage=args.stage,
            submit_mcp_url=args.submit_mcp_url,
            renderer=renderer,
            hypothesis_text=args.hypothesis_text,
            state=shared_state,
        )
        register_judge_tools(
            judge_deps,
            signal_socket_path=args.signal_socket,
            result_file_path=args.result_file,
        )

    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
