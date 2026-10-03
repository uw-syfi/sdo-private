from __future__ import annotations

import argparse
from typing import TYPE_CHECKING

import pytest

from controller.builder import schema
from controller.builder.check_cli import _assert_expected_schema
from controller.builder.check_cli import main as check_main
from controller.builder.schema import SchemaSourceError, schema_digest, schema_identity
from sdo.contracts.sdk_schema import SDK_SCHEMA_IDENTITY

if TYPE_CHECKING:
    from pathlib import Path

# Stage 4 of the single-source-of-truth track (docs/seam-contracts-decisions.md,
# seam 4): the validator image bakes a schema it must not drift from. These
# tests fence the identity, its ratchet against the committed expected value,
# and the loud startup rejection of a stale image.


def test_schema_identity_is_versioned_and_digested() -> None:
    identity = schema_identity()
    assert identity.startswith(f"{schema.SCHEMA_API_VERSION}+sdk.")
    # The digest suffix is a stable 16-hex-char prefix of the content hash.
    suffix = identity.split("+sdk.", 1)[1]
    assert len(suffix) == 16
    assert all(character in "0123456789abcdef" for character in suffix)


def test_committed_identity_matches_the_sdk_sources() -> None:
    # The ratchet: if the Go SDK or manifest schema changes, this fails until
    # SDK_SCHEMA_IDENTITY is regenerated -- which is the prompt to rebuild the
    # validator image. Without it, a schema change could ship with a stale
    # validator that still "passes" (the links-drop bug).
    assert schema_identity() == SDK_SCHEMA_IDENTITY, (
        "SDK schema changed; regenerate sdo/contracts/sdk_schema.py SDK_SCHEMA_IDENTITY "
        "with controller.builder.schema.schema_identity() and rebuild the validator image."
    )


def _fake_controller_tree(root: Path, *, link_field: str) -> None:
    sdk = root / "sdk" / "traffic"
    sdk.mkdir(parents=True)
    (root / "sdk" / "detector.go").write_text("package sdk\n", encoding="utf-8")
    (sdk / "workload.go").write_text(
        f"package traffic\n\ntype Workload struct {{\n\t{link_field}\n}}\n",
        encoding="utf-8",
    )
    builder = root / "builder"
    builder.mkdir()
    (builder / "manifest.py").write_text("SCHEMA = 'manifest'\n", encoding="utf-8")


def test_digest_changes_when_the_schema_surface_changes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    before_root = tmp_path / "before"
    after_root = tmp_path / "after"
    _fake_controller_tree(before_root, link_field="")
    _fake_controller_tree(after_root, link_field="Links []Link")

    monkeypatch.setattr(schema, "_CONTROLLER_ROOT", before_root)
    before = schema_digest()
    monkeypatch.setattr(schema, "_CONTROLLER_ROOT", after_root)
    after = schema_digest()

    assert before != after


def test_test_files_do_not_affect_the_identity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "ctrl"
    _fake_controller_tree(root, link_field="Links []Link")
    monkeypatch.setattr(schema, "_CONTROLLER_ROOT", root)
    baseline = schema_digest()
    # Adding a Go test file must not change the schema identity.
    (root / "sdk" / "traffic" / "workload_test.go").write_text("package traffic\n", encoding="utf-8")
    assert schema_digest() == baseline


def test_missing_sources_raise(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(schema, "_CONTROLLER_ROOT", tmp_path / "absent")
    with pytest.raises(SchemaSourceError):
        schema_digest()


def test_assert_expected_schema_rejects_mismatch() -> None:
    stale = argparse.Namespace(expect_schema="sdo.dev/v1alpha1+sdk.0000000000000000")
    with pytest.raises(ValueError, match="stale validator image"):
        _assert_expected_schema(stale)


def test_assert_expected_schema_accepts_match_and_absent() -> None:
    # A matching identity and an unset expectation are both no-ops (no raise).
    _assert_expected_schema(argparse.Namespace(expect_schema=schema_identity()))
    _assert_expected_schema(argparse.Namespace(expect_schema=None))


def test_check_cli_test_rejects_a_stale_validator_image(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # Regression fixture for the stale-validator bug: an image whose SDK schema
    # no longer matches the tree must fail loud at startup, before any Go build,
    # instead of silently validating against the wrong contract. A wrong
    # --expect-schema stands in for a stale image's self-computed identity.
    exit_code = check_main(["test", "--app", str(tmp_path), "--expect-schema", "sdo.dev/v1alpha1+sdk.0000000000000000"])
    output = capsys.readouterr()
    assert exit_code == 1, output.out + output.err
    assert "stale validator image" in output.out
    assert "0000000000000000" in output.out
