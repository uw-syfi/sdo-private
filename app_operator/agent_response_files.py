from __future__ import annotations

import html
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from app_operator.core import logger

if TYPE_CHECKING:
    from app_operator.core import FileSystemInterface

_FILE_CHANGE_RE = re.compile(
    r"<file_change\s+path=(['\"])(?P<path>.*?)\1\s*>(?P<content>.*?)</file_change>",
    re.DOTALL | re.IGNORECASE,
)
_FILE_SECTION_RE = re.compile(
    r"(?:^|\n)\s*(?:#+\s*)?FILE:\s*(?P<path>[^\n]+)\n```[^\n]*\n(?P<content>.*?)```",
    re.DOTALL | re.IGNORECASE,
)


@dataclass(frozen=True)
class ResponseFileWrite:
    """A file write extracted from a model response."""

    relative_order: int
    path_text: str
    content: str


def apply_agent_response_file_writes(
    response: str,
    repo_path: Path,
    filesystem: FileSystemInterface,
) -> list[Path]:
    """Apply text-based file writes embedded in an agent response.

    Supports the two formats that appear in SDS fix flows:
    - `<file_change path="...">full file contents</file_change>`
    - `FILE: path` fenced code blocks

    Only writes files that resolve under `repo_path`.
    """

    repo_root = repo_path.resolve()
    writes = _extract_response_file_writes(response)
    applied: list[Path] = []

    for write in writes:
        resolved_path = _resolve_repo_relative_path(write.path_text, repo_root)
        if resolved_path is None:
            logger.warning("Skipping agent file write outside repo: {}", write.path_text)
            continue

        filesystem.mkdir(resolved_path.parent, parents=True, exist_ok=True)
        filesystem.write_text(resolved_path, html.unescape(write.content))
        applied.append(resolved_path)

    return applied


def _extract_response_file_writes(response: str) -> list[ResponseFileWrite]:
    """Return file writes in the order they appear in the response."""

    writes = [
        ResponseFileWrite(
            relative_order=match.start(),
            path_text=html.unescape(match.group("path")).strip(),
            content=match.group("content"),
        )
        for match in _FILE_CHANGE_RE.finditer(response)
    ]
    writes.extend(
        ResponseFileWrite(
            relative_order=match.start(),
            path_text=html.unescape(match.group("path")).strip(),
            content=match.group("content"),
        )
        for match in _FILE_SECTION_RE.finditer(response)
    )

    writes.sort(key=lambda item: item.relative_order)
    return writes


def _resolve_repo_relative_path(path_text: str, repo_root: Path) -> Path | None:
    """Resolve an agent-supplied path and ensure it stays within the repo."""

    raw_path = Path(path_text)
    candidate = raw_path if raw_path.is_absolute() else repo_root / raw_path
    resolved = candidate.resolve()

    try:
        resolved.relative_to(repo_root)
    except ValueError:
        return None

    return resolved
