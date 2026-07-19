"""Tests for benchmarks.sregym.agents.crucible.knowledge_base."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch
from unittest.mock import MagicMock as _MagicMock

if TYPE_CHECKING:
    from pathlib import Path

import pytest

from benchmarks.sregym.agents.crucible._prompts import PromptRenderer
from benchmarks.sregym.agents.crucible.agents.base import AgentResult as _AgentResult
from benchmarks.sregym.agents.crucible.config import CrucibleConfig
from benchmarks.sregym.agents.crucible.knowledge_base import SessionFiles, create_knowledge_base, seed_kb
from benchmarks.sregym.agents.crucible.knowledge_base.append_only import AppendOnlyKnowledgeBase
from benchmarks.sregym.agents.crucible.knowledge_base.base import (
    KB_APPEND_FILENAME,
    MAX_INJECTED_INCIDENTS,
    InjectedKB,
    extract_citations,
    find_invalid_citations,
    find_invalid_citations_unified,
    sanitize_app_name,
    strip_benchmark_result,
    strip_citation_wrappers,
)
from benchmarks.sregym.agents.crucible.knowledge_base.merge_result import MergeResult
from benchmarks.sregym.agents.crucible.knowledge_base.reflection import (
    PRIOR_FILES,
    STAGE_TO_PRIOR,
    FailureClassification,
    Reflector,
    StageFailure,
)
from benchmarks.sregym.agents.crucible.knowledge_base.schema import SCHEMA_V2
from benchmarks.sregym.agents.crucible.knowledge_base.structured import StructuredKnowledgeBase
from benchmarks.sregym.agents.crucible.recovery_reflection import RecoveryReflection, RecoveryStageFailure


def _merge_result(text: str = "merged summary") -> MergeResult:
    """Helper: build a noop MergeResult carrying the given summary text."""
    return MergeResult(
        primary_action="noop",
        primary_slug=None,
        primary_class_name=None,
        new_summary_text=text,
    )


# Use v2 schema filenames throughout tests
KB_SUMMARY_FILENAME = SCHEMA_V2.summary
KB_LESSONS_FILENAME = SCHEMA_V2.lessons
KB_ARCHITECTURE_FILENAME = SCHEMA_V2.architecture
KB_INCIDENTS_DIRNAME = SCHEMA_V2.incidents_dir

_renderer = PromptRenderer("v1")


def _make_mock_driver():
    d = _MagicMock()
    d.run = AsyncMock(return_value=_AgentResult(output="", completed=True, messages=[]))
    return d


@pytest.fixture
def mock_driver():
    return _make_mock_driver()


@pytest.fixture
def tmp_kb(tmp_path: Path, mock_driver) -> tuple[StructuredKnowledgeBase, Path, Path]:
    """Return (kb, kb_dir, target_dir)."""
    kb_dir = tmp_path / "kb"
    target_dir = tmp_path / "target"
    target_dir.mkdir()
    kb = StructuredKnowledgeBase(kb_dir, app_name="test-app", renderer=_renderer, driver=mock_driver)
    return kb, kb_dir, target_dir


class TestStripBenchmarkResult:
    def test_removes_single_block(self):
        text = "before <benchmark_result>secret</benchmark_result> after"
        assert strip_benchmark_result(text) == "before  after"

    def test_removes_multiple_blocks(self):
        text = "<benchmark_result>a</benchmark_result> mid <benchmark_result>b</benchmark_result>"
        assert strip_benchmark_result(text) == "mid"

    def test_no_match_returns_original(self):
        text = "no tags here"
        assert strip_benchmark_result(text) == "no tags here"

    def test_multiline_block(self):
        text = "start\n<benchmark_result>\nline1\nline2\n</benchmark_result>\nend"
        assert strip_benchmark_result(text) == "start\n\nend"


class TestSanitizeAppName:
    def test_lowercase_and_strip_special(self):
        assert sanitize_app_name("Hotel Reservation!") == "hotel_reservation"

    def test_already_clean(self):
        assert sanitize_app_name("socialnetwork") == "socialnetwork"

    def test_special_chars(self):
        assert sanitize_app_name("app/with@special#chars") == "app_with_special_chars"

    def test_empty_string(self):
        assert sanitize_app_name("") == "unknown"

    def test_only_special_chars(self):
        assert sanitize_app_name("@#$") == "unknown"

    def test_hyphens_and_underscores_preserved(self):
        assert sanitize_app_name("my-app_v2") == "my-app_v2"


class TestKBDirCreatedOnInit:
    def test_kb_dir_created_on_init(self, tmp_path: Path, mock_driver):
        kb_dir = tmp_path / "nested" / "kb"
        StructuredKnowledgeBase(kb_dir, app_name="myapp", renderer=_renderer, driver=mock_driver)
        assert kb_dir.is_dir()

    def test_app_subdir_created_on_init(self, tmp_path: Path, mock_driver):
        kb_dir = tmp_path / "kb"
        kb = StructuredKnowledgeBase(kb_dir, app_name="My App!", renderer=_renderer, driver=mock_driver)
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

    async def test_skips_incidents_when_disabled(self, tmp_path: Path, mock_driver):
        kb_dir = tmp_path / "kb"
        target_dir = tmp_path / "target"
        target_dir.mkdir()
        kb = StructuredKnowledgeBase(
            kb_dir,
            app_name="test-app",
            config=CrucibleConfig(include_incident_files=False),
            renderer=_renderer,
            driver=mock_driver,
        )
        kb.incidents_dir.mkdir(parents=True, exist_ok=True)
        for i in range(3):
            (kb.incidents_dir / f"2025010{i}_120000.md").write_text(f"incident {i}")

        result = await kb.inject(target_dir)

        assert result.incidents_dir is None
        assert not (target_dir / KB_INCIDENTS_DIRNAME).exists()


class TestUpdate:
    async def test_update_skips_missing_shared_file(self, tmp_path: Path, mock_driver):
        kb = StructuredKnowledgeBase(tmp_path / "kb", app_name="test-app", renderer=_renderer, driver=mock_driver)
        # Should not raise
        await kb.update(SessionFiles(diagnosis=tmp_path / "nonexistent.md"))
        assert not kb.summary_path.exists()

    async def test_update_skips_empty_content(self, tmp_path: Path, mock_driver):
        shared = tmp_path / "shared.md"
        shared.write_text("<benchmark_result>only this</benchmark_result>")
        kb = StructuredKnowledgeBase(tmp_path / "kb", app_name="test-app", renderer=_renderer, driver=mock_driver)
        await kb.update(SessionFiles(diagnosis=shared))
        assert not kb.summary_path.exists()

    @patch.object(StructuredKnowledgeBase, "_merge_into_long_term_summary", new_callable=AsyncMock)
    @patch.object(StructuredKnowledgeBase, "_call_llm", new_callable=AsyncMock)
    async def test_update_full_flow(self, mock_llm, mock_merge, tmp_path: Path, mock_driver):
        shared = tmp_path / "shared.md"
        shared.write_text("real session data")
        kb = StructuredKnowledgeBase(tmp_path / "kb", app_name="test-app", renderer=_renderer, driver=mock_driver)

        mock_llm.side_effect = [
            "session summary",  # _summarize_session
            "distilled lessons",  # _extract_operational_lessons
        ]
        mock_merge.return_value = _merge_result("merged summary")

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
    async def test_update_skips_incident_save_when_disabled(self, mock_llm, mock_merge, tmp_path: Path, mock_driver):
        shared = tmp_path / "shared.md"
        shared.write_text("real session data")
        kb = StructuredKnowledgeBase(
            tmp_path / "kb",
            app_name="test-app",
            config=CrucibleConfig(include_incident_files=False),
            renderer=_renderer,
            driver=mock_driver,
        )

        mock_llm.side_effect = [
            "session summary",  # _summarize_session
            "distilled lessons",  # _extract_operational_lessons
        ]
        mock_merge.return_value = _merge_result("merged summary")

        await kb.update(SessionFiles(diagnosis=shared))

        assert kb.summary_path.read_text() == "merged summary"
        # No incident files should be created
        assert not kb.incidents_dir.exists() or len(list(kb.incidents_dir.glob("*.md"))) == 0
        # Merge should have been called with empty incident_ref
        mock_merge.assert_called_once_with("session summary", "", incident_ref="")

    @patch.object(StructuredKnowledgeBase, "_call_llm", new_callable=AsyncMock)
    async def test_distill_lessons_reads_all_app_summaries(self, mock_llm, tmp_path: Path, mock_driver):
        kb_dir = tmp_path / "kb"

        # Create multiple per-app summary files
        for app, content in [("app-a", "summary A"), ("app-b", "summary B")]:
            app_dir = kb_dir / app
            app_dir.mkdir(parents=True, exist_ok=True)
            (app_dir / KB_SUMMARY_FILENAME).write_text(content)

        kb = StructuredKnowledgeBase(kb_dir, app_name="app-a", renderer=_renderer, driver=mock_driver)
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

    @patch.object(Reflector, "run", new_callable=AsyncMock)
    @patch.object(StructuredKnowledgeBase, "_merge_into_long_term_summary", new_callable=AsyncMock)
    @patch.object(StructuredKnowledgeBase, "_call_llm", new_callable=AsyncMock)
    async def test_update_passes_grounded_recovery_reflection_to_reflector(
        self,
        mock_llm,
        mock_merge,
        mock_reflector_run,
        tmp_path: Path,
        mock_driver,
    ):
        shared = tmp_path / "shared.md"
        shared.write_text("real session data")
        stage_outputs = tmp_path / "stage_outputs.md"
        stage_outputs.write_text("stage outputs")
        kb = StructuredKnowledgeBase(tmp_path / "kb", app_name="test-app", renderer=_renderer, driver=mock_driver)

        mock_llm.side_effect = ["session summary", "distilled lessons"]
        mock_merge.return_value = _merge_result("merged summary")
        grounded = {
            "summary": "Grounded recovery narrative",
            "stage_failures": [
                {
                    "stage": "verification",
                    "description": "Confirmed the wrong candidate",
                    "evidence": "Recovery traced the real dependency chain",
                    "lesson": "Verify upstream dependencies before confirming",
                }
            ],
            "investigation_observations": ["Recovery observed an upstream dependency failure"],
        }

        await kb.update(
            SessionFiles(diagnosis=shared),
            stage_outputs_file=stage_outputs,
            recovery_reflection=grounded,
        )

        _args, kwargs = mock_reflector_run.await_args
        assert kwargs["stage_outputs_file"] == stage_outputs
        assert kwargs["recovery_reflection"].summary == grounded["summary"]


class TestSeedKB:
    """Test the public `seed_kb` function that copies a seed KB into a dest dir."""

    def test_copies_nested_per_app_summary(self, tmp_path: Path):
        seed_dir = tmp_path / "seed"
        (seed_dir / "myapp").mkdir(parents=True)
        (seed_dir / "myapp" / KB_SUMMARY_FILENAME).write_text("seeded summary")

        dest = tmp_path / "kb"
        seed_kb(dest, seed_dir)

        assert (dest / "myapp" / KB_SUMMARY_FILENAME).read_text() == "seeded summary"

    def test_copies_incidents_tree(self, tmp_path: Path):
        seed_dir = tmp_path / "seed"
        incidents = seed_dir / "myapp" / KB_INCIDENTS_DIRNAME
        incidents.mkdir(parents=True)
        (incidents / "20260101_120000.md").write_text("incident content")

        dest = tmp_path / "kb"
        seed_kb(dest, seed_dir)

        copied = list((dest / "myapp" / KB_INCIDENTS_DIRNAME).glob("*.md"))
        assert len(copied) == 1
        assert copied[0].read_text() == "incident content"

    def test_copies_root_level_file(self, tmp_path: Path):
        seed_dir = tmp_path / "seed"
        seed_dir.mkdir(parents=True)
        (seed_dir / KB_LESSONS_FILENAME).write_text("seeded lessons")

        dest = tmp_path / "kb"
        seed_kb(dest, seed_dir)

        assert (dest / KB_LESSONS_FILENAME).read_text() == "seeded lessons"

    def test_does_not_overwrite_existing(self, tmp_path: Path):
        seed_dir = tmp_path / "seed"
        (seed_dir / "myapp").mkdir(parents=True)
        (seed_dir / "myapp" / KB_SUMMARY_FILENAME).write_text("seeded summary")

        dest = tmp_path / "kb"
        (dest / "myapp").mkdir(parents=True)
        (dest / "myapp" / KB_SUMMARY_FILENAME).write_text("existing summary")

        seed_kb(dest, seed_dir)

        assert (dest / "myapp" / KB_SUMMARY_FILENAME).read_text() == "existing summary"

    def test_no_seed_dir_is_noop(self, tmp_path: Path):
        dest = tmp_path / "kb"
        seed_kb(dest, None)
        assert not dest.exists() or not any(dest.iterdir())

    def test_empty_string_seed_is_noop(self, tmp_path: Path):
        dest = tmp_path / "kb"
        seed_kb(dest, "")
        assert not dest.exists() or not any(dest.iterdir())

    def test_missing_seed_dir_warns(self, tmp_path: Path, caplog):
        dest = tmp_path / "kb"
        missing = tmp_path / "does-not-exist"
        with caplog.at_level(logging.WARNING):
            seed_kb(dest, missing)
        assert any("does-not-exist" in r.message for r in caplog.records)


class TestAppendOnlyInject:
    async def test_inject_no_prior_file(self, tmp_path: Path, mock_driver):
        kb = AppendOnlyKnowledgeBase(tmp_path / "kb", renderer=_renderer, driver=mock_driver)
        target = tmp_path / "target"
        target.mkdir()

        result = await kb.inject(target)

        assert isinstance(result, InjectedKB)
        assert result.summary is None
        assert result.lessons is None
        assert result.architecture is None
        assert result.incidents_dir is None

    async def test_inject_copies_existing_file(self, tmp_path: Path, mock_driver):
        kb = AppendOnlyKnowledgeBase(tmp_path / "kb", renderer=_renderer, driver=mock_driver)
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


class TestAppendOnlyUpdate:
    async def test_update_skips_missing_shared_file(self, tmp_path: Path, mock_driver):
        kb = AppendOnlyKnowledgeBase(tmp_path / "kb", renderer=_renderer, driver=mock_driver)
        await kb.update(SessionFiles(diagnosis=tmp_path / "nonexistent.md"))
        assert not kb.knowledge_path.exists()

    async def test_update_skips_empty_content(self, tmp_path: Path, mock_driver):
        shared = tmp_path / "shared.md"
        shared.write_text("<benchmark_result>only this</benchmark_result>")
        kb = AppendOnlyKnowledgeBase(tmp_path / "kb", renderer=_renderer, driver=mock_driver)
        await kb.update(SessionFiles(diagnosis=shared))
        assert not kb.knowledge_path.exists()

    @patch.object(AppendOnlyKnowledgeBase, "_call_llm", new_callable=AsyncMock)
    async def test_update_appends_summary(self, mock_llm, tmp_path: Path, mock_driver):
        shared = tmp_path / "shared.md"
        shared.write_text("session data")
        kb = AppendOnlyKnowledgeBase(tmp_path / "kb", renderer=_renderer, driver=mock_driver)

        mock_llm.return_value = "session summary"
        await kb.update(SessionFiles(diagnosis=shared))

        assert kb.knowledge_path.exists()
        text = kb.knowledge_path.read_text()
        assert "session summary" in text
        assert mock_llm.call_count == 1

    @patch.object(AppendOnlyKnowledgeBase, "_call_llm", new_callable=AsyncMock)
    async def test_update_appends_multiple(self, mock_llm, tmp_path: Path, mock_driver):
        shared = tmp_path / "shared.md"
        shared.write_text("session data")
        kb = AppendOnlyKnowledgeBase(tmp_path / "kb", renderer=_renderer, driver=mock_driver)

        mock_llm.return_value = "summary 1"
        await kb.update(SessionFiles(diagnosis=shared))

        mock_llm.return_value = "summary 2"
        await kb.update(SessionFiles(diagnosis=shared))

        text = kb.knowledge_path.read_text()
        assert "summary 1" in text
        assert "summary 2" in text
        assert text.count("---") == 2


class TestCreateKnowledgeBase:
    def test_structured(self, tmp_path: Path, mock_driver):
        kb = create_knowledge_base("structured", tmp_path / "kb", renderer=_renderer, driver=mock_driver)
        assert isinstance(kb, StructuredKnowledgeBase)

    def test_append_only(self, tmp_path: Path, mock_driver):
        kb = create_knowledge_base("append-only", tmp_path / "kb", renderer=_renderer, driver=mock_driver)
        assert isinstance(kb, AppendOnlyKnowledgeBase)

    def test_invalid_raises(self, tmp_path: Path, mock_driver):
        with pytest.raises(ValueError, match="Unknown kb_type"):
            create_knowledge_base("invalid", tmp_path / "kb", renderer=_renderer, driver=mock_driver)

    def test_include_incident_files_forwarded(self, tmp_path: Path, mock_driver):
        cfg = CrucibleConfig(include_incident_files=False)
        kb = create_knowledge_base(
            "structured",
            tmp_path / "kb",
            config=cfg,
            renderer=_renderer,
            driver=mock_driver,
        )
        assert isinstance(kb, StructuredKnowledgeBase)
        assert kb._config.include_incident_files is False

    def test_include_benchmark_results_forwarded(self, tmp_path: Path, mock_driver):
        cfg = CrucibleConfig(include_benchmark_results=True)
        kb = create_knowledge_base(
            "structured",
            tmp_path / "kb",
            config=cfg,
            renderer=_renderer,
            driver=mock_driver,
        )
        assert isinstance(kb, StructuredKnowledgeBase)
        assert kb._config.include_benchmark_results is True

    def test_include_benchmark_results_forwarded_append_only(self, tmp_path: Path, mock_driver):
        cfg = CrucibleConfig(include_benchmark_results=True)
        kb = create_knowledge_base(
            "append-only",
            tmp_path / "kb",
            config=cfg,
            renderer=_renderer,
            driver=mock_driver,
        )
        assert isinstance(kb, AppendOnlyKnowledgeBase)
        assert kb._config.include_benchmark_results is True


class TestIncludeBenchmarkResults:
    """Tests for the include_benchmark_results toggle."""

    @patch.object(StructuredKnowledgeBase, "_call_llm", new_callable=AsyncMock)
    async def test_structured_preserves_benchmark_when_enabled(self, mock_llm, tmp_path: Path, mock_driver):
        shared = tmp_path / "shared.md"
        shared.write_text("data <benchmark_result>ground truth</benchmark_result> more data")
        kb = StructuredKnowledgeBase(
            tmp_path / "kb",
            app_name="test-app",
            config=CrucibleConfig(include_benchmark_results=True),
            renderer=_renderer,
            driver=mock_driver,
        )
        mock_llm.side_effect = ["session summary", "merged summary", "lessons"]
        await kb.update(SessionFiles(diagnosis=shared))

        # First LLM call is _summarize_session — content should include benchmark block
        summarize_prompt = mock_llm.call_args_list[0][0][0]
        assert "<benchmark_result>" in summarize_prompt
        assert "ground truth" in summarize_prompt

    @patch.object(StructuredKnowledgeBase, "_call_llm", new_callable=AsyncMock)
    async def test_structured_strips_benchmark_when_disabled(self, mock_llm, tmp_path: Path, mock_driver):
        shared = tmp_path / "shared.md"
        shared.write_text("data <benchmark_result>ground truth</benchmark_result> more data")
        kb = StructuredKnowledgeBase(
            tmp_path / "kb",
            app_name="test-app",
            config=CrucibleConfig(include_benchmark_results=False),
            renderer=_renderer,
            driver=mock_driver,
        )
        mock_llm.side_effect = ["session summary", "merged summary", "lessons"]
        await kb.update(SessionFiles(diagnosis=shared))

        summarize_prompt = mock_llm.call_args_list[0][0][0]
        assert "<benchmark_result>" not in summarize_prompt
        assert "ground truth" not in summarize_prompt

    @patch.object(AppendOnlyKnowledgeBase, "_call_llm", new_callable=AsyncMock)
    async def test_append_only_preserves_benchmark_when_enabled(self, mock_llm, tmp_path: Path, mock_driver):
        shared = tmp_path / "shared.md"
        shared.write_text("data <benchmark_result>ground truth</benchmark_result> more data")
        kb = AppendOnlyKnowledgeBase(
            tmp_path / "kb",
            config=CrucibleConfig(include_benchmark_results=True),
            renderer=_renderer,
            driver=mock_driver,
        )
        mock_llm.return_value = "session summary"
        await kb.update(SessionFiles(diagnosis=shared))

        summarize_prompt = mock_llm.call_args_list[0][0][0]
        assert "<benchmark_result>" in summarize_prompt
        assert "ground truth" in summarize_prompt

    @patch.object(AppendOnlyKnowledgeBase, "_call_llm", new_callable=AsyncMock)
    async def test_append_only_strips_benchmark_when_disabled(self, mock_llm, tmp_path: Path, mock_driver):
        shared = tmp_path / "shared.md"
        shared.write_text("data <benchmark_result>ground truth</benchmark_result> more data")
        kb = AppendOnlyKnowledgeBase(
            tmp_path / "kb",
            config=CrucibleConfig(include_benchmark_results=False),
            renderer=_renderer,
            driver=mock_driver,
        )
        mock_llm.return_value = "session summary"
        await kb.update(SessionFiles(diagnosis=shared))

        summarize_prompt = mock_llm.call_args_list[0][0][0]
        assert "<benchmark_result>" not in summarize_prompt

    async def test_structured_empty_after_strip_still_skips(self, tmp_path: Path, mock_driver):
        """When include_benchmark_results=True but content is only whitespace, still skip."""
        shared = tmp_path / "shared.md"
        shared.write_text("   ")
        kb = StructuredKnowledgeBase(
            tmp_path / "kb",
            app_name="test-app",
            config=CrucibleConfig(include_benchmark_results=True),
            renderer=_renderer,
            driver=mock_driver,
        )
        await kb.update(SessionFiles(diagnosis=shared))
        assert not kb.summary_path.exists()


class TestCitationValidation:
    def testextract_citations(self):
        text = "root cause (1 incidents, {{ref:incidents/20260324_010224.md}}) and {{ref:incidents/20260324_010545.md}}"
        assert extract_citations(text) == [
            "incidents/20260324_010224.md",
            "incidents/20260324_010545.md",
        ]

    def testextract_citations_none(self):
        assert extract_citations("no citations here") == []

    def testfind_invalid_citations_all_valid(self, tmp_path: Path):
        incidents_dir = tmp_path / "incidents"
        incidents_dir.mkdir()
        (incidents_dir / "20260324_010224.md").write_text("content")
        text = "some text {{ref:incidents/20260324_010224.md}} more"
        assert find_invalid_citations(text, incidents_dir) == []

    def testfind_invalid_citations_some_invalid(self, tmp_path: Path):
        incidents_dir = tmp_path / "incidents"
        incidents_dir.mkdir()
        (incidents_dir / "20260324_010224.md").write_text("content")
        text = "{{ref:incidents/20260324_010224.md}} and {{ref:incidents/fake_file.md}}"
        assert find_invalid_citations(text, incidents_dir) == ["incidents/fake_file.md"]

    def teststrip_citation_wrappers(self):
        text = "root cause (1 incidents, {{ref:incidents/20260324_010224.md}})"
        assert strip_citation_wrappers(text) == "root cause (1 incidents, incidents/20260324_010224.md)"

    def teststrip_citation_wrappers_multiple(self):
        text = "{{ref:incidents/a.md}} and {{ref:incidents/b.md}}"
        assert strip_citation_wrappers(text) == "incidents/a.md and incidents/b.md"

    @patch.object(StructuredKnowledgeBase, "_call_llm", new_callable=AsyncMock)
    async def test_merge_correction_loop_fixes_bad_citation(self, mock_call_llm, tmp_path: Path, mock_driver):
        """When LLM produces invalid citation, correction loop fixes it."""
        envelope = '\n<merge_result>{"primary_action": "noop", "primary_class_name": null}</merge_result>'
        bad_output = "root cause (1 incidents, {{ref:incidents/fake_20240730.md}})" + envelope
        good_output = "root cause (1 incidents, {{ref:incidents/20260324_010224.md}})" + envelope

        mock_driver = _make_mock_driver()
        mock_driver.run = AsyncMock(
            side_effect=[
                _AgentResult(output=bad_output, completed=True, messages=[{"bad": True}]),
                _AgentResult(output=good_output, completed=True, messages=[{"good": True}]),
            ]
        )
        kb = StructuredKnowledgeBase(tmp_path / "kb", app_name="test-app", renderer=_renderer, driver=mock_driver)

        kb.incidents_dir.mkdir(parents=True, exist_ok=True)
        (kb.incidents_dir / "20260324_010224.md").write_text("incident")

        result = await kb._merge_into_long_term_summary(
            "session summary", "", incident_ref="incidents/20260324_010224.md"
        )

        assert result.new_summary_text == "root cause (1 incidents, incidents/20260324_010224.md)"
        assert mock_driver.run.call_count == 2

    @patch.object(StructuredKnowledgeBase, "_call_llm", new_callable=AsyncMock)
    async def test_merge_no_correction_when_citations_valid(self, mock_call_llm, tmp_path: Path, mock_driver):
        """When LLM produces valid citations, no correction loop runs."""
        envelope = '\n<merge_result>{"primary_action": "noop", "primary_class_name": null}</merge_result>'
        good_output = "root cause (1 incidents, {{ref:incidents/20260324_010224.md}})" + envelope

        mock_driver = _make_mock_driver()
        mock_driver.run = AsyncMock(return_value=_AgentResult(output=good_output, completed=True, messages=[]))
        kb = StructuredKnowledgeBase(tmp_path / "kb", app_name="test-app", renderer=_renderer, driver=mock_driver)

        kb.incidents_dir.mkdir(parents=True, exist_ok=True)
        (kb.incidents_dir / "20260324_010224.md").write_text("incident")

        result = await kb._merge_into_long_term_summary(
            "session summary", "", incident_ref="incidents/20260324_010224.md"
        )

        assert result.new_summary_text == "root cause (1 incidents, incidents/20260324_010224.md)"
        assert mock_driver.run.call_count == 1


class TestReflector:
    def _make_reflector(self, tmp_path: Path, mock_driver) -> Reflector:
        return Reflector(tmp_path / "kb", renderer=_renderer, driver=mock_driver)

    async def test_run_skips_when_no_stage_outputs(self, tmp_path: Path, caplog, mock_driver):
        reflector = self._make_reflector(tmp_path, mock_driver)
        with caplog.at_level(logging.INFO, logger="benchmarks.sregym.agents.crucible.knowledge_base"):
            await reflector.run(stage_outputs_file=tmp_path / "nonexistent.md")
        assert "No stage output content" in caplog.text

    @patch.object(Reflector, "reflect", new_callable=AsyncMock)
    @patch.object(Reflector, "apply", new_callable=AsyncMock)
    async def test_run_updates_triage_priors(self, mock_apply, mock_reflect, tmp_path: Path, mock_driver):
        classification = FailureClassification(
            outcome="failure",
            stage_failures=[
                StageFailure(
                    stage="triage",
                    description="Missed network policies",
                    evidence="No network policy check in triage output",
                    lesson="Always check network policies during triage",
                ),
            ],
            summary="The agent missed checking network policies.",
        )
        mock_reflect.return_value = classification

        reflector = self._make_reflector(tmp_path, mock_driver)
        stage_outputs = tmp_path / "stage_outputs.md"
        stage_outputs.write_text("stage output content")
        await reflector.run(stage_outputs_file=stage_outputs)

        mock_reflect.assert_called_once()
        mock_apply.assert_called_once()
        call_args = mock_apply.call_args
        assert call_args[0][0] is classification
        assert call_args[0][1] == "stage output content"

    @patch.object(Reflector, "reflect", new_callable=AsyncMock)
    async def test_run_handles_reflect_exception(self, mock_reflect, tmp_path: Path, caplog, mock_driver):
        mock_reflect.side_effect = RuntimeError("LLM error")

        reflector = self._make_reflector(tmp_path, mock_driver)
        stage_outputs = tmp_path / "stage_outputs.md"
        stage_outputs.write_text("stage output content")
        with caplog.at_level(logging.ERROR, logger="benchmarks.sregym.agents.crucible.knowledge_base"):
            await reflector.run(stage_outputs_file=stage_outputs)

        assert "Failed to classify failure" in caplog.text

    @patch.object(Reflector, "reflect", new_callable=AsyncMock)
    @patch(
        "benchmarks.sregym.agents.crucible.knowledge_base.reflection.TriagePriorConfig.apply",
        new_callable=AsyncMock,
    )
    async def test_run_logs_individual_apply_errors(
        self, mock_cfg_apply, mock_reflect, tmp_path: Path, caplog, mock_driver
    ):
        classification = FailureClassification(
            outcome="failure",
            stage_failures=[
                StageFailure(
                    stage="triage",
                    description="Bad triage",
                    evidence="Missed pods",
                    lesson="Check pods",
                ),
            ],
            summary="Triage failure.",
        )
        mock_reflect.return_value = classification
        mock_cfg_apply.side_effect = RuntimeError("write failed")

        reflector = self._make_reflector(tmp_path, mock_driver)
        stage_outputs = tmp_path / "stage_outputs.md"
        stage_outputs.write_text("stage output content")
        with caplog.at_level(logging.ERROR, logger="benchmarks.sregym.agents.crucible.knowledge_base"):
            await reflector.run(stage_outputs_file=stage_outputs)

        assert "Reflection apply error" in caplog.text
        mock_cfg_apply.assert_called_once()

    @patch.object(Reflector, "reflect", new_callable=AsyncMock)
    @patch.object(Reflector, "apply", new_callable=AsyncMock)
    async def test_run_success_no_failures_skips_apply(
        self, mock_apply, mock_reflect, tmp_path: Path, caplog, mock_driver
    ):
        classification = FailureClassification(
            outcome="success",
            stage_failures=[],
            summary="The agent handled everything well.",
        )
        mock_reflect.return_value = classification

        reflector = self._make_reflector(tmp_path, mock_driver)
        stage_outputs = tmp_path / "stage_outputs.md"
        stage_outputs.write_text("stage output content")
        with caplog.at_level(logging.INFO, logger="benchmarks.sregym.agents.crucible.knowledge_base"):
            await reflector.run(stage_outputs_file=stage_outputs)

        mock_apply.assert_called_once()


class TestFailureClassification:
    def test_failure_classification_roundtrip(self):
        fc = FailureClassification(
            outcome="failure",
            stage_failures=[
                StageFailure(
                    stage="triage",
                    description="Missed pods",
                    evidence="No pod check in output",
                    lesson="Always check pods",
                ),
                StageFailure(
                    stage="verification",
                    description="Wrong confirmation",
                    evidence="Confirmed bad hypothesis",
                    lesson="Cross-check evidence",
                ),
            ],
            summary="Multiple stage failures.",
        )
        json_str = fc.model_dump_json()
        restored = FailureClassification.model_validate_json(json_str)
        assert restored == fc
        assert len(restored.stage_failures) == 2
        assert restored.stage_failures[0].stage == "triage"
        assert restored.stage_failures[1].stage == "verification"

    async def test_apply_skips_on_success(self, tmp_path: Path, caplog, mock_driver):
        reflector = Reflector(tmp_path / "kb", renderer=_renderer, driver=mock_driver)
        classification = FailureClassification(
            outcome="success",
            stage_failures=[],
            summary="All good.",
        )
        with caplog.at_level(logging.INFO, logger="benchmarks.sregym.agents.crucible.knowledge_base"):
            await reflector.apply(classification, "some stage outputs")
        assert "Unambiguous success; skipping prior updates" in caplog.text

    @patch(
        "benchmarks.sregym.agents.crucible.knowledge_base.reflection.TriagePriorConfig.apply",
        new_callable=AsyncMock,
    )
    @patch(
        "benchmarks.sregym.agents.crucible.knowledge_base.reflection.MarkdownPriorConfig.apply",
        new_callable=AsyncMock,
    )
    async def test_apply_only_updates_failed_stages(
        self,
        mock_md_apply,
        mock_triage_apply,
        tmp_path: Path,
        mock_driver,
    ):
        reflector = Reflector(tmp_path / "kb", renderer=_renderer, driver=mock_driver)
        classification = FailureClassification(
            outcome="failure",
            stage_failures=[
                StageFailure(
                    stage="verification",
                    description="Wrong confirmation",
                    evidence="Confirmed bad hypothesis",
                    lesson="Cross-check evidence",
                ),
            ],
            summary="Verification failure only.",
        )
        await reflector.apply(classification, "stage outputs text")

        # Only verification prior (MarkdownPriorConfig) should be called, not triage
        mock_md_apply.assert_called_once()
        mock_triage_apply.assert_not_called()


class TestPriorFilesAndStageMappings:
    def test_verification_prior_in_prior_files(self):
        assert "verification" in PRIOR_FILES
        cfg = PRIOR_FILES["verification"]
        assert cfg.filename == SCHEMA_V2.verification_priors

    def test_stage_to_prior_mapping(self):
        assert STAGE_TO_PRIOR["verification"] == "verification"
        assert STAGE_TO_PRIOR["triage"] == "triage"


class TestMigrateTriage:
    def test_migrate_triage_priors(self, tmp_path: Path):
        import yaml

        from benchmarks.sregym.agents.crucible.knowledge_base.schema import migrate_triage_priors

        md_content = (
            "## Network Connectivity\n"
            "- Check DNS resolution\n"
            "- Verify service endpoints\n"
            "\n"
            "## Pod Health\n"
            "- Check restart counts\n"
            "- Look for OOMKilled events\n"
        )
        md_path = tmp_path / "triage_priors.md"
        md_path.write_text(md_content)

        migrate_triage_priors(tmp_path)

        yaml_path = tmp_path / "triage_priors.yaml"
        assert yaml_path.exists()
        assert not md_path.exists()

        data = yaml.safe_load(yaml_path.read_text())
        assert len(data["areas"]) == 2
        assert data["areas"][0]["name"] == "Network Connectivity"
        assert data["areas"][0]["hints"] == ["Check DNS resolution", "Verify service endpoints"]
        assert data["areas"][1]["name"] == "Pod Health"
        assert data["areas"][1]["hints"] == ["Check restart counts", "Look for OOMKilled events"]

    def test_load_triage_priors(self, tmp_path: Path):
        import yaml

        from benchmarks.sregym.agents.crucible.tools._kb_tools import TriagePriors, load_triage_priors

        data = {
            "areas": [
                {"name": "DNS", "hints": ["Check CoreDNS pods", "Verify resolv.conf"]},
                {"name": "Storage", "hints": ["Check PV/PVC bindings"]},
            ]
        }
        yaml_path = tmp_path / "triage_priors.yaml"
        yaml_path.write_text(yaml.dump(data, default_flow_style=False))

        result = load_triage_priors(yaml_path)
        assert isinstance(result, TriagePriors)
        assert len(result.areas) == 2
        assert result.areas[0].name == "DNS"
        assert result.areas[0].hints == ["Check CoreDNS pods", "Verify resolv.conf"]


class TestPromptContracts:
    _v3_renderer = PromptRenderer("v3")

    def test_classify_failure_prompt_references_stages(self):
        rendered = self._v3_renderer.render(
            "kb/classify_failure",
            stage_outputs="(test outputs)",
        )
        for stage in ("triage", "retrieval", "verification"):
            assert stage in rendered, f"classify_failure.j2 missing stage: {stage}"

    def test_refine_triage_priors_references_yaml(self):
        rendered = self._v3_renderer.render(
            "kb/refine_triage_priors",
            prior_priors="(test priors)",
            failure_classification="(test classification)",
            stage_outputs="(test outputs)",
            grounded_recovery_reflection="(grounded reflection)",
        )
        assert "areas" in rendered
        assert "hints" in rendered
        assert "grounded reflection" in rendered.lower()

    def test_refine_verification_priors_references_sections(self):
        rendered = self._v3_renderer.render(
            "kb/refine_verification_priors",
            prior_guidance="(test guidance)",
            failure_classification="(test classification)",
            stage_outputs="(test outputs)",
            grounded_recovery_reflection="(grounded reflection)",
        )
        assert "##" in rendered
        assert "grounded reflection" in rendered.lower()


class TestGroundedRecoveryReflection:
    def test_round_trips_json(self):
        reflection = RecoveryReflection(
            summary="Grounded summary",
            investigation_observations=["Observed persistent upstream failures"],
            stage_failures=[
                RecoveryStageFailure(
                    stage="verification",
                    description="Confirmed a local symptom instead of the upstream cause",
                    evidence="Recovery traced the failing dependency chain in cluster state",
                    lesson="Verify upstream dependencies before accepting a candidate",
                )
            ],
        )

        restored = RecoveryReflection.model_validate_json(reflection.model_dump_json())
        assert restored == reflection


# ---------- Unified KB (per_app=False) ----------


_unified_config = CrucibleConfig(per_app=False)


@pytest.fixture
def tmp_unified_kb(tmp_path: Path, mock_driver) -> tuple[StructuredKnowledgeBase, Path, Path]:
    """Return (kb, kb_dir, target_dir) with per_app=False."""
    kb_dir = tmp_path / "kb"
    target_dir = tmp_path / "target"
    target_dir.mkdir()
    kb = StructuredKnowledgeBase(
        kb_dir,
        app_name="test-app",
        config=_unified_config,
        renderer=_renderer,
        driver=mock_driver,
    )
    return kb, kb_dir, target_dir


class TestUnifiedKBPaths:
    def test_summary_path_at_root(self, tmp_unified_kb):
        kb, kb_dir, _ = tmp_unified_kb
        assert kb.summary_path == kb_dir / KB_SUMMARY_FILENAME

    def test_incidents_dir_categorized_by_app(self, tmp_unified_kb):
        kb, kb_dir, _ = tmp_unified_kb
        assert kb.incidents_dir == kb_dir / KB_INCIDENTS_DIRNAME / "test-app"

    def test_architecture_stays_per_app(self, tmp_unified_kb):
        kb, kb_dir, _ = tmp_unified_kb
        assert kb.architecture_path == kb_dir / "test-app" / KB_ARCHITECTURE_FILENAME

    def test_lessons_path_unchanged(self, tmp_unified_kb):
        kb, kb_dir, _ = tmp_unified_kb
        assert kb.lessons_path == kb_dir / KB_LESSONS_FILENAME


class TestUnifiedKBSaveIncident:
    def test_saves_to_app_subdir(self, tmp_unified_kb):
        kb, kb_dir, _ = tmp_unified_kb
        incident_id = kb._save_incident("summary", "content")
        assert (kb_dir / KB_INCIDENTS_DIRNAME / "test-app" / f"{incident_id}.md").exists()


class TestUnifiedKBInject:
    async def test_copies_root_summary(self, tmp_unified_kb):
        kb, kb_dir, target_dir = tmp_unified_kb
        kb.summary_path.write_text("unified summary")

        result = await kb.inject(target_dir)

        assert result.summary is not None
        assert result.summary.read_text() == "unified summary"

    async def test_copies_all_app_incidents(self, tmp_unified_kb):
        kb, kb_dir, target_dir = tmp_unified_kb
        # Create incidents for two apps
        for app in ("app-a", "app-b"):
            app_incidents = kb_dir / KB_INCIDENTS_DIRNAME / app
            app_incidents.mkdir(parents=True)
            (app_incidents / "20260101_120000.md").write_text(f"incident from {app}")

        result = await kb.inject(target_dir)

        assert result.incidents_dir is not None
        assert (target_dir / KB_INCIDENTS_DIRNAME / "app-a" / "20260101_120000.md").exists()
        assert (target_dir / KB_INCIDENTS_DIRNAME / "app-b" / "20260101_120000.md").exists()

    async def test_no_incidents(self, tmp_unified_kb):
        kb, _, target_dir = tmp_unified_kb
        result = await kb.inject(target_dir)
        assert result.incidents_dir is None


class TestUnifiedKBUpdate:
    @patch.object(StructuredKnowledgeBase, "_merge_into_long_term_summary", new_callable=AsyncMock)
    @patch.object(StructuredKnowledgeBase, "_call_llm", new_callable=AsyncMock)
    async def test_citation_ref_includes_app(self, mock_llm, mock_merge, tmp_path: Path, mock_driver):
        shared = tmp_path / "shared.md"
        shared.write_text("session data")
        kb = StructuredKnowledgeBase(
            tmp_path / "kb",
            app_name="test-app",
            config=_unified_config,
            renderer=_renderer,
            driver=mock_driver,
        )
        mock_llm.side_effect = ["session summary", "distilled lessons"]
        mock_merge.return_value = _merge_result("merged")

        await kb.update(SessionFiles(diagnosis=shared))

        _, kwargs = mock_merge.call_args
        assert "test-app" in kwargs["incident_ref"]
        assert kwargs["incident_ref"].startswith("incidents/test-app/")


class TestUnifiedKBDistillLessons:
    @patch.object(StructuredKnowledgeBase, "_call_llm", new_callable=AsyncMock)
    async def test_reads_root_summary(self, mock_llm, tmp_path: Path, mock_driver):
        kb_dir = tmp_path / "kb"
        kb = StructuredKnowledgeBase(
            kb_dir, app_name="test-app", config=_unified_config, renderer=_renderer, driver=mock_driver
        )
        kb.summary_path.write_text("unified summary content")
        mock_llm.return_value = "lessons from unified"

        await kb._distill_lessons()

        assert mock_llm.call_count == 1
        prompt_arg = mock_llm.call_args[0][0]
        assert "unified summary content" in prompt_arg

    @patch.object(StructuredKnowledgeBase, "_call_llm", new_callable=AsyncMock)
    async def test_skips_when_no_summary(self, mock_llm, tmp_path: Path, mock_driver):
        kb = StructuredKnowledgeBase(
            tmp_path / "kb",
            app_name="test-app",
            config=_unified_config,
            renderer=_renderer,
            driver=mock_driver,
        )
        await kb._distill_lessons()
        mock_llm.assert_not_called()


class TestFindInvalidCitationsUnified:
    def test_valid_citation(self, tmp_path: Path):
        incidents = tmp_path / "incidents" / "myapp"
        incidents.mkdir(parents=True)
        (incidents / "20260324_010224.md").write_text("content")
        text = "{{ref:incidents/myapp/20260324_010224.md}}"
        assert find_invalid_citations_unified(text, tmp_path) == []

    def test_invalid_citation(self, tmp_path: Path):
        text = "{{ref:incidents/myapp/fake.md}}"
        assert find_invalid_citations_unified(text, tmp_path) == ["incidents/myapp/fake.md"]


class TestPerAppConfigFlag:
    def test_default_is_true(self):
        assert CrucibleConfig().per_app is True

    def test_per_app_false(self):
        cfg = CrucibleConfig(per_app=False)
        assert cfg.per_app is False

    def test_config_from_experiment_reads_per_app(self):
        from benchmarks.sregym.agents.crucible.config import crucible_config_from_experiment_agent

        cfg = crucible_config_from_experiment_agent({"per_app": False, "prompt_version": "v1"})
        assert cfg.per_app is False

    def test_config_from_experiment_defaults_true(self):
        from benchmarks.sregym.agents.crucible.config import crucible_config_from_experiment_agent

        cfg = crucible_config_from_experiment_agent({"prompt_version": "v1"})
        assert cfg.per_app is True

    def test_config_from_kb_task_reads_per_app(self):
        from benchmarks.sregym.agents.crucible.config import crucible_config_from_kb_task

        cfg = crucible_config_from_kb_task({"per_app": False, "prompt_version": "v1"})
        assert cfg.per_app is False
