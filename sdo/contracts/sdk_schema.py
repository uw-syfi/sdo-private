"""Committed validator-image schema identity (the expected value).

Stage 4 of the single-source-of-truth track (``docs/seam-contracts-decisions.md``,
seam 4). This is the schema identity the production detector-validation path
expects the validator image to embed. The validator sandbox passes it to the
in-image ``controller.builder.check_cli`` as ``--expect-schema``; the image
recomputes its own identity from its baked SDK and fails loud on a mismatch (a
stale image).

It lives in ``sdo.contracts`` because the sandbox runner (``sdo.operational_memory``)
may depend on ``sdo.contracts`` but not on ``controller.builder``. The value is
produced by ``controller.builder.schema.schema_identity()``; the ratchet test
``tests/unit/controller/builder/test_schema_identity.py`` asserts the two stay
in sync, so an SDK schema change that forgets to update this value -- and thus
to rebuild the validator image -- fails the test rather than silently shipping a
stale validator.
"""

from __future__ import annotations

SDK_SCHEMA_IDENTITY = "sdo.dev/v1alpha1+sdk.a24a1bfd980f6444"
"""Regenerate with ``controller.builder.schema.schema_identity()`` when the SDK schema changes."""
