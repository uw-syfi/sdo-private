"""Tests for benchmarks.sregym.participants.crucible.knowledge_base.mitigation_playbook."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from benchmarks.sregym.participants.crucible.knowledge_base.mitigation_playbook import (
    MitigationPlaybook,
    MitigationPlaybookStore,
    MitigationPlaybookValidationError,
    validate_mitigation_playbook,
)


def _valid_mitigation_playbook_markdown(
    slug: str = "missing_env_variable",
    class_name: str = "Missing environment variable",
) -> str:
    """Return a minimal fully-valid mitigation playbook markdown string."""
    return (
        f"# Mitigation Playbook: {class_name}\n"
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
        "Restart the failing Deployment after adding the missing env var.\n"
        "\n"
        "## Applicability\n"
        "- A required environment variable is absent from the failing Deployment's container env spec.\n"
        "\n"
        "## Mitigation Procedure\n"
        "1. **Action:** kubectl set env deploy/<DEPLOYMENT> -n <NAMESPACE> <ENV_VAR>=<VALUE>\n"
        "   **Expected outcome:** rollout starts on the patched Deployment.\n"
        "\n"
        "## Post-Mitigation Verification\n"
        "1. **Action:** kubectl get pods -n <NAMESPACE> -l app=<APP>\n"
        "   **Expected outcome:** all pods running with no restarts.\n"
        "\n"
        "## Required Evidence\n"
        "- [ ] All target Deployment pods are Running with restartCount=0 for at least 30s.\n"
        "\n"
        "## Known Pitfalls\n"
        "- Setting the env var on the Pod template alone bypasses the Deployment controller.\n"
        "\n"
        "## Failure Patterns\n"
        "- Patching the wrong namespace and reporting success.\n"
    )


class TestValidateMitigationPlaybook:
    def test_valid_minimal_playbook_returns_empty(self):
        assert validate_mitigation_playbook(_valid_mitigation_playbook_markdown()) == []

    def test_missing_meta_block_fails(self):
        text = _valid_mitigation_playbook_markdown()
        text = text.replace("<!-- meta -->", "").replace("<!-- /meta -->", "")
        violations = validate_mitigation_playbook(text)
        assert any("meta" in v and "missing" in v for v in violations)

    def test_missing_required_meta_key(self):
        text = _valid_mitigation_playbook_markdown()
        text = text.replace("class_name: Missing environment variable\n", "")
        violations = validate_mitigation_playbook(text)
        assert any("class_name" in v for v in violations)

    def test_seen_not_integer(self):
        text = _valid_mitigation_playbook_markdown()
        text = text.replace("seen: 3", "seen: not_a_number")
        violations = validate_mitigation_playbook(text)
        assert any("seen" in v and "integer" in v for v in violations)

    def test_created_from_invalid_value(self):
        text = _valid_mitigation_playbook_markdown()
        text = text.replace("created_from: success", "created_from: magic")
        violations = validate_mitigation_playbook(text)
        assert any("created_from" in v for v in violations)

    def test_missing_applicability_section(self):
        text = _valid_mitigation_playbook_markdown()
        text = text.replace(
            "## Applicability\n"
            "- A required environment variable is absent from the failing Deployment's container env spec.\n\n",
            "",
        )
        violations = validate_mitigation_playbook(text)
        assert any("Applicability" in v for v in violations)

    def test_missing_mitigation_procedure_section(self):
        text = _valid_mitigation_playbook_markdown()
        text = text.replace(
            "## Mitigation Procedure\n"
            "1. **Action:** kubectl set env deploy/<DEPLOYMENT> -n <NAMESPACE> <ENV_VAR>=<VALUE>\n"
            "   **Expected outcome:** rollout starts on the patched Deployment.\n\n",
            "",
        )
        violations = validate_mitigation_playbook(text)
        assert any("Mitigation Procedure" in v for v in violations)

    def test_missing_post_mitigation_verification_section(self):
        text = _valid_mitigation_playbook_markdown()
        text = text.replace(
            "## Post-Mitigation Verification\n"
            "1. **Action:** kubectl get pods -n <NAMESPACE> -l app=<APP>\n"
            "   **Expected outcome:** all pods running with no restarts.\n\n",
            "",
        )
        violations = validate_mitigation_playbook(text)
        assert any("Post-Mitigation Verification" in v for v in violations)

    def test_empty_applicability_section(self):
        text = _valid_mitigation_playbook_markdown()
        text = text.replace(
            "## Applicability\n"
            "- A required environment variable is absent from the failing Deployment's container env spec.\n",
            "## Applicability\n",
        )
        violations = validate_mitigation_playbook(text)
        assert any("Applicability" in v and "bullet" in v for v in violations)

    def test_empty_mitigation_procedure_section(self):
        text = _valid_mitigation_playbook_markdown()
        text = text.replace(
            "## Mitigation Procedure\n"
            "1. **Action:** kubectl set env deploy/<DEPLOYMENT> -n <NAMESPACE> <ENV_VAR>=<VALUE>\n"
            "   **Expected outcome:** rollout starts on the patched Deployment.\n",
            "## Mitigation Procedure\n",
        )
        violations = validate_mitigation_playbook(text)
        assert any("Mitigation Procedure" in v and "numbered step" in v for v in violations)

    def test_empty_post_mitigation_verification_section(self):
        text = _valid_mitigation_playbook_markdown()
        text = text.replace(
            "## Post-Mitigation Verification\n"
            "1. **Action:** kubectl get pods -n <NAMESPACE> -l app=<APP>\n"
            "   **Expected outcome:** all pods running with no restarts.\n",
            "## Post-Mitigation Verification\n",
        )
        violations = validate_mitigation_playbook(text)
        assert any("Post-Mitigation Verification" in v and "numbered step" in v for v in violations)

    def test_missing_checkbox_in_required_evidence(self):
        text = _valid_mitigation_playbook_markdown()
        text = text.replace(
            "- [ ] All target Deployment pods are Running with restartCount=0 for at least 30s.",
            "- All target Deployment pods are Running with restartCount=0 for at least 30s.",
        )
        violations = validate_mitigation_playbook(text)
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
        text = _valid_mitigation_playbook_markdown()
        text = text.replace(
            "Restart the failing Deployment after adding the missing env var.",
            f"Restart {phrase} the failing Deployment.",
        )
        violations = validate_mitigation_playbook(text)
        assert any(phrase in v for v in violations)


class TestMitigationPlaybookParseRoundtrip:
    def test_parse_populates_fields(self):
        text = _valid_mitigation_playbook_markdown()
        pb = MitigationPlaybook.parse(text)

        assert pb.slug == "missing_env_variable"
        assert pb.class_name == "Missing environment variable"
        assert pb.seen == 3
        assert pb.created_from == "success"
        assert pb.last_updated == "2026-04-09T12:00:00Z"
        assert "missing env var" in pb.summary
        assert len(pb.applicability) == 1
        assert len(pb.mitigation_procedure) == 1
        assert "kubectl set env" in pb.mitigation_procedure[0]
        assert len(pb.post_mitigation_verification) == 1
        assert len(pb.required_evidence) == 1
        assert len(pb.known_pitfalls) == 1
        assert len(pb.failure_patterns) == 1

    def test_roundtrip_to_markdown_reparses(self):
        original = MitigationPlaybook.parse(_valid_mitigation_playbook_markdown())
        reparsed = MitigationPlaybook.parse(original.to_markdown())

        assert reparsed.slug == original.slug
        assert reparsed.class_name == original.class_name
        assert reparsed.seen == original.seen
        assert reparsed.created_from == original.created_from
        assert reparsed.summary == original.summary
        assert reparsed.applicability == original.applicability
        assert reparsed.mitigation_procedure == original.mitigation_procedure
        assert reparsed.post_mitigation_verification == original.post_mitigation_verification
        assert reparsed.required_evidence == original.required_evidence
        assert reparsed.known_pitfalls == original.known_pitfalls
        assert reparsed.failure_patterns == original.failure_patterns

    def test_required_evidence_submission_gate_qualifier_stripped(self):
        text = _valid_mitigation_playbook_markdown().replace(
            "## Required Evidence\n",
            "## Required Evidence (Submission Gate)\n",
        )
        pb = MitigationPlaybook.parse(text)
        assert len(pb.required_evidence) == 1
        assert "restartCount" in pb.required_evidence[0]

    def test_parse_raises_validation_error_on_invalid(self):
        text = _valid_mitigation_playbook_markdown().replace("seen: 3", "seen: not_a_number")
        with pytest.raises(MitigationPlaybookValidationError) as excinfo:
            MitigationPlaybook.parse(text)
        assert excinfo.value.violations
        assert any("seen" in v for v in excinfo.value.violations)


class TestMitigationPlaybookStore:
    def _store(self, tmp_path: Path) -> MitigationPlaybookStore:
        return MitigationPlaybookStore(tmp_path / "mitigation_playbooks")

    def test_save_creates_dir_and_file(self, tmp_path: Path):
        store = self._store(tmp_path)
        pb = MitigationPlaybook.parse(_valid_mitigation_playbook_markdown())

        path = store.save(pb)

        assert path.exists()
        assert path.name == "missing_env_variable.md"
        assert store.playbooks_dir.is_dir()

    def test_load_missing_returns_none(self, tmp_path: Path):
        store = self._store(tmp_path)
        assert store.load("does_not_exist") is None

    def test_load_returns_saved_playbook(self, tmp_path: Path):
        store = self._store(tmp_path)
        pb = MitigationPlaybook.parse(_valid_mitigation_playbook_markdown())
        store.save(pb)

        loaded = store.load("missing_env_variable")
        assert loaded is not None
        assert loaded.slug == pb.slug
        assert loaded.class_name == pb.class_name
        assert loaded.summary == pb.summary

    def test_save_twice_with_changes_records_diff(self, tmp_path: Path):
        store = self._store(tmp_path)
        pb1 = MitigationPlaybook.parse(_valid_mitigation_playbook_markdown())
        store.save(pb1)

        pb2 = pb1.model_copy(
            update={
                "summary": "Updated summary: patch then verify rollout.",
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
        pb = MitigationPlaybook.parse(_valid_mitigation_playbook_markdown(slug="canonical", class_name="Canonical"))
        store.save(pb)

        store.add_alias("old_name", "canonical")

        loaded = store.load("old_name")
        assert loaded is not None
        assert loaded.slug == "canonical"

    def test_archive_consolidation_moves_file_to_history(self, tmp_path: Path):
        store = self._store(tmp_path)
        loser = MitigationPlaybook.parse(_valid_mitigation_playbook_markdown(slug="loser", class_name="Loser"))
        winner = MitigationPlaybook.parse(_valid_mitigation_playbook_markdown(slug="winner", class_name="Winner"))
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
            store.save(MitigationPlaybook.parse(_valid_mitigation_playbook_markdown(slug=slug, class_name=cls)))
        store.add_alias("old", "alpha")  # creates .aliases.yaml

        slugs = store.list_slugs()

        assert slugs == ["alpha", "bravo", "charlie"]
        assert ".history" not in slugs
        assert ".aliases" not in slugs
