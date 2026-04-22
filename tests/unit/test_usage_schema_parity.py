"""Pin the shared schema contract between the two TokenUsage copies.

``agentshim.usage.TokenUsage`` and ``libs.pydantic_agent._usage.TokenUsage``
are intentionally duplicated to avoid a cross-lib dependency. The contract
they share is the ``to_dict()`` output shape; this test catches drift.
"""

from __future__ import annotations

from agentshim.usage import TokenUsage as CliTokenUsage

from libs.pydantic_agent._usage import TokenUsage as PydTokenUsage


def test_to_dict_shapes_match():
    a = CliTokenUsage(input_tokens=1, output_tokens=2, cached_input_tokens=3, turns=4)
    b = PydTokenUsage(input_tokens=1, output_tokens=2, cached_input_tokens=3, turns=4)
    assert a.to_dict() == b.to_dict()


def test_default_shapes_match():
    assert CliTokenUsage().to_dict() == PydTokenUsage().to_dict()


def test_field_sets_match():
    assert set(CliTokenUsage().to_dict()) == set(PydTokenUsage().to_dict())
