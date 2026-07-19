"""Crucible knowledge base: cross-problem learning via pydantic-ai Agent."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from .append_only import AppendOnlyKnowledgeBase
from .base import InjectedKB, KnowledgeBase, SessionFiles
from .reflection import Reflector
from .schema import CURRENT_SCHEMA_VERSION, KBSchema, get_schema, migrate_to_current
from .structured import StructuredKnowledgeBase

if TYPE_CHECKING:
    from benchmarks.sregym.agents.crucible._prompts import PromptRenderer
    from benchmarks.sregym.agents.crucible.agents.base import AgentDriver
    from benchmarks.sregym.agents.crucible.config import CrucibleConfig

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
    "seed_kb",
]


logger = logging.getLogger(__name__)


def seed_kb(dest_kb_dir: Path, seed_kb_dir: Path | str | None) -> None:
    """Copy every file from seed_kb_dir into dest_kb_dir, preserving layout.

    Existing destination files are left untouched. Seed and destination are
    assumed to share the same on-disk layout; no restructuring is performed.
    No-op if seed_kb_dir is falsy or does not exist on disk.
    """
    if not seed_kb_dir:
        return
    src = Path(seed_kb_dir)
    if not src.is_dir():
        logger.warning(f"Seed KB dir does not exist: {src}")
        return

    dest_kb_dir.mkdir(parents=True, exist_ok=True)
    copied = 0
    skipped = 0
    for f in src.rglob("*"):
        if not f.is_file():
            continue
        rel = f.relative_to(src)
        out = dest_kb_dir / rel
        if out.exists():
            skipped += 1
            continue
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, out)
        copied += 1
    logger.info(f"Seeded {copied} file(s) from {src} into {dest_kb_dir} (skipped {skipped} already-present)")


def create_knowledge_base(
    kb_type: str,
    kb_dir: Path,
    app_name: str = "unknown",
    *,
    config: CrucibleConfig | None = None,
    renderer: PromptRenderer,
    driver: AgentDriver,
) -> KnowledgeBase:
    """Factory function to create a knowledge base implementation."""
    if kb_type == "structured":
        return StructuredKnowledgeBase(
            kb_dir,
            app_name,
            config=config,
            renderer=renderer,
            driver=driver,
        )
    if kb_type == "append-only":
        return AppendOnlyKnowledgeBase(
            kb_dir,
            app_name,
            config=config,
            renderer=renderer,
            driver=driver,
        )
    raise ValueError(f"Unknown kb_type: {kb_type!r}. Must be 'structured' or 'append-only'.")
