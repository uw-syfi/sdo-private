"""Tests for benchmarks.sregym.participants.crucible.knowledge_base.playbook."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from benchmarks.sregym.participants.crucible.knowledge_base.playbook import (
    Playbook,
    PlaybookStore,
    PlaybookValidationError,
    slugify,
    validate_playbook,
)


def _valid_playbook_markdown(
    slug: str = "missing_env_variable",
    class_name: str = "Missing environment variable",
) -> str:
    """Return a minimal fully-valid playbook markdown string."""
    return (
        f"# Playbook: {class_name}\n"
        "\n"
        "<!-- meta -->\n"
        f"slug: {slug}\n"
        f"class_name: {class_name}\n"
        "seen: 3\n"
        "created_from: success\n"
        "last_updated: 2026-04-09T12:00:00Z\n"
        "<!-- /meta -->\n"
        "\n"
        "## Summary\n"
        "Service crashes on startup because a required env var is unset.\n"
        "\n"
        "## Symptoms\n"
        "- Pod restarts with CrashLoopBackOff within 10 seconds of start.\n"
        "\n"
        "## Triage Procedure\n"
        "1. **Action:** kubectl get pods\n"
        "   **Expected shape:** running pods\n"
        "   **If absent:** check namespace\n"
        "\n"
        "## Verification Procedure\n"
        "1. **Action:** kubectl logs deploy/api\n"
        "   **Expected shape:** startup log line printed\n"
        "   **If absent:** rerun kubectl describe\n"
        "\n"
        "## Required Evidence\n"
        "- [ ] Confirmed the env var was missing from deployment spec.\n"
        "\n"
        "## Known Distractors\n"
        "- Network policy errors in unrelated pods.\n"
        "\n"
        "## Failure Patterns\n"
        "- Pod exit code 1 with stderr referencing unset variable.\n"
    )


class TestSlugify:
    def test_lowercase_and_non_alnum_collapses(self):
        assert slugify("Hello World!") == "hello_world"

    def test_multiple_separators_collapse_to_single(self):
        assert slugify("Foo   ---  Bar") == "foo_bar"

    def test_idempotent(self):
        for raw in [
            "Missing Env Variable",
            "Hello World!",
            "HTTP 500: Internal Server Error",
            "simple_slug",
        ]:
            once = slugify(raw)
            twice = slugify(once)
            assert once == twice

    def test_truncates_at_64_chars(self):
        raw = "a" * 200
        result = slugify(raw)
        assert len(result) == 64
        assert result == "a" * 64

    def test_strips_leading_and_trailing_underscores(self):
        assert slugify("!!!hello!!!") == "hello"
        assert slugify("___weird___") == "weird"

    def test_empty_string(self):
        assert slugify("") == ""

    def test_only_non_alnum(self):
        assert slugify("!!!!") == ""


class TestValidatePlaybook:
    def test_valid_minimal_playbook_returns_empty(self):
        assert validate_playbook(_valid_playbook_markdown()) == []

    def test_missing_meta_block_fails(self):
        text = _valid_playbook_markdown()
        text = text.replace("<!-- meta -->", "").replace("<!-- /meta -->", "")
        violations = validate_playbook(text)
        assert any("meta" in v and "missing" in v for v in violations)

    def test_missing_required_meta_key(self):
        text = _valid_playbook_markdown()
        text = text.replace("class_name: Missing environment variable\n", "")
        violations = validate_playbook(text)
        assert any("class_name" in v for v in violations)

    def test_seen_not_integer(self):
        text = _valid_playbook_markdown()
        text = text.replace("seen: 3", "seen: not_a_number")
        violations = validate_playbook(text)
        assert any("seen" in v and "integer" in v for v in violations)

    def test_created_from_invalid_value(self):
        text = _valid_playbook_markdown()
        text = text.replace("created_from: success", "created_from: magic")
        violations = validate_playbook(text)
        assert any("created_from" in v for v in violations)

    def test_missing_required_section(self):
        text = _valid_playbook_markdown()
        # Drop the entire Symptoms section header and body.
        text = text.replace(
            "## Symptoms\n- Pod restarts with CrashLoopBackOff within 10 seconds of start.\n\n",
            "",
        )
        violations = validate_playbook(text)
        assert any("Symptoms" in v for v in violations)

    def test_empty_symptoms_section(self):
        text = _valid_playbook_markdown()
        text = text.replace(
            "## Symptoms\n- Pod restarts with CrashLoopBackOff within 10 seconds of start.\n",
            "## Symptoms\n",
        )
        violations = validate_playbook(text)
        assert any("Symptoms" in v and "bullet" in v for v in violations)

    def test_missing_checkbox_in_required_evidence(self):
        text = _valid_playbook_markdown()
        text = text.replace(
            "- [ ] Confirmed the env var was missing from deployment spec.",
            "- Confirmed the env var was missing from deployment spec.",
        )
        violations = validate_playbook(text)
        assert any("Required Evidence" in v and "[ ]" in v for v in violations)

    @pytest.mark.parametrize(
        "phrase",
        [
            "appropriate values",
            "as needed",
            "relevant configuration",
            "if applicable",
            "as appropriate",
        ],
    )
    def test_forbidden_vague_phrase(self, phrase: str):
        text = _valid_playbook_markdown()
        text = text.replace(
            "Service crashes on startup because a required env var is unset.",
            f"Service crashes on startup, set {phrase} to fix.",
        )
        violations = validate_playbook(text)
        assert any(phrase in v for v in violations)


class TestPlaybookParseRoundtrip:
    def test_parse_populates_fields(self):
        text = _valid_playbook_markdown()
        pb = Playbook.parse(text)

        assert pb.slug == "missing_env_variable"
        assert pb.class_name == "Missing environment variable"
        assert pb.seen == 3
        assert pb.created_from == "success"
        assert pb.last_updated == "2026-04-09T12:00:00Z"
        assert "required env var" in pb.summary
        assert len(pb.symptoms) == 1
        assert len(pb.triage_procedure) == 1
        assert "kubectl get pods" in pb.triage_procedure[0]
        assert len(pb.verification_procedure) == 1
        assert len(pb.required_evidence) == 1
        assert len(pb.known_distractors) == 1
        assert len(pb.failure_patterns) == 1

    def test_roundtrip_to_markdown_reparses(self):
        original = Playbook.parse(_valid_playbook_markdown())
        reparsed = Playbook.parse(original.to_markdown())

        assert reparsed.slug == original.slug
        assert reparsed.class_name == original.class_name
        assert reparsed.seen == original.seen
        assert reparsed.created_from == original.created_from
        assert reparsed.summary == original.summary
        assert reparsed.symptoms == original.symptoms
        assert reparsed.triage_procedure == original.triage_procedure
        assert reparsed.verification_procedure == original.verification_procedure
        assert reparsed.required_evidence == original.required_evidence
        assert reparsed.known_distractors == original.known_distractors
        assert reparsed.failure_patterns == original.failure_patterns

    def test_required_evidence_submission_gate_qualifier_stripped(self):
        text = _valid_playbook_markdown().replace(
            "## Required Evidence\n",
            "## Required Evidence (Submission Gate)\n",
        )
        pb = Playbook.parse(text)
        assert len(pb.required_evidence) == 1
        assert "env var" in pb.required_evidence[0]

    def test_parse_raises_validation_error_on_invalid(self):
        text = _valid_playbook_markdown().replace("seen: 3", "seen: not_a_number")
        with pytest.raises(PlaybookValidationError) as excinfo:
            Playbook.parse(text)
        assert excinfo.value.violations
        assert any("seen" in v for v in excinfo.value.violations)


class TestPlaybookStore:
    def _store(self, tmp_path: Path) -> PlaybookStore:
        return PlaybookStore(tmp_path / "playbooks")

    def test_save_creates_dir_and_file(self, tmp_path: Path):
        store = self._store(tmp_path)
        pb = Playbook.parse(_valid_playbook_markdown())

        path = store.save(pb)

        assert path.exists()
        assert path.name == "missing_env_variable.md"
        assert store.playbooks_dir.is_dir()

    def test_load_missing_returns_none(self, tmp_path: Path):
        store = self._store(tmp_path)
        assert store.load("does_not_exist") is None

    def test_load_returns_saved_playbook(self, tmp_path: Path):
        store = self._store(tmp_path)
        pb = Playbook.parse(_valid_playbook_markdown())
        store.save(pb)

        loaded = store.load("missing_env_variable")
        assert loaded is not None
        assert loaded.slug == pb.slug
        assert loaded.class_name == pb.class_name
        assert loaded.summary == pb.summary

    def test_save_twice_with_changes_records_diff(self, tmp_path: Path):
        store = self._store(tmp_path)
        pb1 = Playbook.parse(_valid_playbook_markdown())
        store.save(pb1)

        # Modify and save again.
        pb2 = pb1.model_copy(
            update={
                "summary": "Updated summary: env var missing causes startup crash.",
                "markdown": "",  # force reserialization
            }
        )
        store.save(pb2)

        history_dir = store.playbooks_dir / ".history" / "missing_env_variable"
        assert history_dir.is_dir()
        diffs = list(history_dir.glob("*.diff"))
        assert len(diffs) == 1
        assert diffs[0].read_text().strip() != ""

    def test_add_alias_load_resolves(self, tmp_path: Path):
        store = self._store(tmp_path)
        pb = Playbook.parse(_valid_playbook_markdown(slug="canonical", class_name="Canonical"))
        store.save(pb)

        store.add_alias("old_name", "canonical")

        loaded = store.load("old_name")
        assert loaded is not None
        assert loaded.slug == "canonical"

    def test_add_alias_collapses_transitively(self, tmp_path: Path):
        store = self._store(tmp_path)
        store.add_alias("a", "b")
        store.add_alias("b", "c")

        aliases = store._load_aliases()
        assert aliases["a"] == "c"
        assert aliases["b"] == "c"

    def test_add_alias_cycle_raises(self, tmp_path: Path):
        store = self._store(tmp_path)
        store.add_alias("a", "b")
        with pytest.raises(RuntimeError, match="cycle"):
            store.add_alias("b", "a")

    def test_archive_consolidation_moves_file_to_history(self, tmp_path: Path):
        store = self._store(tmp_path)
        loser = Playbook.parse(_valid_playbook_markdown(slug="loser", class_name="Loser"))
        winner = Playbook.parse(_valid_playbook_markdown(slug="winner", class_name="Winner"))
        store.save(loser)
        store.save(winner)

        dest = store.archive_consolidation("loser", "winner")

        assert dest is not None
        assert dest.exists()
        assert dest.name.startswith("consolidated_into_winner_")
        assert dest.suffix == ".md"
        assert not (store.playbooks_dir / "loser.md").exists()
        assert (store.playbooks_dir / "winner.md").exists()

    def test_list_slugs_sorted_excludes_history_and_aliases(self, tmp_path: Path):
        store = self._store(tmp_path)
        for slug, cls in [
            ("charlie", "Charlie"),
            ("alpha", "Alpha"),
            ("bravo", "Bravo"),
        ]:
            store.save(Playbook.parse(_valid_playbook_markdown(slug=slug, class_name=cls)))
        store.add_alias("old", "alpha")  # creates .aliases.yaml

        slugs = store.list_slugs()

        assert slugs == ["alpha", "bravo", "charlie"]
        assert ".history" not in slugs
        assert ".aliases" not in slugs
