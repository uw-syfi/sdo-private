"""Tests for benchmarks.sregym.participants.crucible.knowledge_base.merge_result."""

from __future__ import annotations

import pytest

from benchmarks.sregym.participants.crucible.knowledge_base.merge_result import (
    MergeEnvelopeError,
    MergeResult,
    Reorganization,
    build_merge_result,
    ensure_slug_lines,
    extract_class_slugs,
    parse_merge_envelope,
    slugify,
)


class TestExtractClassSlugs:
    def test_empty_text_returns_empty_dict(self):
        assert extract_class_slugs("") == {}

    def test_two_blocks_with_explicit_slugs(self):
        text = (
            "# Long Term Summary\n"
            "\n"
            "### Root Cause: OOMKilled Pods\n"
            "\n"
            "#### Slug: oomkilled_pods\n"
            "Some body text.\n"
            "\n"
            "### Root Cause: DNS Resolution Flake\n"
            "\n"
            "#### Slug: dns_flake\n"
            "More body text.\n"
        )
        assert extract_class_slugs(text) == {
            "OOMKilled Pods": "oomkilled_pods",
            "DNS Resolution Flake": "dns_flake",
        }

    def test_missing_slug_line_uses_slugify(self):
        text = "### Root Cause: Database Connection Timeout\nNo slug line here.\n"
        result = extract_class_slugs(text)
        assert result == {
            "Database Connection Timeout": slugify("Database Connection Timeout"),
        }

    def test_slug_line_outside_root_cause_block_ignored(self):
        text = "## Preamble\n#### Slug: stale\n\n### Root Cause: Real Issue\n#### Slug: real_issue\n"
        result = extract_class_slugs(text)
        assert result == {"Real Issue": "real_issue"}
        assert "stale" not in result.values()


class TestEnsureSlugLines:
    def test_empty_input_returns_empty(self):
        assert ensure_slug_lines("") == ""

    def test_already_has_slug_lines_unchanged(self):
        text = "### Root Cause: Something\n\n#### Slug: something\nBody.\n"
        # Round-trip should yield the same mapping; text should contain slug.
        result = ensure_slug_lines(text)
        assert "#### Slug: something" in result
        # Only one occurrence of the slug line should exist.
        assert result.count("#### Slug: something") == 1

    def test_missing_slug_lines_injected(self):
        text = "### Root Cause: Foo Bar\nBody A.\n\n### Root Cause: Baz Qux\nBody B.\n"
        result = ensure_slug_lines(text)
        assert f"#### Slug: {slugify('Foo Bar')}" in result
        assert f"#### Slug: {slugify('Baz Qux')}" in result

    def test_round_trip_idempotent_mapping(self):
        text = (
            "### Root Cause: Foo Bar\nBody A.\n\n### Root Cause: Already Slugged\n#### Slug: already_slugged\nBody B.\n"
        )
        direct = extract_class_slugs(text)
        after_ensure = extract_class_slugs(ensure_slug_lines(text))
        assert direct == after_ensure
        assert after_ensure == {
            "Foo Bar": slugify("Foo Bar"),
            "Already Slugged": "already_slugged",
        }


class TestParseMergeEnvelope:
    def test_parses_envelope_and_returns_leading_text(self):
        output = (
            'Updated summary body.\n<merge_result>{"primary_action": "noop", "primary_class_name": null}</merge_result>'
        )
        envelope, stripped = parse_merge_envelope(output)
        assert envelope == {"primary_action": "noop", "primary_class_name": None}
        assert stripped == "Updated summary body."

    def test_missing_envelope_raises(self):
        with pytest.raises(MergeEnvelopeError):
            parse_merge_envelope("just some text with no envelope")

    def test_malformed_json_raises(self):
        output = "body\n<merge_result>{not valid json}</merge_result>"
        with pytest.raises(MergeEnvelopeError):
            parse_merge_envelope(output)

    def test_json_array_raises(self):
        output = '<merge_result>["not", "an", "object"]</merge_result>'
        with pytest.raises(MergeEnvelopeError):
            parse_merge_envelope(output)

    def test_stripped_text_excludes_envelope_and_trailing_whitespace(self):
        output = 'Summary text here.\n<merge_result>{"primary_action": "noop"}</merge_result>\n\n   \n'
        _, stripped = parse_merge_envelope(output)
        assert "<merge_result>" not in stripped
        assert "</merge_result>" not in stripped
        assert stripped == "Summary text here."


