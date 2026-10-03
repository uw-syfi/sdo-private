"""Validator-image schema identity.

Stage 4 of the single-source-of-truth track (``docs/seam-contracts-decisions.md``,
seam 4). The validator image bakes a copy of the Go SDK that app-authored
detectors compile and are validated against, plus the manifest schema. When that
schema changes but the image is not rebuilt, the stale image validates detectors
against a *different* contract than the one in the tree -- the documented
stale-validator bug, where an old image whose SDK lacked the ``links`` field
silently dropped ``links.yaml``.

``schema_identity()`` fingerprints that embedded schema surface so a stale image
is caught loud at startup (``controller.builder.check_cli --expect-schema``)
instead of silently degrading. The image recomputes its own identity from its
baked sources; the caller passes the identity it expects
(``sdo.contracts.sdk_schema.SDK_SCHEMA_IDENTITY``, the committed value kept in
sync by the ratchet test). A mismatch means the image is stale.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

# The apiVersion the SDK and manifests declare (mirrors
# controller/builder/manifest.py and controller/runtime/protocol.go). The
# identity is version + content digest: the version is the coarse contract line,
# the digest catches same-version schema drift (the links-field class, which
# never changed apiVersion).
SCHEMA_API_VERSION = "sdo.dev/v1alpha1"

# Resolve the schema source relative to this file so the digest is identical
# whether computed from a repo checkout or from the copy baked into the
# validator image (the Dockerfile copies controller/ to /opt/sdo/controller).
_CONTROLLER_ROOT = Path(__file__).resolve().parent.parent


class SchemaSourceError(RuntimeError):
    """The sources needed to compute the validator schema identity are missing."""


def _schema_source_files() -> list[Path]:
    """Return the files whose content defines the validator's embedded schema.

    The whole Go SDK (the surface detectors compile against) plus the manifest
    schema. Go test files are excluded: they are not part of the contract an
    app authors against.
    """

    sdk_dir = _CONTROLLER_ROOT / "sdk"
    manifest = _CONTROLLER_ROOT / "builder" / "manifest.py"
    if not sdk_dir.is_dir():
        raise SchemaSourceError(f"validator SDK schema source missing: {sdk_dir}")
    if not manifest.is_file():
        raise SchemaSourceError(f"validator manifest schema source missing: {manifest}")
    files = [path for path in sdk_dir.rglob("*.go") if not path.name.endswith("_test.go")]
    if not files:
        raise SchemaSourceError(f"no Go SDK schema sources under {sdk_dir}")
    files.append(manifest)
    return files


def schema_digest() -> str:
    """Return a stable hex digest of the validator's embedded schema surface."""

    digest = hashlib.sha256()
    for path in sorted(_schema_source_files(), key=lambda p: p.relative_to(_CONTROLLER_ROOT).as_posix()):
        digest.update(path.relative_to(_CONTROLLER_ROOT).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def schema_identity() -> str:
    """Return the validator schema identity: apiVersion + a content digest.

    Pinned into the validator image (a Docker label and the ``--expect-schema``
    startup check) so a stale image fails loud instead of validating against a
    schema that no longer matches the tree.
    """

    return f"{SCHEMA_API_VERSION}+sdk.{schema_digest()[:16]}"
