"""Generated proto contract messages and the canonical protojson convention.

This is the Python edge of the proto single-source-of-truth track
(``docs/seam-contracts-decisions.md``). The ``.proto`` files under ``proto/`` are
the one source; ``buf generate`` emits the messages into ``sdo/contracts/_gen``
and this facade re-exports them under a stable import path so callers never
reach into the generated tree directly.

Contracts are serialized as **protojson, never binary**, and share one wire
convention with the Go controller (``controller/runtime`` ``protoContractJSON``):
proto (snake_case) field names, zero-valued fields omitted. ``to_canonical_json``
additionally sorts keys and indents so a payload is byte-comparable across the
Go and Python encoders — that identity is what the round-trip contract tests
assert.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from google.protobuf import json_format

from sdo.contracts._gen.sdo.contracts.v1alpha1.common_pb2 import ObjectRef

if TYPE_CHECKING:
    from google.protobuf.message import Message

__all__ = ["ObjectRef", "parse_json", "to_canonical_json"]


def to_canonical_json(message: Message) -> str:
    """Serialize a proto message to the canonical, byte-stable protojson form."""
    wire = json_format.MessageToJson(message, preserving_proto_field_name=True)
    return json.dumps(json.loads(wire), indent=2, sort_keys=True) + "\n"


def parse_json(text: str, message: Message) -> Message:
    """Parse protojson ``text`` into ``message`` (populated in place and returned)."""
    return json_format.Parse(text, message)
