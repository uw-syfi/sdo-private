"""Root-cause-first playbook models, parsing, and storage."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, cast

import yaml
from pydantic import BaseModel, Field

from sregym_agents.crucible.knowledge_base.incident_review import PlaceholderResolutionRule


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class RootCauseValidationError(ValueError):
    """Raised when a root-cause playbook document is malformed."""

    def __init__(self, violations: list[str]):
        self.violations = list(violations)
        super().__init__("; ".join(self.violations))


def _split_front_matter(text: str) -> tuple[dict[str, Any], str]:
    stripped = text.lstrip()
    if not stripped.startswith("---\n"):
        raise RootCauseValidationError(["missing YAML front matter"])
    _, _, rest = stripped.partition("---\n")
    meta_text, sep, body = rest.partition("\n---\n")
    if not sep:
        raise RootCauseValidationError(["front matter missing closing '---'"])
    raw_data: object = yaml.safe_load(meta_text) or {}
    if not isinstance(raw_data, dict):
        raise RootCauseValidationError(["front matter must parse to a mapping"])
    raw_mapping = cast("dict[object, Any]", raw_data)
    normalized: dict[str, Any] = {str(key): value for key, value in raw_mapping.items()}
    return normalized, body.lstrip()


def _extract_sections(text: str) -> dict[str, str]:
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in text.splitlines():
        if line.startswith("## "):
            current = line[3:].strip()
            sections[current] = []
            continue
        if current is not None:
            sections[current].append(line)
    return {name: "\n".join(lines).strip() for name, lines in sections.items()}


def _parse_bullets(text: str) -> list[str]:
    result: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("- [ ] "):
            result.append(stripped[6:].strip())
        elif stripped.startswith("- "):
            result.append(stripped[2:].strip())
    return [item for item in result if item]


def _parse_numbered(text: str) -> list[str]:
    result: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        prefix, dot, tail = stripped.partition(". ")
        if dot and prefix.isdigit() and tail.strip():
            result.append(stripped)
    return result


def _render_bullets(items: list[str], *, checkbox: bool = False) -> str:
    marker = "- [ ]" if checkbox else "-"
    return "\n".join(f"{marker} {item}" for item in items)


def _render_numbered(items: list[str]) -> str:
    rendered: list[str] = []
    for idx, item in enumerate(items, start=1):
        _, dot, _ = item.partition(". ")
        rendered.append(item if dot and item.split(". ", 1)[0].isdigit() else f"{idx}. {item}")
    return "\n".join(rendered)


def _parse_placeholder_resolution(items: list[str]) -> tuple[list[PlaceholderResolutionRule], list[str]]:
    rules: list[PlaceholderResolutionRule] = []
    violations: list[str] = []
    for item in items:
        symbol, sep, guidance = item.partition(":")
        symbol = symbol.strip()
        guidance = guidance.strip()
        if not sep or not symbol or not guidance:
            violations.append(
                "each '## Placeholder Resolution' bullet must have the form '- <SYMBOL>: how to resolve it'"
            )
            continue
        rules.append(PlaceholderResolutionRule(symbol=symbol, resolution_guidance=guidance))
    return rules, violations


def _render_placeholder_resolution(items: list[PlaceholderResolutionRule]) -> str:
    return "\n".join(f"- {item.symbol}: {item.resolution_guidance}" for item in items)


def _empty_placeholder_resolution() -> list[PlaceholderResolutionRule]:
    return []


class DiagnosisFrontMatter(BaseModel):
    slug: str
    root_cause: str
    when_to_consider: list[str] = Field(default_factory=list)
    disambiguators: list[str] = Field(default_factory=list)


class MitigationFrontMatter(BaseModel):
    slug: str
    root_cause: str


class DiagnosisPlaybook(BaseModel):
    front_matter: DiagnosisFrontMatter
    summary: str
    triage_checks: list[str] = Field(default_factory=list)
    fault_localization_checks: list[str] = Field(default_factory=list)
    verification_checks: list[str] = Field(default_factory=list)
    required_evidence: list[str] = Field(default_factory=list)
    known_confounders: list[str] = Field(default_factory=list)
    markdown: str = ""

    @property
    def slug(self) -> str:
        return self.front_matter.slug

    @classmethod
    def parse(cls, text: str) -> DiagnosisPlaybook:
        meta, body = _split_front_matter(text)
        fm = DiagnosisFrontMatter.model_validate(meta)
        sections = _extract_sections(body)
        violations = [
            f"missing section '## {section}'"
            for section in (
                "Summary",
                "Triage Checks",
                "Fault Localization",
                "Verification Checks",
                "Required Evidence",
                "Known Confounders",
            )
            if section not in sections
        ]
        if violations:
            raise RootCauseValidationError(violations)
        triage_checks = _parse_numbered(sections.get("Triage Checks", ""))
        fault_localization_checks = _parse_numbered(sections.get("Fault Localization", ""))
        verification_checks = _parse_numbered(sections.get("Verification Checks", ""))
        required_evidence = _parse_bullets(sections.get("Required Evidence", ""))
        known_confounders = _parse_bullets(sections.get("Known Confounders", ""))
        if not fm.when_to_consider:
            violations.append("front matter 'when_to_consider' must contain at least one item")
        if not fm.disambiguators:
            violations.append("front matter 'disambiguators' must contain at least one item")
        if not triage_checks:
            violations.append("'## Triage Checks' must contain at least one numbered step")
        if not fault_localization_checks:
            violations.append("'## Fault Localization' must contain at least one numbered step")
        if not verification_checks:
            violations.append("'## Verification Checks' must contain at least one numbered step")
        if not required_evidence:
            violations.append("'## Required Evidence' must contain at least one bullet")
        if violations:
            raise RootCauseValidationError(violations)
        return cls(
            front_matter=fm,
            summary=sections.get("Summary", "").strip(),
            triage_checks=triage_checks,
            fault_localization_checks=fault_localization_checks,
            verification_checks=verification_checks,
            required_evidence=required_evidence,
            known_confounders=known_confounders,
            markdown=text,
        )

    def to_markdown(self) -> str:
        if self.markdown:
            try:
                parsed = DiagnosisPlaybook.parse(self.markdown)
                if parsed.front_matter.slug == self.slug:
                    return self.markdown
            except Exception:
                pass
        meta = yaml.safe_dump(self.front_matter.model_dump(mode="python"), sort_keys=False).strip()
        body = "\n\n".join(
            [
                "# Diagnosis Playbook",
                "## Summary\n" + self.summary.strip(),
                "## Triage Checks\n" + _render_numbered(self.triage_checks),
                "## Fault Localization\n" + _render_numbered(self.fault_localization_checks),
                "## Verification Checks\n" + _render_numbered(self.verification_checks),
                "## Required Evidence\n" + _render_bullets(self.required_evidence, checkbox=True),
                "## Known Confounders\n" + _render_bullets(self.known_confounders),
            ]
        ).strip()
        return f"---\n{meta}\n---\n\n{body}\n"


class MitigationPlaybook(BaseModel):
    front_matter: MitigationFrontMatter
    summary: str
    mitigation_procedure: list[str] = Field(default_factory=list)
    placeholder_resolution: list[PlaceholderResolutionRule] = Field(default_factory=_empty_placeholder_resolution)
    verification_checks: list[str] = Field(default_factory=list)
    rollback_stop_conditions: list[str] = Field(default_factory=list)
    markdown: str = ""

    @property
    def slug(self) -> str:
        return self.front_matter.slug

    @classmethod
    def parse(cls, text: str) -> MitigationPlaybook:
        meta, body = _split_front_matter(text)
        fm = MitigationFrontMatter.model_validate(meta)
        sections = _extract_sections(body)
        violations = [
            f"missing section '## {section}'"
            for section in ("Summary", "Mitigation Procedure", "Verification Checks", "Rollback / Stop Conditions")
            if section not in sections
        ]
        mitigation_procedure = _parse_numbered(sections.get("Mitigation Procedure", ""))
        placeholder_resolution, placeholder_violations = _parse_placeholder_resolution(
            _parse_bullets(sections.get("Placeholder Resolution", ""))
        )
        verification_checks = _parse_numbered(sections.get("Verification Checks", ""))
        rollback_stop_conditions = _parse_bullets(sections.get("Rollback / Stop Conditions", ""))
        violations.extend(placeholder_violations)
        if not mitigation_procedure:
            violations.append("'## Mitigation Procedure' must contain at least one numbered step")
        if not verification_checks:
            violations.append("'## Verification Checks' must contain at least one numbered step")
        if violations:
            raise RootCauseValidationError(violations)
        return cls(
            front_matter=fm,
            summary=sections.get("Summary", "").strip(),
            mitigation_procedure=mitigation_procedure,
            placeholder_resolution=placeholder_resolution,
            verification_checks=verification_checks,
            rollback_stop_conditions=rollback_stop_conditions,
            markdown=text,
        )

    def to_markdown(self) -> str:
        if self.markdown:
            try:
                parsed = MitigationPlaybook.parse(self.markdown)
                if parsed.front_matter.slug == self.slug:
                    return self.markdown
            except Exception:
                pass
        meta = yaml.safe_dump(self.front_matter.model_dump(mode="python"), sort_keys=False).strip()
        sections = [
            "# Mitigation Playbook",
            "## Summary\n" + self.summary.strip(),
            "## Mitigation Procedure\n" + _render_numbered(self.mitigation_procedure),
        ]
        if self.placeholder_resolution:
            sections.append("## Placeholder Resolution\n" + _render_placeholder_resolution(self.placeholder_resolution))
        sections.extend(
            [
                "## Verification Checks\n" + _render_numbered(self.verification_checks),
                "## Rollback / Stop Conditions\n" + _render_bullets(self.rollback_stop_conditions),
            ]
        )
        body = "\n\n".join(sections).strip()
        return f"---\n{meta}\n---\n\n{body}\n"


class RootCauseMeta(BaseModel):
    slug: str
    status: Literal["active", "deprecated"] = "active"
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)
    created_from: str = ""
    merged_from: list[str] = Field(default_factory=list)


class RootCauseManifestEntry(BaseModel):
    slug: str
    path: str
    has_diagnosis: bool
    has_mitigation: bool
    status: Literal["active", "deprecated"] = "active"


class RootCauseManifest(BaseModel):
    schema_version: int = 3
    root_causes: list[RootCauseManifestEntry] = []

    def entry_for(self, slug: str) -> RootCauseManifestEntry | None:
        for entry in self.root_causes:
            if entry.slug == slug:
                return entry
        return None


@dataclass(frozen=True)
class RootCausePaths:
    scope_dir: Path

    @property
    def manifest(self) -> Path:
        return self.scope_dir / "manifest.json"

    @property
    def root_causes_dir(self) -> Path:
        return self.scope_dir / "root_causes"

    @property
    def incidents_dir(self) -> Path:
        return self.scope_dir / "incidents"

    def root_cause_dir(self, slug: str) -> Path:
        return self.root_causes_dir / slug

    def diagnosis_path(self, slug: str) -> Path:
        return self.root_cause_dir(slug) / "diagnosis.md"

    def mitigation_path(self, slug: str) -> Path:
        return self.root_cause_dir(slug) / "mitigation.md"

    def meta_path(self, slug: str) -> Path:
        return self.root_cause_dir(slug) / "meta.json"


class RootCauseStore:
    """Filesystem-backed root-cause KB store."""

    def __init__(self, scope_dir: Path):
        self.paths = RootCausePaths(Path(scope_dir))
        self.paths.scope_dir.mkdir(parents=True, exist_ok=True)
        self.paths.root_causes_dir.mkdir(parents=True, exist_ok=True)

    def load_manifest(self) -> RootCauseManifest:
        if not self.paths.manifest.exists():
            return RootCauseManifest()
        return RootCauseManifest.model_validate(json.loads(self.paths.manifest.read_text()))

    def save_manifest(self, manifest: RootCauseManifest) -> None:
        self.paths.manifest.write_text(json.dumps(manifest.model_dump(mode="python"), indent=2) + "\n")

    def refresh_manifest(self) -> RootCauseManifest:
        entries: list[RootCauseManifestEntry] = []
        for root_dir in sorted(self.paths.root_causes_dir.iterdir()):
            if not root_dir.is_dir():
                continue
            slug = root_dir.name
            meta = self.load_meta(slug)
            entries.append(
                RootCauseManifestEntry(
                    slug=slug,
                    path=str(root_dir.relative_to(self.paths.scope_dir)),
                    has_diagnosis=self.paths.diagnosis_path(slug).exists(),
                    has_mitigation=self.paths.mitigation_path(slug).exists(),
                    status=meta.status,
                )
            )
        manifest = RootCauseManifest(root_causes=entries)
        self.save_manifest(manifest)
        return manifest

    def load_meta(self, slug: str) -> RootCauseMeta:
        path = self.paths.meta_path(slug)
        if not path.exists():
            return RootCauseMeta(slug=slug)
        return RootCauseMeta.model_validate(json.loads(path.read_text()))

    def save_meta(self, meta: RootCauseMeta) -> None:
        path = self.paths.meta_path(meta.slug)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(meta.model_dump(mode="python"), indent=2) + "\n")

    def list_active_diagnosis_cards(self) -> list[DiagnosisFrontMatter]:
        manifest = self.load_manifest()
        cards: list[DiagnosisFrontMatter] = []
        for entry in manifest.root_causes:
            if entry.status != "active" or not entry.has_diagnosis:
                continue
            diagnosis = self.load_diagnosis(entry.slug)
            if diagnosis is not None:
                cards.append(diagnosis.front_matter)
        return cards

    def load_diagnosis(self, slug: str) -> DiagnosisPlaybook | None:
        path = self.paths.diagnosis_path(slug)
        if not path.exists():
            return None
        return DiagnosisPlaybook.parse(path.read_text())

    def load_mitigation(self, slug: str) -> MitigationPlaybook | None:
        path = self.paths.mitigation_path(slug)
        if not path.exists():
            return None
        return MitigationPlaybook.parse(path.read_text())

    def save_diagnosis(
        self,
        playbook: DiagnosisPlaybook,
        *,
        created_from: str = "",
        merged_from: list[str] | None = None,
    ) -> Path:
        path = self.paths.diagnosis_path(playbook.slug)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(playbook.to_markdown())
        meta = self.load_meta(playbook.slug)
        meta = meta.model_copy(
            update={
                "slug": playbook.slug,
                "updated_at": utc_now_iso(),
                "created_from": meta.created_from or created_from,
                "merged_from": list(merged_from or meta.merged_from),
            }
        )
        if not self.paths.meta_path(playbook.slug).exists():
            meta = meta.model_copy(update={"created_at": utc_now_iso()})
        self.save_meta(meta)
        self.refresh_manifest()
        return path

    def save_mitigation(
        self,
        playbook: MitigationPlaybook,
        *,
        created_from: str = "",
    ) -> Path:
        path = self.paths.mitigation_path(playbook.slug)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(playbook.to_markdown())
        meta = self.load_meta(playbook.slug)
        meta = meta.model_copy(
            update={
                "slug": playbook.slug,
                "updated_at": utc_now_iso(),
                "created_from": meta.created_from or created_from,
            }
        )
        if not self.paths.meta_path(playbook.slug).exists():
            meta = meta.model_copy(update={"created_at": utc_now_iso()})
        self.save_meta(meta)
        self.refresh_manifest()
        return path


class KBView:
    """Runtime view over one scoped root-cause KB."""

    def __init__(self, scope_dir: Path):
        self.scope_dir = Path(scope_dir)
        self.store = RootCauseStore(self.scope_dir)

    def list_active_diagnosis_cards(self) -> list[DiagnosisFrontMatter]:
        return self.store.list_active_diagnosis_cards()

    def load_diagnosis(self, slug: str) -> DiagnosisPlaybook | None:
        return self.store.load_diagnosis(slug)

    def load_mitigation(self, slug: str) -> MitigationPlaybook | None:
        return self.store.load_mitigation(slug)

    def load_diagnosis_text(self, slug: str | None) -> str:
        if not slug:
            return ""
        playbook = self.load_diagnosis(slug)
        return playbook.to_markdown() if playbook is not None else ""

    def load_mitigation_text(self, slug: str | None) -> str:
        if not slug:
            return ""
        playbook = self.load_mitigation(slug)
        return playbook.to_markdown() if playbook is not None else ""
