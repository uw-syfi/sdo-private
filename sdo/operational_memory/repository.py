from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Generic, TypeVar

import yaml
from pydantic import BaseModel, ValidationError

from sdo.operational_memory.models import (
    MEMORY_SCHEMA_VERSION,
    ArchitectureMetadata,
    ArtifactOwner,
    DiagnosticsManifest,
    GoalMetadata,
    OutcomeRecord,
    PlaybookMetadata,
    TrafficMix,
)

MetadataT = TypeVar("MetadataT", bound=BaseModel)


class MemoryRepositoryError(ValueError):
    """Raised when operational memory cannot be read or safely updated."""


@dataclass(frozen=True)
class MarkdownArtifact(Generic[MetadataT]):
    path: Path
    metadata: MetadataT
    body: str


class MemoryRepository:
    def __init__(self, app_root: Path) -> None:
        self.app_root = app_root.resolve()
        canonical = self.app_root / ".sdo"
        if canonical.exists() or canonical.is_symlink():
            selected = canonical
        else:
            raise MemoryRepositoryError(f"application has no .sdo operational memory: {self.app_root}")
        self.memory_root = self._contained(selected, label="memory root")

    def schema_version(self) -> int:
        path = self.memory_root / "schema-version"
        try:
            version = int(path.read_text(encoding="utf-8").strip())
        except (OSError, ValueError) as exc:
            raise MemoryRepositoryError(f"invalid memory schema version at {path}") from exc
        if version != MEMORY_SCHEMA_VERSION:
            raise MemoryRepositoryError(
                f"unsupported memory schema version {version}; expected {MEMORY_SCHEMA_VERSION}"
            )
        return version

    def goal(self) -> MarkdownArtifact[GoalMetadata]:
        return self._markdown("goal.md", GoalMetadata)

    def architecture(self) -> MarkdownArtifact[ArchitectureMetadata]:
        return self._markdown("arch.md", ArchitectureMetadata)

    def playbooks(self) -> list[MarkdownArtifact[PlaybookMetadata]]:
        playbook_root = self.memory_root / "playbooks"
        artifacts = []
        for path in sorted(playbook_root.glob("*/README.md")):
            relative = path.relative_to(self.memory_root).as_posix()
            artifacts.append(self._markdown(relative, PlaybookMetadata))
        return artifacts

    def diagnostics(self) -> DiagnosticsManifest:
        manifest_path = self.memory_root / "diagnostics" / "manifest.yaml"
        try:
            data = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
            manifest = DiagnosticsManifest.model_validate(data)
        except (OSError, yaml.YAMLError, ValidationError) as exc:
            raise MemoryRepositoryError(f"invalid diagnostics manifest: {exc}") from exc
        diagnostics_root = manifest_path.parent.resolve()
        for detector in manifest.detectors:
            package = detector.package.removeprefix("./")
            if not package or Path(package).is_absolute() or ".." in Path(package).parts:
                raise MemoryRepositoryError(f"detector {detector.id!r} package escapes diagnostics root")
            package_path = (diagnostics_root / package).resolve()
            try:
                package_path.relative_to(diagnostics_root)
            except ValueError as exc:
                raise MemoryRepositoryError(f"detector {detector.id!r} package escapes diagnostics root") from exc
            if not package_path.is_dir() or not any(package_path.glob("*.go")):
                raise MemoryRepositoryError(f"detector {detector.id!r} package must contain a Go file")
        return manifest

    def traffic_mixes(self) -> list[TrafficMix]:
        """Health-judge traffic mixes under ``.sdo/diagnostics/traffic/``, sorted by name."""

        directory = self.memory_root / "diagnostics" / "traffic"
        mixes: list[TrafficMix] = []
        for path in sorted(directory.glob("*.yaml")) if directory.is_dir() else []:
            relative = path.relative_to(self.memory_root).as_posix()
            self._contained(path, label=relative)
            try:
                mix = TrafficMix.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
            except (OSError, yaml.YAMLError, ValidationError) as exc:
                raise MemoryRepositoryError(f"invalid traffic mix {relative}: {exc}") from exc
            if mix.name != path.stem:
                raise MemoryRepositoryError(f"traffic mix {relative}: name {mix.name!r} must match its file name")
            mixes.append(mix)
        return mixes

    def outcomes(self) -> list[OutcomeRecord]:
        path = self.memory_root / "outcomes.jsonl"
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise MemoryRepositoryError(f"read outcomes: {exc}") from exc
        outcomes = []
        for line_number, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            try:
                outcomes.append(OutcomeRecord.model_validate_json(line))
            except ValidationError as exc:
                raise MemoryRepositoryError(f"invalid outcome on line {line_number}: {exc}") from exc
        return outcomes

    def append_outcome(self, outcome: OutcomeRecord, *, actor: ArtifactOwner) -> None:
        if actor != ArtifactOwner.CONTROLLER:
            raise MemoryRepositoryError("only the controller may append outcomes")
        path = self.memory_root / "outcomes.jsonl"
        with path.open("a", encoding="utf-8") as stream:
            stream.write(outcome.model_dump_json(exclude_none=True) + "\n")

    def _markdown(self, relative_path: str, model: type[MetadataT]) -> MarkdownArtifact[MetadataT]:
        path = self._contained(self.memory_root / relative_path, label=relative_path)
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise MemoryRepositoryError(f"read {relative_path}: {exc}") from exc
        metadata_data, body = _parse_front_matter(text, path)
        try:
            metadata = model.model_validate(metadata_data)
        except ValidationError as exc:
            raise MemoryRepositoryError(f"invalid front matter in {relative_path}: {exc}") from exc
        return MarkdownArtifact(path=path, metadata=metadata, body=body)

    def _contained(self, path: Path, *, label: str) -> Path:
        resolved = path.resolve()
        try:
            resolved.relative_to(self.app_root)
        except ValueError as exc:
            raise MemoryRepositoryError(f"{label} escapes application root") from exc
        return resolved


def _parse_front_matter(text: str, path: Path) -> tuple[dict[str, object], str]:
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        raise MemoryRepositoryError(f"{path} must start with YAML front matter")
    closing = next((index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---"), None)
    if closing is None:
        raise MemoryRepositoryError(f"{path} has unterminated YAML front matter")
    try:
        metadata = yaml.safe_load("".join(lines[1:closing]))
    except yaml.YAMLError as exc:
        raise MemoryRepositoryError(f"invalid YAML front matter in {path}: {exc}") from exc
    if not isinstance(metadata, dict):
        raise MemoryRepositoryError(f"front matter in {path} must be a mapping")
    return metadata, "".join(lines[closing + 1 :]).lstrip("\n")
