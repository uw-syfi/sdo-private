from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from controller.builder.errors import ControllerBuilderError


class ManifestError(ControllerBuilderError):
    """Raised when an operational-memory diagnostics manifest is invalid."""


DETECTOR_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]*$")
GO_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
DURATION_PART_RE = re.compile(r"(\d+(?:\.\d+)?)(ns|us|µs|ms|s|m|h)")
DURATION_FACTORS = {
    "ns": 1,
    "us": 1_000,
    "µs": 1_000,
    "ms": 1_000_000,
    "s": 1_000_000_000,
    "m": 60_000_000_000,
    "h": 3_600_000_000_000,
}


class DetectorWatchManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_version: str = Field(alias="apiVersion", min_length=1)
    kind: str = Field(min_length=1)
    namespace: str = ""


class PersistenceManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    firing: int = Field(ge=1)
    clearing: int = Field(ge=1)


class BatchingManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    severity: Literal["info", "warn", "critical"]
    debounce: str

    @field_validator("debounce")
    @classmethod
    def validate_debounce(cls, value: str) -> str:
        duration_nanoseconds(value, allow_zero=True)
        return value


class DetectorDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    package: str
    constructor: str = "New"
    detector_class: Literal["health", "incident"] = Field(alias="class")
    owner: Literal["health_judge", "responder"]
    watches: list[DetectorWatchManifest]
    interval: str
    persistence: PersistenceManifest
    batching: BatchingManifest
    possible_playbooks: list[str] = Field(alias="possiblePlaybooks")
    originating_incident: str | None = Field(default=None, alias="originatingIncident")
    originating_commit: str = Field(alias="originatingCommit", min_length=1)

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        if not DETECTOR_ID_RE.fullmatch(value):
            raise ValueError("must be lowercase letters, digits, '.', '_', or '-' and start with a letter or digit")
        return value

    @field_validator("constructor")
    @classmethod
    def validate_constructor(cls, value: str) -> str:
        if not GO_IDENTIFIER_RE.fullmatch(value):
            raise ValueError("must be a Go identifier")
        return value

    @field_validator("interval")
    @classmethod
    def validate_interval(cls, value: str) -> str:
        duration_nanoseconds(value)
        return value

    @model_validator(mode="after")
    def validate_ownership_and_uniqueness(self) -> DetectorDefinition:
        expected_owner = "health_judge" if self.detector_class == "health" else "responder"
        if self.owner != expected_owner:
            raise ValueError(f"class {self.detector_class!r} must be owned by {expected_owner!r}")
        if self.detector_class == "incident" and not self.originating_incident:
            raise ValueError("incident detectors require originatingIncident")
        watch_keys = [(watch.api_version, watch.kind, watch.namespace) for watch in self.watches]
        if len(watch_keys) != len(set(watch_keys)):
            raise ValueError("detector watches must be unique")
        if len(self.possible_playbooks) != len(set(self.possible_playbooks)):
            raise ValueError("possiblePlaybooks must be unique")
        return self


class DetectorManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_version: str = Field(alias="apiVersion")
    kind: str
    sdk_version: str = Field(alias="sdkVersion")
    detectors: list[DetectorDefinition]

    @model_validator(mode="after")
    def validate_header(self) -> DetectorManifest:
        if self.api_version != "sdo.dev/v1alpha1":
            raise ValueError("apiVersion must be sdo.dev/v1alpha1")
        if self.kind != "DetectorManifest":
            raise ValueError("kind must be DetectorManifest")
        if not self.sdk_version:
            raise ValueError("sdkVersion is required")
        if not self.detectors:
            raise ValueError("at least one detector is required")
        ids = [detector.id for detector in self.detectors]
        duplicates = sorted({detector_id for detector_id in ids if ids.count(detector_id) > 1})
        if duplicates:
            raise ValueError(f"duplicate detector id(s): {', '.join(duplicates)}")
        return self


def load_manifest(path: Path, *, app_root: Path) -> DetectorManifest:
    diagnostics_dir = path.parent
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ManifestError(f"read manifest: {exc}") from exc
    if not isinstance(data, dict):
        raise ManifestError("manifest must be a YAML mapping")
    try:
        manifest = DetectorManifest.model_validate(data)
    except ValidationError as exc:
        raise ManifestError(str(exc)) from exc

    _validate_detector_files(manifest, diagnostics_dir=diagnostics_dir)
    return manifest


def _validate_detector_files(manifest: DetectorManifest, *, diagnostics_dir: Path) -> None:
    for detector in manifest.detectors:
        package_dir = _safe_child_path(diagnostics_dir, detector.package, label="detector package")
        if not package_dir.is_dir():
            raise ManifestError(f"detector {detector.id!r} package directory does not exist: {detector.package}")
        if not any(child.suffix == ".go" for child in package_dir.iterdir() if child.is_file()):
            raise ManifestError(f"detector {detector.id!r} package must contain at least one Go file")


def _safe_child_path(root: Path, raw_path: str, *, label: str) -> Path:
    if not raw_path or Path(raw_path).is_absolute():
        raise ManifestError(f"{label} path must be relative")
    parts = Path(raw_path).parts
    if ".." in parts:
        raise ManifestError(f"{label} path must stay inside {root.name}")
    if parts and parts[0] == ".":
        parts = parts[1:]
    candidate = (root / Path(*parts)).resolve()
    root_resolved = root.resolve()
    try:
        candidate.relative_to(root_resolved)
    except ValueError as exc:
        raise ManifestError(f"{label} path must stay inside {root.name}") from exc
    return candidate


def manifest_to_dict(manifest: DetectorManifest) -> dict[str, Any]:
    return manifest.model_dump(by_alias=True)


def duration_nanoseconds(value: str, *, allow_zero: bool = False) -> int:
    if value == "0" and allow_zero:
        return 0
    matches = list(DURATION_PART_RE.finditer(value))
    if not matches or "".join(match.group(0) for match in matches) != value:
        raise ValueError("must be a Go-style duration such as 500ms, 30s, or 1m")
    nanoseconds = sum(float(match.group(1)) * DURATION_FACTORS[match.group(2)] for match in matches)
    if nanoseconds <= 0 and not allow_zero:
        raise ValueError("duration must be positive")
    return int(nanoseconds)
