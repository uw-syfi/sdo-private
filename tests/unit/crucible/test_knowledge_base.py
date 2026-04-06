"""Tests for sregym_agents.crucible.knowledge_base."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

if TYPE_CHECKING:
    from pathlib import Path

import pytest

from sregym_agents.crucible._prompts import PromptRenderer
from sregym_agents.crucible.knowledge_base import (
    KB_APPEND_FILENAME,
    KB_ARCHITECTURE_FILENAME,
    KB_INCIDENTS_DIRNAME,
    KB_LESSONS_FILENAME,
    KB_SUMMARY_FILENAME,
    MAX_INJECTED_INCIDENTS,
    AppendOnlyKnowledgeBase,
    HeuristicRefiner,
    InjectedKB,
    SessionFiles,
    StructuredKnowledgeBase,
    _extract_citations,
    _find_invalid_citations,
    _sanitize_app_name,
    _strip_benchmark_result,
    _strip_citation_wrappers,
    create_knowledge_base,
)
from sregym_agents.crucible.orchestrator import CrucibleFlags

_renderer = PromptRenderer("v1")


@pytest.fixture
def tmp_kb(tmp_path: Path) -> tuple[StructuredKnowledgeBase, Path, Path]:
    """Return (kb, kb_dir, target_dir)."""
    kb_dir = tmp_path / "kb"
    target_dir = tmp_path / "target"
    target_dir.mkdir()
    kb = StructuredKnowledgeBase(kb_dir, model_id="test-model", app_name="test-app", renderer=_renderer)
    return kb, kb_dir, target_dir


class TestStripBenchmarkResult:
    def test_removes_single_block(self):
        text = "before <benchmark_result>secret</benchmark_result> after"
        assert _strip_benchmark_result(text) == "before  after"

    def test_removes_multiple_blocks(self):
        text = "<benchmark_result>a</benchmark_result> mid <benchmark_result>b</benchmark_result>"
        assert _strip_benchmark_result(text) == "mid"

    def test_no_match_returns_original(self):
        text = "no tags here"
        assert _strip_benchmark_result(text) == "no tags here"

    def test_multiline_block(self):
        text = "start\n<benchmark_result>\nline1\nline2\n</benchmark_result>\nend"
        assert _strip_benchmark_result(text) == "start\n\nend"


class TestSanitizeAppName:
    def test_lowercase_and_strip_special(self):
        assert _sanitize_app_name("Hotel Reservation!") == "hotel_reservation"

    def test_already_clean(self):
        assert _sanitize_app_name("socialnetwork") == "socialnetwork"

    def test_special_chars(self):
        assert _sanitize_app_name("app/with@special#chars") == "app_with_special_chars"

    def test_empty_string(self):
        assert _sanitize_app_name("") == "unknown"

    def test_only_special_chars(self):
        assert _sanitize_app_name("@#$") == "unknown"

    def test_hyphens_and_underscores_preserved(self):
        assert _sanitize_app_name("my-app_v2") == "my-app_v2"


class TestKBDirCreatedOnInit:
    def test_kb_dir_created_on_init(self, tmp_path: Path):
        kb_dir = tmp_path / "nested" / "kb"
        StructuredKnowledgeBase(kb_dir, model_id="m", app_name="myapp", renderer=_renderer)
        assert kb_dir.is_dir()

    def test_app_subdir_created_on_init(self, tmp_path: Path):
        kb_dir = tmp_path / "kb"
        kb = StructuredKnowledgeBase(kb_dir, model_id="m", app_name="My App!", renderer=_renderer)
        assert kb.app_dir.is_dir()
        assert kb.app_dir.name == "my_app"


class TestInject:
    async def test_inject_no_prior_files(self, tmp_kb):
        kb, _kb_dir, target_dir = tmp_kb
        result = await kb.inject(target_dir)
        assert isinstance(result, InjectedKB)
        assert result.summary is None
        assert result.lessons is None
        assert result.architecture is None

    async def test_inject_copies_existing_files(self, tmp_kb):
        kb, kb_dir, target_dir = tmp_kb
        kb.summary_path.write_text("summary content")
        (kb_dir / KB_LESSONS_FILENAME).write_text("lessons content")

        result = await kb.inject(target_dir)

        assert result.summary == target_dir / KB_SUMMARY_FILENAME
        assert result.summary.read_text() == "summary content"
        assert result.lessons == target_dir / KB_LESSONS_FILENAME
        assert result.lessons.read_text() == "lessons content"

    async def test_inject_partial_summary_only(self, tmp_kb):
        kb, _kb_dir, target_dir = tmp_kb
        kb.summary_path.write_text("summary only")

        result = await kb.inject(target_dir)

        assert result.summary == target_dir / KB_SUMMARY_FILENAME
        assert result.summary.read_text() == "summary only"
        assert result.lessons is None

    async def test_inject_copies_architecture_file(self, tmp_kb):
        kb, _kb_dir, target_dir = tmp_kb
        kb.architecture_path.write_text("arch content")

        result = await kb.inject(target_dir)

        assert result.architecture == target_dir / KB_ARCHITECTURE_FILENAME
        assert result.architecture.read_text() == "arch content"

    async def test_inject_no_architecture_file(self, tmp_kb):
        kb, _kb_dir, target_dir = tmp_kb
        result = await kb.inject(target_dir)
        assert result.architecture is None

    async def test_inject_warns_missing_architecture(self, tmp_kb, caplog):
        kb, _kb_dir, target_dir = tmp_kb
        assert not kb.architecture_path.exists()

        with caplog.at_level(logging.WARNING):
            await kb.inject(target_dir)

        assert any("no architecture file" in r.message for r in caplog.records)


class TestSaveIncident:
    def test_creates_file_with_content(self, tmp_kb):
        kb, _kb_dir, _target_dir = tmp_kb
        incident_id = kb._save_incident("incident details", "session content here")

        path = kb.incidents_dir / f"{incident_id}.md"
        assert path.exists()
        assert path.read_text() == "incident details\n\n---\n\nsession content here"

    def test_returns_timestamp_id(self, tmp_kb):
        import re as re_mod

        kb, _kb_dir, _target_dir = tmp_kb
        incident_id = kb._save_incident("some summary", "some content")
        assert re_mod.match(r"\d{8}_\d{6}$", incident_id)


class TestInjectIncidents:
    async def test_copies_recent_incidents(self, tmp_kb):
        kb, _kb_dir, target_dir = tmp_kb
        kb.incidents_dir.mkdir(parents=True, exist_ok=True)
        for i in range(3):
            (kb.incidents_dir / f"2025010{i}_120000.md").write_text(f"incident {i}")

        await kb.inject(target_dir)

        dest = target_dir / KB_INCIDENTS_DIRNAME
        assert dest.is_dir()
        assert len(list(dest.glob("*.md"))) == 3

    async def test_limits_to_max_incidents(self, tmp_kb):
        kb, _kb_dir, target_dir = tmp_kb
        kb.incidents_dir.mkdir(parents=True, exist_ok=True)
        for i in range(MAX_INJECTED_INCIDENTS + 5):
            (kb.incidents_dir / f"20250{i:03d}_120000.md").write_text(f"incident {i}")

        await kb.inject(target_dir)

        dest = target_dir / KB_INCIDENTS_DIRNAME
        assert len(list(dest.glob("*.md"))) == MAX_INJECTED_INCIDENTS

    async def test_no_incidents_dir(self, tmp_kb):
        kb, _kb_dir, target_dir = tmp_kb
        assert not kb.incidents_dir.exists()
        # Should not crash
        await kb.inject(target_dir)
        assert not (target_dir / KB_INCIDENTS_DIRNAME).exists()

    async def test_skips_incidents_when_disabled(self, tmp_path: Path):
        kb_dir = tmp_path / "kb"
        target_dir = tmp_path / "target"
        target_dir.mkdir()
        kb = StructuredKnowledgeBase(
            kb_dir,
            model_id="m",
            app_name="test-app",
            flags=CrucibleFlags(include_incident_files=False),
            renderer=_renderer,
        )
        kb.incidents_dir.mkdir(parents=True, exist_ok=True)
        for i in range(3):
            (kb.incidents_dir / f"2025010{i}_120000.md").write_text(f"incident {i}")

        result = await kb.inject(target_dir)

        assert result.incidents_dir is None
        assert not (target_dir / KB_INCIDENTS_DIRNAME).exists()


class TestUpdate:
    async def test_update_skips_missing_shared_file(self, tmp_path: Path):
        kb = StructuredKnowledgeBase(tmp_path / "kb", model_id="m", app_name="test-app", renderer=_renderer)
        # Should not raise
        await kb.update(SessionFiles(diagnosis=tmp_path / "nonexistent.md"))
        assert not kb.summary_path.exists()

    async def test_update_skips_empty_content(self, tmp_path: Path):
        shared = tmp_path / "shared.md"
        shared.write_text("<benchmark_result>only this</benchmark_result>")
        kb = StructuredKnowledgeBase(tmp_path / "kb", model_id="m", app_name="test-app", renderer=_renderer)
        await kb.update(SessionFiles(diagnosis=shared))
        assert not kb.summary_path.exists()

    @patch.object(StructuredKnowledgeBase, "_merge_into_long_term_summary", new_callable=AsyncMock)
    @patch.object(StructuredKnowledgeBase, "_call_llm", new_callable=AsyncMock)
    async def test_update_full_flow(self, mock_llm, mock_merge, tmp_path: Path):
        shared = tmp_path / "shared.md"
        shared.write_text("real session data")
        kb = StructuredKnowledgeBase(tmp_path / "kb", model_id="m", app_name="test-app", renderer=_renderer)

        mock_llm.side_effect = [
            "session summary",  # _summarize_session
            "distilled lessons",  # _extract_operational_lessons
        ]
        mock_merge.return_value = "merged summary"

        await kb.update(SessionFiles(diagnosis=shared))

        assert kb.summary_path.read_text() == "merged summary"
        assert kb.lessons_path.read_text() == "distilled lessons"
        assert mock_llm.call_count == 2
        assert mock_merge.call_count == 1
        # Verify incident file was created
        incident_files = list(kb.incidents_dir.glob("*.md"))
        assert len(incident_files) == 1
        assert incident_files[0].read_text() == "session summary\n\n---\n\nreal session data"

    @patch.object(StructuredKnowledgeBase, "_merge_into_long_term_summary", new_callable=AsyncMock)
    @patch.object(StructuredKnowledgeBase, "_call_llm", new_callable=AsyncMock)
    async def test_update_skips_incident_save_when_disabled(self, mock_llm, mock_merge, tmp_path: Path):
        shared = tmp_path / "shared.md"
        shared.write_text("real session data")
        kb = StructuredKnowledgeBase(
            tmp_path / "kb",
            model_id="m",
            app_name="test-app",
            flags=CrucibleFlags(include_incident_files=False),
            renderer=_renderer,
        )

        mock_llm.side_effect = [
            "session summary",  # _summarize_session
            "distilled lessons",  # _extract_operational_lessons
        ]
        mock_merge.return_value = "merged summary"

        await kb.update(SessionFiles(diagnosis=shared))

        assert kb.summary_path.read_text() == "merged summary"
        # No incident files should be created
        assert not kb.incidents_dir.exists() or len(list(kb.incidents_dir.glob("*.md"))) == 0
        # Merge should have been called with empty incident_ref
        mock_merge.assert_called_once_with("session summary", "", incident_ref="")

    @patch.object(StructuredKnowledgeBase, "_call_llm", new_callable=AsyncMock)
    async def test_distill_lessons_reads_all_app_summaries(self, mock_llm, tmp_path: Path):
        kb_dir = tmp_path / "kb"

        # Create multiple per-app summary files
        for app, content in [("app-a", "summary A"), ("app-b", "summary B")]:
            app_dir = kb_dir / app
            app_dir.mkdir(parents=True, exist_ok=True)
            (app_dir / KB_SUMMARY_FILENAME).write_text(content)

        kb = StructuredKnowledgeBase(kb_dir, model_id="m", app_name="app-a", renderer=_renderer)
        mock_llm.return_value = "combined lessons"

        await kb._distill_lessons()

        # The LLM should have been called with combined content from both apps
        assert mock_llm.call_count == 1
        prompt_arg = mock_llm.call_args[0][0]
        assert "summary A" in prompt_arg
        assert "summary B" in prompt_arg

    def test_summary_path_uses_app_subdir(self, tmp_kb):
        kb, kb_dir, _target_dir = tmp_kb
        assert kb.summary_path == kb_dir / "test-app" / KB_SUMMARY_FILENAME


class TestSeedKB:
    def test_seed_copies_per_app_summary(self, tmp_path: Path):
        seed_dir = tmp_path / "seed"
        (seed_dir / "myapp").mkdir(parents=True)
        (seed_dir / "myapp" / KB_SUMMARY_FILENAME).write_text("seeded summary")

        kb = StructuredKnowledgeBase(
            tmp_path / "kb",
            model_id="m",
            app_name="myapp",
            seed_kb_dir=seed_dir,
            renderer=_renderer,
        )
        assert kb.summary_path.read_text() == "seeded summary"

    def test_seed_copies_incidents(self, tmp_path: Path):
        seed_dir = tmp_path / "seed"
        incidents = seed_dir / "myapp" / KB_INCIDENTS_DIRNAME
        incidents.mkdir(parents=True)
        (incidents / "20260101_120000.md").write_text("incident content")

        kb = StructuredKnowledgeBase(
            tmp_path / "kb",
            model_id="m",
            app_name="myapp",
            seed_kb_dir=seed_dir,
            renderer=_renderer,
        )
        copied = list(kb.incidents_dir.glob("*.md"))
        assert len(copied) == 1
        assert copied[0].read_text() == "incident content"

    def test_seed_copies_lessons(self, tmp_path: Path):
        seed_dir = tmp_path / "seed"
        seed_dir.mkdir(parents=True)
        (seed_dir / KB_LESSONS_FILENAME).write_text("seeded lessons")

        kb = StructuredKnowledgeBase(
            tmp_path / "kb",
            model_id="m",
            app_name="myapp",
            seed_kb_dir=seed_dir,
            renderer=_renderer,
        )
        assert kb.lessons_path.read_text() == "seeded lessons"

    def test_seed_copies_architecture(self, tmp_path: Path):
        seed_dir = tmp_path / "seed"
        (seed_dir / "myapp").mkdir(parents=True)
        (seed_dir / "myapp" / KB_ARCHITECTURE_FILENAME).write_text("arch info")

        kb = StructuredKnowledgeBase(
            tmp_path / "kb",
            model_id="m",
            app_name="myapp",
            seed_kb_dir=seed_dir,
            renderer=_renderer,
        )
        assert kb.architecture_path.read_text() == "arch info"

    def test_seed_does_not_overwrite_existing_architecture(self, tmp_path: Path):
        seed_dir = tmp_path / "seed"
        (seed_dir / "myapp").mkdir(parents=True)
        (seed_dir / "myapp" / KB_ARCHITECTURE_FILENAME).write_text("seeded arch")

        kb_dir = tmp_path / "kb"
        app_dir = kb_dir / "myapp"
        app_dir.mkdir(parents=True)
        (app_dir / KB_ARCHITECTURE_FILENAME).write_text("existing arch")

        kb = StructuredKnowledgeBase(
            kb_dir,
            model_id="m",
            app_name="myapp",
            seed_kb_dir=seed_dir,
            renderer=_renderer,
        )
        assert kb.architecture_path.read_text() == "existing arch"

    def test_seed_warns_missing_architecture(self, tmp_path: Path, caplog):
        seed_dir = tmp_path / "seed"
        (seed_dir / "myapp").mkdir(parents=True)
        # App dir exists but no architecture.md

        with caplog.at_level(logging.WARNING):
            StructuredKnowledgeBase(
                tmp_path / "kb",
                model_id="m",
                app_name="myapp",
                seed_kb_dir=seed_dir,
                renderer=_renderer,
            )

        assert any(KB_ARCHITECTURE_FILENAME in r.message for r in caplog.records)

    def test_seed_does_not_overwrite_existing(self, tmp_path: Path):
        seed_dir = tmp_path / "seed"
        (seed_dir / "myapp").mkdir(parents=True)
        (seed_dir / "myapp" / KB_SUMMARY_FILENAME).write_text("seeded summary")

        # Pre-create the KB with existing summary
        kb_dir = tmp_path / "kb"
        app_dir = kb_dir / "myapp"
        app_dir.mkdir(parents=True)
        (app_dir / KB_SUMMARY_FILENAME).write_text("existing summary")

        kb = StructuredKnowledgeBase(
            kb_dir,
            model_id="m",
            app_name="myapp",
            seed_kb_dir=seed_dir,
            renderer=_renderer,
        )
        assert kb.summary_path.read_text() == "existing summary"

    def test_seed_no_matching_app(self, tmp_path: Path):
        seed_dir = tmp_path / "seed"
        (seed_dir / "otherapp").mkdir(parents=True)
        (seed_dir / "otherapp" / KB_SUMMARY_FILENAME).write_text("other summary")

        kb = StructuredKnowledgeBase(
            tmp_path / "kb",
            model_id="m",
            app_name="myapp",
            seed_kb_dir=seed_dir,
            renderer=_renderer,
        )
        assert not kb.summary_path.exists()

    def test_no_seed_dir(self, tmp_path: Path):
        kb = StructuredKnowledgeBase(
            tmp_path / "kb",
            model_id="m",
            app_name="myapp",
            seed_kb_dir=None,
            renderer=_renderer,
        )
        assert not kb.summary_path.exists()


class TestAppendOnlyInject:
    async def test_inject_no_prior_file(self, tmp_path: Path):
        kb = AppendOnlyKnowledgeBase(tmp_path / "kb", model_id="m", renderer=_renderer)
        target = tmp_path / "target"
        target.mkdir()

        result = await kb.inject(target)

        assert isinstance(result, InjectedKB)
        assert result.summary is None
        assert result.lessons is None
        assert result.architecture is None
        assert result.incidents_dir is None

    async def test_inject_copies_existing_file(self, tmp_path: Path):
        kb = AppendOnlyKnowledgeBase(tmp_path / "kb", model_id="m", renderer=_renderer)
        kb.knowledge_path.write_text("prior knowledge")
        target = tmp_path / "target"
        target.mkdir()

        result = await kb.inject(target)

        assert result.summary == target / KB_APPEND_FILENAME
        assert result.summary is not None
        assert result.summary.read_text() == "prior knowledge"
        assert result.lessons is None
        assert result.architecture is None
        assert result.incidents_dir is None

    async def test_extract_triage_additions_returns_empty(self, tmp_path: Path):
        kb = AppendOnlyKnowledgeBase(tmp_path / "kb", model_id="m", renderer=_renderer)
        assert await kb.extract_triage_additions() == ""


class TestAppendOnlyUpdate:
    async def test_update_skips_missing_shared_file(self, tmp_path: Path):
        kb = AppendOnlyKnowledgeBase(tmp_path / "kb", model_id="m", renderer=_renderer)
        await kb.update(SessionFiles(diagnosis=tmp_path / "nonexistent.md"))
        assert not kb.knowledge_path.exists()

    async def test_update_skips_empty_content(self, tmp_path: Path):
        shared = tmp_path / "shared.md"
        shared.write_text("<benchmark_result>only this</benchmark_result>")
        kb = AppendOnlyKnowledgeBase(tmp_path / "kb", model_id="m", renderer=_renderer)
        await kb.update(SessionFiles(diagnosis=shared))
        assert not kb.knowledge_path.exists()

    @patch.object(AppendOnlyKnowledgeBase, "_call_llm", new_callable=AsyncMock)
    async def test_update_appends_summary(self, mock_llm, tmp_path: Path):
        shared = tmp_path / "shared.md"
        shared.write_text("session data")
        kb = AppendOnlyKnowledgeBase(tmp_path / "kb", model_id="m", renderer=_renderer)

        mock_llm.return_value = "session summary"
        await kb.update(SessionFiles(diagnosis=shared))

        assert kb.knowledge_path.exists()
        text = kb.knowledge_path.read_text()
        assert "session summary" in text
        assert mock_llm.call_count == 1

    @patch.object(AppendOnlyKnowledgeBase, "_call_llm", new_callable=AsyncMock)
    async def test_update_appends_multiple(self, mock_llm, tmp_path: Path):
        shared = tmp_path / "shared.md"
        shared.write_text("session data")
        kb = AppendOnlyKnowledgeBase(tmp_path / "kb", model_id="m", renderer=_renderer)

        mock_llm.return_value = "summary 1"
        await kb.update(SessionFiles(diagnosis=shared))

        mock_llm.return_value = "summary 2"
        await kb.update(SessionFiles(diagnosis=shared))

        text = kb.knowledge_path.read_text()
        assert "summary 1" in text
        assert "summary 2" in text
        assert text.count("---") == 2


class TestCreateKnowledgeBase:
    def test_structured(self, tmp_path: Path):
        kb = create_knowledge_base("structured", tmp_path / "kb", model_id="m", renderer=_renderer)
        assert isinstance(kb, StructuredKnowledgeBase)

    def test_append_only(self, tmp_path: Path):
        kb = create_knowledge_base("append-only", tmp_path / "kb", model_id="m", renderer=_renderer)
        assert isinstance(kb, AppendOnlyKnowledgeBase)

    def test_invalid_raises(self, tmp_path: Path):
        with pytest.raises(ValueError, match="Unknown kb_type"):
            create_knowledge_base("invalid", tmp_path / "kb", model_id="m", renderer=_renderer)

    def test_include_incident_files_forwarded(self, tmp_path: Path):
        kb = create_knowledge_base(
            "structured",
            tmp_path / "kb",
            model_id="m",
            flags=CrucibleFlags(include_incident_files=False),
            renderer=_renderer,
        )
        assert isinstance(kb, StructuredKnowledgeBase)
        assert kb.include_incident_files is False

    def test_include_benchmark_results_forwarded(self, tmp_path: Path):
        kb = create_knowledge_base(
            "structured",
            tmp_path / "kb",
            model_id="m",
            flags=CrucibleFlags(include_benchmark_results=True),
            renderer=_renderer,
        )
        assert isinstance(kb, StructuredKnowledgeBase)
        assert kb.include_benchmark_results is True

    def test_include_benchmark_results_forwarded_append_only(self, tmp_path: Path):
        kb = create_knowledge_base(
            "append-only",
            tmp_path / "kb",
            model_id="m",
            flags=CrucibleFlags(include_benchmark_results=True),
            renderer=_renderer,
        )
        assert isinstance(kb, AppendOnlyKnowledgeBase)
        assert kb.include_benchmark_results is True


class TestIncludeBenchmarkResults:
    """Tests for the include_benchmark_results toggle."""

    @patch.object(StructuredKnowledgeBase, "_call_llm", new_callable=AsyncMock)
    async def test_structured_preserves_benchmark_when_enabled(self, mock_llm, tmp_path: Path):
        shared = tmp_path / "shared.md"
        shared.write_text("data <benchmark_result>ground truth</benchmark_result> more data")
        kb = StructuredKnowledgeBase(
            tmp_path / "kb",
            model_id="m",
            app_name="test-app",
            flags=CrucibleFlags(include_benchmark_results=True),
            renderer=_renderer,
        )
        mock_llm.side_effect = ["session summary", "merged summary", "lessons"]
        await kb.update(SessionFiles(diagnosis=shared))

        # First LLM call is _summarize_session — content should include benchmark block
        summarize_prompt = mock_llm.call_args_list[0][0][0]
        assert "<benchmark_result>" in summarize_prompt
        assert "ground truth" in summarize_prompt

    @patch.object(StructuredKnowledgeBase, "_call_llm", new_callable=AsyncMock)
    async def test_structured_strips_benchmark_when_disabled(self, mock_llm, tmp_path: Path):
        shared = tmp_path / "shared.md"
        shared.write_text("data <benchmark_result>ground truth</benchmark_result> more data")
        kb = StructuredKnowledgeBase(
            tmp_path / "kb",
            model_id="m",
            app_name="test-app",
            flags=CrucibleFlags(include_benchmark_results=False),
            renderer=_renderer,
        )
        mock_llm.side_effect = ["session summary", "merged summary", "lessons"]
        await kb.update(SessionFiles(diagnosis=shared))

        summarize_prompt = mock_llm.call_args_list[0][0][0]
        assert "<benchmark_result>" not in summarize_prompt
        assert "ground truth" not in summarize_prompt

    @patch.object(AppendOnlyKnowledgeBase, "_call_llm", new_callable=AsyncMock)
    async def test_append_only_preserves_benchmark_when_enabled(self, mock_llm, tmp_path: Path):
        shared = tmp_path / "shared.md"
        shared.write_text("data <benchmark_result>ground truth</benchmark_result> more data")
        kb = AppendOnlyKnowledgeBase(
            tmp_path / "kb",
            model_id="m",
            flags=CrucibleFlags(include_benchmark_results=True),
            renderer=_renderer,
        )
        mock_llm.return_value = "session summary"
        await kb.update(SessionFiles(diagnosis=shared))

        summarize_prompt = mock_llm.call_args_list[0][0][0]
        assert "<benchmark_result>" in summarize_prompt
        assert "ground truth" in summarize_prompt

    @patch.object(AppendOnlyKnowledgeBase, "_call_llm", new_callable=AsyncMock)
    async def test_append_only_strips_benchmark_when_disabled(self, mock_llm, tmp_path: Path):
        shared = tmp_path / "shared.md"
        shared.write_text("data <benchmark_result>ground truth</benchmark_result> more data")
        kb = AppendOnlyKnowledgeBase(
            tmp_path / "kb",
            model_id="m",
            flags=CrucibleFlags(include_benchmark_results=False),
            renderer=_renderer,
        )
        mock_llm.return_value = "session summary"
        await kb.update(SessionFiles(diagnosis=shared))

        summarize_prompt = mock_llm.call_args_list[0][0][0]
        assert "<benchmark_result>" not in summarize_prompt

    async def test_structured_empty_after_strip_still_skips(self, tmp_path: Path):
        """When include_benchmark_results=True but content is only whitespace, still skip."""
        shared = tmp_path / "shared.md"
        shared.write_text("   ")
        kb = StructuredKnowledgeBase(
            tmp_path / "kb",
            model_id="m",
            app_name="test-app",
            flags=CrucibleFlags(include_benchmark_results=True),
            renderer=_renderer,
        )
        await kb.update(SessionFiles(diagnosis=shared))
        assert not kb.summary_path.exists()


class TestCitationValidation:
    def test_extract_citations(self):
        text = "root cause (1 incidents, {{ref:incidents/20260324_010224.md}}) and {{ref:incidents/20260324_010545.md}}"
        assert _extract_citations(text) == [
            "incidents/20260324_010224.md",
            "incidents/20260324_010545.md",
        ]

    def test_extract_citations_none(self):
        assert _extract_citations("no citations here") == []

    def test_find_invalid_citations_all_valid(self, tmp_path: Path):
        incidents_dir = tmp_path / "incidents"
        incidents_dir.mkdir()
        (incidents_dir / "20260324_010224.md").write_text("content")
        text = "some text {{ref:incidents/20260324_010224.md}} more"
        assert _find_invalid_citations(text, incidents_dir) == []

    def test_find_invalid_citations_some_invalid(self, tmp_path: Path):
        incidents_dir = tmp_path / "incidents"
        incidents_dir.mkdir()
        (incidents_dir / "20260324_010224.md").write_text("content")
        text = "{{ref:incidents/20260324_010224.md}} and {{ref:incidents/fake_file.md}}"
        assert _find_invalid_citations(text, incidents_dir) == ["incidents/fake_file.md"]

    def test_strip_citation_wrappers(self):
        text = "root cause (1 incidents, {{ref:incidents/20260324_010224.md}})"
        assert _strip_citation_wrappers(text) == "root cause (1 incidents, incidents/20260324_010224.md)"

    def test_strip_citation_wrappers_multiple(self):
        text = "{{ref:incidents/a.md}} and {{ref:incidents/b.md}}"
        assert _strip_citation_wrappers(text) == "incidents/a.md and incidents/b.md"

    @patch.object(StructuredKnowledgeBase, "_call_llm", new_callable=AsyncMock)
    async def test_merge_correction_loop_fixes_bad_citation(self, mock_call_llm, tmp_path: Path):
        """When LLM produces invalid citation, correction loop fixes it."""
        shared = tmp_path / "shared.md"
        shared.write_text("session data")
        kb = StructuredKnowledgeBase(tmp_path / "kb", model_id="m", app_name="test-app", renderer=_renderer)

        # Create an incident file so we have a valid reference
        kb.incidents_dir.mkdir(parents=True, exist_ok=True)
        (kb.incidents_dir / "20260324_010224.md").write_text("incident")

        bad_output = "root cause (1 incidents, {{ref:incidents/fake_20240730.md}})"
        good_output = "root cause (1 incidents, {{ref:incidents/20260324_010224.md}})"

        # Mock arun_with_retry to return bad then good output
        mock_result_bad = AsyncMock()
        mock_result_bad.output = bad_output
        mock_result_bad.all_messages = lambda: [{"role": "assistant", "content": bad_output}]

        mock_result_good = AsyncMock()
        mock_result_good.output = good_output
        mock_result_good.all_messages = lambda: [{"role": "assistant", "content": good_output}]

        with (
            patch("sregym_agents.crucible.knowledge_base.structured.Agent"),
            patch(
                "sregym_agents.crucible.knowledge_base.structured.arun_with_retry",
                new_callable=AsyncMock,
                side_effect=[mock_result_bad, mock_result_good],
            ) as mock_retry,
        ):
            result = await kb._merge_into_long_term_summary(
                "session summary", "", incident_ref="incidents/20260324_010224.md"
            )

        # Should have stripped the wrapper and used the corrected citation
        assert result == "root cause (1 incidents, incidents/20260324_010224.md)"
        # arun_with_retry should have been called twice (initial + 1 correction)
        assert mock_retry.call_count == 2

    @patch.object(StructuredKnowledgeBase, "_call_llm", new_callable=AsyncMock)
    async def test_merge_no_correction_when_citations_valid(self, mock_call_llm, tmp_path: Path):
        """When LLM produces valid citations, no correction loop runs."""
        shared = tmp_path / "shared.md"
        shared.write_text("session data")
        kb = StructuredKnowledgeBase(tmp_path / "kb", model_id="m", app_name="test-app", renderer=_renderer)

        kb.incidents_dir.mkdir(parents=True, exist_ok=True)
        (kb.incidents_dir / "20260324_010224.md").write_text("incident")

        good_output = "root cause (1 incidents, {{ref:incidents/20260324_010224.md}})"

        mock_result = AsyncMock()
        mock_result.output = good_output
        mock_result.all_messages = list

        with (
            patch("sregym_agents.crucible.knowledge_base.structured.Agent"),
            patch(
                "sregym_agents.crucible.knowledge_base.structured.arun_with_retry",
                new_callable=AsyncMock,
                return_value=mock_result,
            ) as mock_retry,
        ):
            result = await kb._merge_into_long_term_summary(
                "session summary", "", incident_ref="incidents/20260324_010224.md"
            )

        assert result == "root cause (1 incidents, incidents/20260324_010224.md)"
        assert mock_retry.call_count == 1


class TestHeuristicRefiner:
    def _make_refiner(self, tmp_path: Path) -> HeuristicRefiner:
        return HeuristicRefiner(tmp_path / "kb", model_id="test-model", renderer=_renderer)

    async def test_refine_skips_when_no_session_content(self, tmp_path: Path, caplog):
        refiner = self._make_refiner(tmp_path)
        session_files = SessionFiles(diagnosis=tmp_path / "nonexistent.md")
        with caplog.at_level(logging.INFO, logger="sregym_agents.crucible.knowledge_base"):
            await refiner.refine(session_files)
        assert "No shared session content" in caplog.text
        assert not refiner.diagnosis_heuristics_path.exists()

    @patch.object(HeuristicRefiner, "_classify_failure", new_callable=AsyncMock)
    @patch.object(HeuristicRefiner, "_refine_diagnosis_heuristics", new_callable=AsyncMock)
    @patch.object(HeuristicRefiner, "_refine_triage_heuristics", new_callable=AsyncMock)
    @patch.object(HeuristicRefiner, "_refine_arbitration_heuristics", new_callable=AsyncMock)
    async def test_refine_runs_diagnosis_on_reasoning_classification(
        self, mock_arb, mock_triage, mock_diag, mock_classify, tmp_path: Path
    ):
        session = tmp_path / "session.md"
        session.write_text("some session content")
        mock_classify.return_value = "Reasoning failure in diagnosis step"

        refiner = self._make_refiner(tmp_path)
        await refiner.refine(SessionFiles(diagnosis=session))

        mock_classify.assert_called_once()
        mock_diag.assert_called_once()
        mock_triage.assert_not_called()
        mock_arb.assert_not_called()

    @patch.object(HeuristicRefiner, "_classify_failure", new_callable=AsyncMock)
    @patch.object(HeuristicRefiner, "_refine_diagnosis_heuristics", new_callable=AsyncMock)
    @patch.object(HeuristicRefiner, "_refine_triage_heuristics", new_callable=AsyncMock)
    @patch.object(HeuristicRefiner, "_refine_arbitration_heuristics", new_callable=AsyncMock)
    async def test_refine_runs_triage_on_triage_classification(
        self, mock_arb, mock_triage, mock_diag, mock_classify, tmp_path: Path
    ):
        session = tmp_path / "session.md"
        session.write_text("session content")
        mock_classify.return_value = "Triage step failed to narrow down the fault"

        refiner = self._make_refiner(tmp_path)
        await refiner.refine(SessionFiles(diagnosis=session))

        mock_triage.assert_called_once()
        mock_diag.assert_not_called()
        mock_arb.assert_not_called()

    @patch.object(HeuristicRefiner, "_classify_failure", new_callable=AsyncMock)
    @patch.object(HeuristicRefiner, "_refine_diagnosis_heuristics", new_callable=AsyncMock)
    @patch.object(HeuristicRefiner, "_refine_triage_heuristics", new_callable=AsyncMock)
    @patch.object(HeuristicRefiner, "_refine_arbitration_heuristics", new_callable=AsyncMock)
    async def test_refine_runs_arbitration_on_arbitration_classification(
        self, mock_arb, mock_triage, mock_diag, mock_classify, tmp_path: Path
    ):
        session = tmp_path / "session.md"
        session.write_text("session content")
        mock_classify.return_value = "Arbitration disagreement between agents"

        refiner = self._make_refiner(tmp_path)
        await refiner.refine(SessionFiles(diagnosis=session))

        mock_arb.assert_called_once()
        mock_diag.assert_not_called()
        mock_triage.assert_not_called()

    @patch.object(HeuristicRefiner, "_classify_failure", new_callable=AsyncMock)
    async def test_refine_handles_classify_exception(self, mock_classify, tmp_path: Path, caplog):
        session = tmp_path / "session.md"
        session.write_text("session content")
        mock_classify.side_effect = RuntimeError("LLM error")

        refiner = self._make_refiner(tmp_path)
        with caplog.at_level(logging.ERROR, logger="sregym_agents.crucible.knowledge_base"):
            await refiner.refine(SessionFiles(diagnosis=session))

        assert "Failed to classify failure" in caplog.text
        assert not refiner.diagnosis_heuristics_path.exists()

    @patch.object(HeuristicRefiner, "_classify_failure", new_callable=AsyncMock)
    @patch.object(HeuristicRefiner, "_refine_diagnosis_heuristics", new_callable=AsyncMock)
    async def test_refine_logs_individual_refinement_errors(self, mock_diag, mock_classify, tmp_path: Path, caplog):
        session = tmp_path / "session.md"
        session.write_text("session content")
        mock_classify.return_value = "reasoning error"
        mock_diag.side_effect = RuntimeError("write failed")

        refiner = self._make_refiner(tmp_path)
        with caplog.at_level(logging.ERROR, logger="sregym_agents.crucible.knowledge_base"):
            await refiner.refine(SessionFiles(diagnosis=session))

        assert "Heuristic refinement error" in caplog.text

    @patch.object(HeuristicRefiner, "_classify_failure", new_callable=AsyncMock)
    @patch.object(HeuristicRefiner, "_refine_diagnosis_heuristics", new_callable=AsyncMock)
    @patch.object(HeuristicRefiner, "_refine_triage_heuristics", new_callable=AsyncMock)
    @patch.object(HeuristicRefiner, "_refine_arbitration_heuristics", new_callable=AsyncMock)
    async def test_refine_skips_when_no_keywords_match(
        self, mock_arb, mock_triage, mock_diag, mock_classify, tmp_path: Path, caplog
    ):
        session = tmp_path / "session.md"
        session.write_text("session content")
        mock_classify.return_value = "unknown category"

        refiner = self._make_refiner(tmp_path)
        with caplog.at_level(logging.INFO, logger="sregym_agents.crucible.knowledge_base"):
            await refiner.refine(SessionFiles(diagnosis=session))

        assert "No heuristic refinement needed" in caplog.text
        mock_diag.assert_not_called()
        mock_triage.assert_not_called()
        mock_arb.assert_not_called()
