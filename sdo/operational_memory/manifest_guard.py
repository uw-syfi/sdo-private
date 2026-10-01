"""Deterministic, application-agnostic syntax gate for YAML files in source-repair proposals."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import yaml
from yaml.constructor import ConstructorError

from sdo.operational_memory.validation import MemoryValidationError

if TYPE_CHECKING:
    from pathlib import Path

YAML_SUFFIXES = (".yaml", ".yml")
_TEMPLATE_MARKER = "{{"


class _UniqueKeyLoader(yaml.SafeLoader):
    """SafeLoader that rejects duplicate mapping keys instead of silently keeping the last."""

    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict[Any, Any]:
        seen: set[Any] = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=True)
            try:
                hash(key)
            except TypeError:
                continue
            if key in seen:
                raise ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    f"duplicate mapping key {key!r}",
                    key_node.start_mark,
                )
            seen.add(key)
        return super().construct_mapping(node, deep=deep)


def validate_yaml_manifests(worktree: Path, changed_paths: list[str]) -> None:
    """Reject changed YAML files that do not parse or that repeat a mapping key.

    `kubectl apply -k` and most YAML consumers fail on such files, so a repair that commits one
    would break the next deployment. Deleted files, non-YAML files, and files containing Go/Helm
    template markers (`{{`) are skipped because they are not plain YAML.
    """
    for relative in changed_paths:
        if not relative.endswith(YAML_SUFFIXES):
            continue
        path = worktree / relative
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if _TEMPLATE_MARKER in text:
            continue
        try:
            for _ in yaml.load_all(text, Loader=_UniqueKeyLoader):
                pass
        except yaml.YAMLError as exc:
            raise MemoryValidationError(_describe(relative, exc)) from exc


def _describe(relative: str, exc: yaml.YAMLError) -> str:
    if isinstance(exc, ConstructorError) and exc.problem_mark is not None:
        return (
            f"source repair rejected: {relative} has {exc.problem} at line {exc.problem_mark.line + 1}. "
            "Remove the repeated key; a manifest copied from a live patched object often repeats "
            "fields, so edit the existing source manifest instead of pasting the live object."
        )
    return f"source repair rejected: {relative} is not valid YAML ({exc}). Fix the syntax before proposing the change."
