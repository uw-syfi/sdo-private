"""Tests for the playbook-first mitigation shortcut feature.

When ``enable_playbook_shortcut`` is on, the orchestrator attempts to execute a
mitigation playbook directly before running the full SRE mitigation agent loop.
The slug is resolved via ``matched_candidate_index`` from the benchmark oracle
cross-referenced with ``confirmed_slugs`` threaded from ``LTMShortCircuit``.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from sregym_agents.crucible.orchestrator import (
    StageLoopResult,
    _extract_matched_candidate_index,
    _try_playbook_shortcut,
)
from sregym_agents.crucible.tools._kb_tools import (
    LTMShortCircuit,
    MitigationApplication,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_benchmark_block(
    success: bool = True,
    matched_candidate: str | None = "diag-a",
    matched_candidate_index: int | None = 0,
) -> str:
    oracle = {
        "Diagnosis": {
            "judgment": "True" if success else "False",
            "reasoning": "test reasoning",
            "success": success,
            "accuracy": 100.0 if success else 0.0,
            "matched_candidate": matched_candidate,
            "matched_candidate_index": matched_candidate_index,
        },
    }
    return (
        f"\n<benchmark_result>\nsuccess: {success}\nmessage: ok\n"
        f"<oracle>\n{json.dumps(oracle, indent=2)}\n</oracle>\n</benchmark_result>\n"
    )


# ---------------------------------------------------------------------------
# _extract_matched_candidate_index
# ---------------------------------------------------------------------------


class TestExtractMatchedCandidateIndex:
    def test_parses_index_from_oracle(self):
        block = _make_benchmark_block(matched_candidate_index=2)
        assert _extract_matched_candidate_index(block) == 2

    def test_returns_none_when_index_absent(self):
        block = _make_benchmark_block(matched_candidate_index=None)
        assert _extract_matched_candidate_index(block) is None

    def test_returns_none_when_no_oracle_tags(self):
        block = "\n<benchmark_result>\nsuccess: True\nmessage: ok\n</benchmark_result>\n"
        assert _extract_matched_candidate_index(block) is None

    def test_returns_none_on_malformed_json(self):
        block = "\n<benchmark_result>\n<oracle>\n{bad json}\n</oracle>\n</benchmark_result>\n"
        assert _extract_matched_candidate_index(block) is None

    def test_returns_none_when_diagnosis_key_missing(self):
        oracle = {"Mitigation": {"matched_candidate_index": 0}}
        block = f"<oracle>\n{json.dumps(oracle)}\n</oracle>"
        assert _extract_matched_candidate_index(block) is None


# ---------------------------------------------------------------------------
# LTMShortCircuit slug threading
# ---------------------------------------------------------------------------


class TestLTMShortCircuitSlugs:
    def test_carries_confirmed_slugs(self):
        sig = LTMShortCircuit(
            confirmed=["diag-a", "diag-b"],
            iteration=1,
            confirmed_slugs=["slug-a", "slug-b"],
        )
        assert sig.confirmed_slugs == ["slug-a", "slug-b"]
        assert sig.confirmed == ["diag-a", "diag-b"]

    def test_defaults_to_empty_slugs(self):
        sig = LTMShortCircuit(confirmed=["diag-a"], iteration=1)
        assert sig.confirmed_slugs == []


# ---------------------------------------------------------------------------
# StageLoopResult.confirmed_slugs
# ---------------------------------------------------------------------------


class TestStageLoopResultSlugs:
    def test_defaults_to_empty(self):
        r = StageLoopResult(approved=True)
        assert r.confirmed_slugs == []

    def test_stores_slugs(self):
        r = StageLoopResult(approved=True, confirmed_slugs=["s1", "s2"])
        assert r.confirmed_slugs == ["s1", "s2"]


# ---------------------------------------------------------------------------
# Slug resolution via index
# ---------------------------------------------------------------------------


class TestSlugResolution:
    def test_resolves_slug_via_index(self):
        block = _make_benchmark_block(matched_candidate_index=1)
        idx = _extract_matched_candidate_index(block)
        slugs = ["slug-a", "slug-b", "slug-c"]
        assert idx is not None and 0 <= idx < len(slugs)
        assert slugs[idx] == "slug-b"

    def test_index_out_of_range_is_guarded(self):
        block = _make_benchmark_block(matched_candidate_index=5)
        idx = _extract_matched_candidate_index(block)
        slugs = ["slug-a"]
        assert idx is not None
        assert not (0 <= idx < len(slugs))

    def test_empty_slug_skips(self):
        block = _make_benchmark_block(matched_candidate_index=0)
        idx = _extract_matched_candidate_index(block)
        slugs = [""]
        assert idx is not None and 0 <= idx < len(slugs)
        assert not slugs[idx]  # empty string is falsy


# ---------------------------------------------------------------------------
# _try_playbook_shortcut
# ---------------------------------------------------------------------------


class TestTryPlaybookShortcut:
    @pytest.mark.anyio
    async def test_no_playbook_file_returns_none(self, tmp_path: Path):
        shared_path = tmp_path / "shared.md"
        shared_path.write_text("# Session\n")

        from sregym_agents.crucible.tools import SharedFile

        sf = SharedFile(shared_path)
        pb_dir = tmp_path / "mitigation_playbooks"
        pb_dir.mkdir()

        result = await _try_playbook_shortcut(
            model=MagicMock(),
            namespace="ns",
            slug="nonexistent-slug",
            mitigation_playbooks_dir=pb_dir,
            shared_file=sf,
            submit_mcp_url="http://test/submit",
            renderer=MagicMock(),
            usage_collector=MagicMock(),
        )
        assert result is None
        content = shared_path.read_text()
        assert "No Playbook Found" in content

    @pytest.mark.anyio
    async def test_playbook_applied_returns_result(self, tmp_path: Path):
        shared_path = tmp_path / "shared.md"
        shared_path.write_text("# Session\n")

        from sregym_agents.crucible.tools import SharedFile

        sf = SharedFile(shared_path)

        applied_output = MitigationApplication(
            strategy_index=0,
            root_cause_class="test-class",
            applied=True,
            mitigation_summary="Fixed the issue",
            reasoning="It worked",
        )

        with (
            patch(
                "sregym_agents.crucible.tools._kb_tools.load_mitigation_playbook_text",
                return_value="# Playbook\nsome content",
            ),
            patch(
                "sregym_agents.crucible.knowledge_base.mitigation_playbook.MitigationPlaybookStore",
            ) as mock_store_cls,
            patch(
                "sregym_agents.crucible.tools._kb_tools.run_single_mitigation_playbook",
                new_callable=AsyncMock,
                return_value=applied_output,
            ),
            patch(
                "sregym_agents.crucible.orchestrator._direct_submit_confirmed",
                new_callable=AsyncMock,
                return_value=StageLoopResult(approved=True, benchmark_block="success: True"),
            ) as mock_submit,
        ):
            mock_store = MagicMock()
            mock_pb = MagicMock()
            mock_pb.class_name = "test-class"
            mock_store.load.return_value = mock_pb
            mock_store_cls.return_value = mock_store

            result = await _try_playbook_shortcut(
                model=MagicMock(),
                namespace="ns",
                slug="test-slug",
                mitigation_playbooks_dir=tmp_path,
                shared_file=sf,
                submit_mcp_url="http://test/submit",
                renderer=MagicMock(),
                usage_collector=MagicMock(),
            )
            assert result is not None
            assert result.approved is True
            mock_submit.assert_awaited_once()
            content = shared_path.read_text()
            assert "Applied" in content

    @pytest.mark.anyio
    async def test_playbook_not_applied_returns_none(self, tmp_path: Path):
        shared_path = tmp_path / "shared.md"
        shared_path.write_text("# Session\n")

        from sregym_agents.crucible.tools import SharedFile

        sf = SharedFile(shared_path)

        not_applied = MitigationApplication(
            strategy_index=0,
            root_cause_class="test-class",
            applied=False,
            reasoning="Playbook doesn't apply",
        )

        with (
            patch(
                "sregym_agents.crucible.tools._kb_tools.load_mitigation_playbook_text",
                return_value="# Playbook\nsome content",
            ),
            patch(
                "sregym_agents.crucible.knowledge_base.mitigation_playbook.MitigationPlaybookStore",
            ) as mock_store_cls,
            patch(
                "sregym_agents.crucible.tools._kb_tools.run_single_mitigation_playbook",
                new_callable=AsyncMock,
                return_value=not_applied,
            ),
        ):
            mock_store = MagicMock()
            mock_pb = MagicMock()
            mock_pb.class_name = "test-class"
            mock_store.load.return_value = mock_pb
            mock_store_cls.return_value = mock_store

            result = await _try_playbook_shortcut(
                model=MagicMock(),
                namespace="ns",
                slug="test-slug",
                mitigation_playbooks_dir=tmp_path,
                shared_file=sf,
                submit_mcp_url="http://test/submit",
                renderer=MagicMock(),
                usage_collector=MagicMock(),
            )
            assert result is None
            content = shared_path.read_text()
            assert "Not Applied" in content

    @pytest.mark.anyio
    async def test_subagent_exception_returns_none(self, tmp_path: Path):
        shared_path = tmp_path / "shared.md"
        shared_path.write_text("# Session\n")

        from sregym_agents.crucible.tools import SharedFile

        sf = SharedFile(shared_path)

        with (
            patch(
                "sregym_agents.crucible.tools._kb_tools.load_mitigation_playbook_text",
                return_value="# Playbook\nsome content",
            ),
            patch(
                "sregym_agents.crucible.knowledge_base.mitigation_playbook.MitigationPlaybookStore",
            ) as mock_store_cls,
            patch(
                "sregym_agents.crucible.tools._kb_tools.run_single_mitigation_playbook",
                new_callable=AsyncMock,
                side_effect=RuntimeError("subagent boom"),
            ),
        ):
            mock_store = MagicMock()
            mock_pb = MagicMock()
            mock_pb.class_name = "test-class"
            mock_store.load.return_value = mock_pb
            mock_store_cls.return_value = mock_store

            result = await _try_playbook_shortcut(
                model=MagicMock(),
                namespace="ns",
                slug="test-slug",
                mitigation_playbooks_dir=tmp_path,
                shared_file=sf,
                submit_mcp_url="http://test/submit",
                renderer=MagicMock(),
                usage_collector=MagicMock(),
            )
            assert result is None
            content = shared_path.read_text()
            assert "Subagent Error" in content


# ---------------------------------------------------------------------------
# Config flag
# ---------------------------------------------------------------------------


class TestConfigFlag:
    def test_defaults_to_false(self):
        from sregym_agents.crucible.config import CrucibleConfig

        assert CrucibleConfig().enable_playbook_shortcut is False

    def test_parsed_from_experiment_agent(self):
        from sregym_agents.crucible.config import crucible_config_from_experiment_agent

        cfg = crucible_config_from_experiment_agent({"prompt_version": "v3", "enable_playbook_shortcut": True})
        assert cfg.enable_playbook_shortcut is True
