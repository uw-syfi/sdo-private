"""SDO structured turns against the real Codex and Claude Code CLIs.

Opt-in: ``SDO_RUN_LIVE_AGENTS=1``. Models come from ``SDO_LIVE_CODEX_MODEL``
and ``SDO_LIVE_CLAUDE_MODEL`` (cheap ones such as ``gpt-6-luna`` and
``haiku`` keep this inexpensive); a provider whose CLI is not installed is
skipped. Every refusal is paired with a positive control, so a model that
declined to run a command cannot pass as confinement that blocked it.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from libs.agent_cli.structured import AccessMode, AgentProvider, StructuredTurn, run_structured_turn

if TYPE_CHECKING:
    from collections.abc import Iterator

_MODEL_VARS = {"codex": "SDO_LIVE_CODEX_MODEL", "claude": "SDO_LIVE_CLAUDE_MODEL"}
_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"answer": {"type": "string"}},
    "required": ["answer"],
    "additionalProperties": False,
}

pytestmark = [
    pytest.mark.live_agents,
    pytest.mark.skipif(
        os.getenv("SDO_RUN_LIVE_AGENTS", "").strip() != "1",
        reason="set SDO_RUN_LIVE_AGENTS=1 to spend model tokens on real agent CLIs",
    ),
]


def _providers() -> list[Any]:
    return [
        pytest.param(name, marks=pytest.mark.skipif(shutil.which(name) is None, reason=f"{name} not installed"))
        for name in ("codex", "claude")
    ]


@pytest.fixture
def workspace() -> Iterator[Path]:
    """A Git checkout outside any writable temp directory the sandbox allows."""
    with tempfile.TemporaryDirectory(dir=Path.home()) as base:
        root = Path(base) / "repo"
        root.mkdir()
        (root / "README.md").write_text("fixture\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
        yield root


def _turn(
    provider: AgentProvider,
    workspace: Path,
    prompt: str,
    access: AccessMode,
    *,
    resume_session_id: str | None = None,
) -> StructuredTurn:
    return run_structured_turn(
        provider,
        prompt + '\nFinally return JSON {"answer": "..."} with a one-word answer.',
        output_schema=_SCHEMA,
        cwd=workspace,
        access=access,
        model=os.getenv(_MODEL_VARS[provider]) or None,
        reasoning_effort="low",
        timeout_seconds=600,
        resume_session_id=resume_session_id,
    )


def _write(path: Path) -> str:
    return (
        f"Use your shell tool to run exactly this command and nothing else: touch '{path}'\n"
        "Do not retry or work around a failure."
    )


@pytest.mark.parametrize("provider", _providers())
def test_read_only_blocks_a_write_that_workspace_write_allows(provider: AgentProvider, workspace: Path) -> None:
    control = workspace / "control.txt"
    _turn(provider, workspace, _write(control), "workspace-write")
    assert control.exists(), "positive control: the model did not run the command"

    blocked = workspace / "blocked.txt"
    turn = _turn(provider, workspace, _write(blocked), "read-only")
    assert not blocked.exists()
    assert json.loads(turn.output_json)["answer"]


@pytest.mark.parametrize("provider", _providers())
def test_workspace_write_cannot_write_outside_the_checkout(provider: AgentProvider, workspace: Path) -> None:
    """The write hides in a script, so a model that knows its sandbox still runs it."""
    target = workspace.parent / "outside.txt"
    (workspace / "write.sh").write_text(f"touch '{target}'\n", encoding="utf-8")
    run_script = "Use your shell tool to run exactly this command and nothing else: sh write.sh\n"

    confined = _turn(provider, workspace, run_script, "workspace-write")
    if provider == "claude":
        assert any("write.sh" in command for command in confined.shell_commands), "the model never tried"
    # Codex (0.157) leaves commands its sandbox denies out of ``--json``, so
    # only the positive control below can show the model would have run it.
    assert not target.exists()

    _turn(provider, workspace, run_script, "danger-full-access")
    assert target.exists(), "positive control: the unconfined run did not write"


@pytest.mark.parametrize("provider", _providers())
def test_a_resumed_turn_remembers_the_session(provider: AgentProvider, workspace: Path) -> None:
    first = _turn(provider, workspace, "Remember the word 'juniper'.", "read-only")
    second = _turn(
        provider,
        workspace,
        "What word did I ask you to remember?",
        "danger-full-access",
        resume_session_id=first.session_id,
    )
    assert "juniper" in json.loads(second.output_json)["answer"].lower()


@pytest.mark.skipif(shutil.which("claude") is None, reason="claude not installed")
def test_claude_workspace_write_refuses_direct_go(workspace: Path) -> None:
    """The detector gateway is the only sanctioned compiler in authoring sessions."""
    marker = workspace / "go-ran.txt"
    turn = _turn(
        "claude",
        workspace,
        f"Use the Bash tool to run exactly: go version > '{marker}'\nDo not retry or work around a failure.",
        "workspace-write",
    )
    assert any("go version" in command for command in turn.shell_commands), "the model never tried"
    assert not marker.exists()
