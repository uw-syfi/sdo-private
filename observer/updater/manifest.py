from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from observer.updater.errors import ObserverUpdaterError


class ManifestError(ObserverUpdaterError):
    """Raised when .sds/diagnostics/manifest.yaml is invalid."""


DETECTOR_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]*$")
GO_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class DetectorManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    package: str
    constructor: str = "New"

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


class ObserverDiagnosticsManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_version: str = Field(alias="apiVersion")
    kind: str
    sdk_version: str = Field(alias="sdkVersion")
    detectors: list[DetectorManifest]

    @model_validator(mode="after")
    def validate_header(self) -> ObserverDiagnosticsManifest:
        if self.api_version != "sds.dev/v1alpha1":
            raise ValueError("apiVersion must be sds.dev/v1alpha1")
        if self.kind != "ObserverDiagnostics":
            raise ValueError("kind must be ObserverDiagnostics")
        if not self.sdk_version:
            raise ValueError("sdkVersion is required")
        if not self.detectors:
            raise ValueError("at least one detector is required")
        ids = [detector.id for detector in self.detectors]
        duplicates = sorted({detector_id for detector_id in ids if ids.count(detector_id) > 1})
        if duplicates:
            raise ValueError(f"duplicate detector id(s): {', '.join(duplicates)}")
        return self


def load_manifest(path: Path, *, app_root: Path) -> ObserverDiagnosticsManifest:
    diagnostics_dir = path.parent
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ManifestError(f"read manifest: {exc}") from exc
    if not isinstance(data, dict):
        raise ManifestError("manifest must be a YAML mapping")
    try:
        manifest = ObserverDiagnosticsManifest.model_validate(data)
    except ValidationError as exc:
        raise ManifestError(str(exc)) from exc

    _validate_detector_files(manifest, diagnostics_dir=diagnostics_dir)
    return manifest


def _validate_detector_files(manifest: ObserverDiagnosticsManifest, *, diagnostics_dir: Path) -> None:
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


def manifest_to_dict(manifest: ObserverDiagnosticsManifest) -> dict[str, Any]:
    return manifest.model_dump(by_alias=True)
