"""Tests for the recovery-path playbook classifier.

The classifier picks a KB slug (from ``long_term_summary.md``) for a recovered
diagnosis produced after the benchmark rejected the agent's original answer.
Only slugs present in the summary are accepted; anything else falls back to
``None`` so the caller runs the normal mitigation loop.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from sregym_agents.crucible.recovery_playbook_classifier import (
    RecoveryPlaybookMatch,
    classify_recovery_playbook,
)

_SUMMARY = """\
# Long Term Summary

### Root Cause: MongoDB Replica Set Misconfig

#### Slug: mongo_replica_misconfig

Some details.

### Root Cause: Redis OOM

#### Slug: redis_oom

Details.
"""


def _driver_returning(slug: str | None) -> MagicMock:
    driver = MagicMock()
    result = MagicMock()
    result.output = RecoveryPlaybookMatch(slug=slug, reasoning="because")
    driver.run = AsyncMock(return_value=result)
    return driver


class TestClassifyRecoveryPlaybook:
    @pytest.mark.anyio
    async def test_valid_slug_from_driver_returned(self):
        driver = _driver_returning("mongo_replica_misconfig")
        picked = await classify_recovery_playbook(
            driver=driver,
            model_id="m",
            summary_text=_SUMMARY,
            recovery_answer="The mongo replica set was misconfigured.",
        )
        assert picked == "mongo_replica_misconfig"
        driver.run.assert_awaited_once()

    @pytest.mark.anyio
    async def test_invalid_slug_rejected(self):
        driver = _driver_returning("not_in_summary")
        picked = await classify_recovery_playbook(
            driver=driver,
            model_id="m",
            summary_text=_SUMMARY,
            recovery_answer="something",
        )
        assert picked is None

    @pytest.mark.anyio
    async def test_null_slug_returns_none(self):
        driver = _driver_returning(None)
        picked = await classify_recovery_playbook(
            driver=driver,
            model_id="m",
            summary_text=_SUMMARY,
            recovery_answer="something",
        )
        assert picked is None

    @pytest.mark.anyio
    async def test_empty_summary_skips_driver(self):
        driver = MagicMock()
        driver.run = AsyncMock()
        picked = await classify_recovery_playbook(
            driver=driver,
            model_id="m",
            summary_text="",
            recovery_answer="x",
        )
        assert picked is None
        driver.run.assert_not_awaited()

    @pytest.mark.anyio
    async def test_empty_answer_skips_driver(self):
        driver = MagicMock()
        driver.run = AsyncMock()
        picked = await classify_recovery_playbook(
            driver=driver,
            model_id="m",
            summary_text=_SUMMARY,
            recovery_answer="",
        )
        assert picked is None
        driver.run.assert_not_awaited()

    @pytest.mark.anyio
    async def test_driver_exception_returns_none(self):
        driver = MagicMock()
        driver.run = AsyncMock(side_effect=RuntimeError("boom"))
        picked = await classify_recovery_playbook(
            driver=driver,
            model_id="m",
            summary_text=_SUMMARY,
            recovery_answer="x",
        )
        assert picked is None

    @pytest.mark.anyio
    async def test_driver_no_output_returns_none(self):
        driver = MagicMock()
        result = MagicMock()
        result.output = None
        driver.run = AsyncMock(return_value=result)
        picked = await classify_recovery_playbook(
            driver=driver,
            model_id="m",
            summary_text=_SUMMARY,
            recovery_answer="x",
        )
        assert picked is None

    @pytest.mark.anyio
    async def test_orchestrator_shortcut_invokes_playbook_on_classifier_hit(self, tmp_path):
        from unittest.mock import patch

        from sregym_agents.crucible.knowledge_base.base import InjectedKB
        from sregym_agents.crucible.orchestrator import (
            StageLoopResult,
            _try_recovery_playbook_shortcut,
        )
        from sregym_agents.crucible.tools import SharedFile

        summary_file = tmp_path / "long_term_summary.md"
        summary_file.write_text(_SUMMARY)
        pb_dir = tmp_path / "mitigation_playbooks"
        pb_dir.mkdir()
        shared = tmp_path / "shared.md"
        shared.write_text("# Session\n")

        injected = InjectedKB(
            summary=summary_file,
            mitigation_playbooks_dir=pb_dir,
        )
        diag_result = StageLoopResult(
            approved=False,
            benchmark_block="<benchmark_result>\nsuccess: False\n</benchmark_result>",
            agent_answer="Recovered: redis is out of memory",
        )

        expected = StageLoopResult(approved=True, benchmark_block="success: True")
        with (
            patch(
                "sregym_agents.crucible.recovery_playbook_classifier.classify_recovery_playbook",
                new=AsyncMock(return_value="redis_oom"),
            ) as mock_classify,
            patch(
                "sregym_agents.crucible.orchestrator._try_playbook_shortcut",
                new=AsyncMock(return_value=expected),
            ) as mock_shortcut,
        ):
            out = await _try_recovery_playbook_shortcut(
                driver=MagicMock(),
                model="m",
                app_info={"namespace": "ns"},
                diag_result=diag_result,
                injected_kb=injected,
                shared_file=SharedFile(shared),
                submit_mcp_url="http://x",
                renderer=MagicMock(),
                playbook_run_subagent=AsyncMock(),
                primary_collector=MagicMock(),
                recovery_collector=MagicMock(),
            )

        assert out is expected
        mock_classify.assert_awaited_once()
        mock_shortcut.assert_awaited_once()
        assert mock_shortcut.await_args is not None
        assert mock_shortcut.await_args.kwargs["slug"] == "redis_oom"

    @pytest.mark.anyio
    async def test_orchestrator_shortcut_returns_none_when_classifier_misses(self, tmp_path):
        from unittest.mock import patch

        from sregym_agents.crucible.knowledge_base.base import InjectedKB
        from sregym_agents.crucible.orchestrator import (
            StageLoopResult,
            _try_recovery_playbook_shortcut,
        )
        from sregym_agents.crucible.tools import SharedFile

        summary_file = tmp_path / "long_term_summary.md"
        summary_file.write_text(_SUMMARY)
        pb_dir = tmp_path / "mitigation_playbooks"
        pb_dir.mkdir()
        shared = tmp_path / "shared.md"
        shared.write_text("# Session\n")

        injected = InjectedKB(summary=summary_file, mitigation_playbooks_dir=pb_dir)
        diag_result = StageLoopResult(
            approved=False,
            benchmark_block="<benchmark_result>\nsuccess: False\n</benchmark_result>",
            agent_answer="Recovered answer",
        )

        with (
            patch(
                "sregym_agents.crucible.recovery_playbook_classifier.classify_recovery_playbook",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "sregym_agents.crucible.orchestrator._try_playbook_shortcut",
                new=AsyncMock(),
            ) as mock_shortcut,
        ):
            out = await _try_recovery_playbook_shortcut(
                driver=MagicMock(),
                model="m",
                app_info={},
                diag_result=diag_result,
                injected_kb=injected,
                shared_file=SharedFile(shared),
                submit_mcp_url="http://x",
                renderer=MagicMock(),
                playbook_run_subagent=AsyncMock(),
                primary_collector=MagicMock(),
                recovery_collector=MagicMock(),
            )

        assert out is None
        mock_shortcut.assert_not_awaited()
        assert "No slug match" in shared.read_text()

    @pytest.mark.anyio
    async def test_summary_with_no_slug_lines_falls_back_to_slugify(self):
        summary = "### Root Cause: MongoDB Replica Set Misconfig\n\nsome text\n"
        driver = _driver_returning("mongodb_replica_set_misconfig")
        picked = await classify_recovery_playbook(
            driver=driver,
            model_id="m",
            summary_text=summary,
            recovery_answer="mongo replica set issue",
        )
        assert picked == "mongodb_replica_set_misconfig"