class TestBuildMergeResult:
    def test_primary_action_new_adds_slug(self):
        pre = {"Existing Class": "existing_class"}
        post = {
            "Existing Class": "existing_class",
            "Fresh Class": "fresh_class",
        }
        envelope = {
            "primary_action": "new",
            "primary_class_name": "Fresh Class",
        }
        result = build_merge_result(envelope, pre, post, "new text")
        assert isinstance(result, MergeResult)
        assert result.primary_action == "new"
        assert result.primary_class_name == "Fresh Class"
        assert result.primary_slug == "fresh_class"
        assert result.new_summary_text == "new text"
        assert result.reorganizations == []

    def test_primary_action_merged_existing(self):
        pre = {"Existing Class": "existing_class"}
        post = {"Existing Class": "existing_class"}
        envelope = {
            "primary_action": "merged",
            "primary_class_name": "Existing Class",
        }
        result = build_merge_result(envelope, pre, post, "body")
        assert result.primary_action == "merged"
        assert result.primary_slug == "existing_class"
        assert result.primary_class_name == "Existing Class"

    def test_primary_action_noop_with_null_class(self):
        pre = {"Class A": "class_a"}
        post = {"Class A": "class_a"}
        envelope = {
            "primary_action": "noop",
            "primary_class_name": None,
        }
        result = build_merge_result(envelope, pre, post, "body")
        assert result.primary_action == "noop"
        assert result.primary_class_name is None
        assert result.primary_slug is None

    def test_new_requires_primary_class_name(self):
        envelope = {"primary_action": "new", "primary_class_name": None}
        with pytest.raises(MergeEnvelopeError):
            build_merge_result(envelope, {}, {}, "body")

    def test_invalid_primary_action_raises(self):
        envelope = {"primary_action": "delete", "primary_class_name": "X"}
        with pytest.raises(MergeEnvelopeError):
            build_merge_result(envelope, {}, {}, "body")

    def test_consolidate_reorganization_builds_entry(self):
        pre = {
            "Winner Class": "winner_class",
            "Loser One": "loser_one",
            "Loser Two": "loser_two",
        }
        post = {"Winner Class": "winner_class"}
        envelope = {
            "primary_action": "merged",
            "primary_class_name": "Winner Class",
            "reorganizations": [
                {
                    "type": "consolidate",
                    "winner": "Winner Class",
                    "losers": ["Loser One", "Loser Two"],
                }
            ],
        }
        result = build_merge_result(envelope, pre, post, "body")
        assert len(result.reorganizations) == 1
        reorg = result.reorganizations[0]
        assert isinstance(reorg, Reorganization)
        assert reorg.type == "consolidate"
        assert reorg.winner_slug == "winner_class"
        assert reorg.winner_class_name == "Winner Class"
        assert set(reorg.loser_slugs) == {"loser_one", "loser_two"}

    def test_undeclared_destruction_raises(self):
        pre = {"Class A": "a", "Class B": "b"}
        post = {"Class A": "a"}
        envelope = {
            "primary_action": "noop",
            "primary_class_name": None,
        }
        with pytest.raises(MergeEnvelopeError) as excinfo:
            build_merge_result(envelope, pre, post, "body")
        assert "b" in str(excinfo.value)

    def test_declared_consolidation_allows_slug_removal(self):
        pre = {"Class A": "a", "Class B": "b"}
        post = {"Class A": "a"}
        envelope = {
            "primary_action": "merged",
            "primary_class_name": "Class A",
            "reorganizations": [
                {
                    "type": "consolidate",
                    "winner": "Class A",
                    "losers": ["Class B"],
                }
            ],
        }
        result = build_merge_result(envelope, pre, post, "body")
        assert len(result.reorganizations) == 1
        assert result.reorganizations[0].loser_slugs == ("b",)

    def test_consolidate_missing_winner_raises(self):
        envelope = {
            "primary_action": "noop",
            "primary_class_name": None,
            "reorganizations": [{"type": "consolidate", "losers": ["Loser"]}],
        }
        with pytest.raises(MergeEnvelopeError):
            build_merge_result(envelope, {}, {}, "body")

    def test_consolidate_empty_losers_raises(self):
        envelope = {
            "primary_action": "noop",
            "primary_class_name": None,
            "reorganizations": [{"type": "consolidate", "winner": "Winner", "losers": []}],
        }
        with pytest.raises(MergeEnvelopeError):
            build_merge_result(envelope, {}, {}, "body")

    def test_unknown_reorganization_type_raises(self):
        envelope = {
            "primary_action": "noop",
            "primary_class_name": None,
            "reorganizations": [{"type": "bogus", "winner": "W", "losers": ["L"]}],
        }
        with pytest.raises(MergeEnvelopeError):
            build_merge_result(envelope, {}, {}, "body")
