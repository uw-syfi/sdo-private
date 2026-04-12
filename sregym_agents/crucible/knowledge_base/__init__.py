"""Crucible knowledge base: cross-problem learning via pydantic-ai Agent."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .append_only import AppendOnlyKnowledgeBase
from .base import InjectedKB, KnowledgeBase, SessionFiles
from .reflection import Reflector
from .schema import CURRENT_SCHEMA_VERSION, KBSchema, get_schema, migrate_to_current
from .structured import StructuredKnowledgeBase

if TYPE_CHECKING:
    from pathlib import Path

    from sregym_agents.crucible._prompts import PromptRenderer
    from sregym_agents.crucible.config import CrucibleConfig

__all__ = [
    "CURRENT_SCHEMA_VERSION",
    "InjectedKB",
    "KBSchema",
    "KnowledgeBase",
    "Reflector",
    "SessionFiles",
    "create_knowledge_base",
    "get_schema",
    "migrate_to_current",
]


def create_knowledge_base(
    kb_type: str,
    kb_dir: Path,
    model_id: str,
    app_name: str = "unknown",
    seed_kb_dir: Path | None = None,
    *,
    config: CrucibleConfig | None = None,
    renderer: PromptRenderer,
) -> KnowledgeBase:
    """Factory function to create a knowledge base implementation."""
    if kb_type == "structured":
        return StructuredKnowledgeBase(
            kb_dir,
            model_id,
            app_name,
            seed_kb_dir,
            config=config,
            renderer=renderer,
        )
    if kb_type == "append-only":
        return AppendOnlyKnowledgeBase(
            kb_dir,
            model_id,
            app_name,
            config=config,
            renderer=renderer,
        )
    raise ValueError(f"Unknown kb_type: {kb_type!r}. Must be 'structured' or 'append-only'.")
