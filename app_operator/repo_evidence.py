"""Repository evidence extractor for health-check validation.

Extracts structured facts about services in a repository, reusable by
preflight validation, prompt rendering, and health-judge analysis.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

_SOURCE_BASE_DIRS = ("cmd", "services")

_REGISTRATION_PATTERNS = (
    r"\bRegistry\.Register\s*\(",
    r"\bregistry\.Register\s*\(",
    r"\bServiceRegister\s*\(",
    r"\bAgent\(\)\.ServiceRegister\s*\(",
)

_CODE_SUFFIXES = frozenset(
    {
        ".go",
        ".java",
        ".js",
        ".jsx",
        ".kt",
        ".php",
        ".py",
        ".rb",
        ".rs",
        ".ts",
        ".tsx",
    }
)

_SKIP_DIRS = frozenset({".git", "__pycache__", "node_modules", "vendor"})


@dataclass(frozen=True)
class ServiceEvidence:
    """Structural facts about a single service in the repository."""

    name: str
    has_local_source: bool
    source_dirs: tuple[str, ...]
    self_registers: bool
    registration_patterns: tuple[str, ...]


@dataclass(frozen=True)
class RepoEvidence:
    """Structured facts extracted from a repository before deployment."""

    services: tuple[ServiceEvidence, ...]

    def service(self, name: str) -> ServiceEvidence | None:
        """Return evidence for a named service, or None if not found."""
        for svc in self.services:
            if svc.name == name:
                return svc
        return None

    def service_names_with_local_source(self) -> tuple[str, ...]:
        """Return names of services that have local source code."""
        return tuple(svc.name for svc in self.services if svc.has_local_source)

    def service_names_that_self_register(self) -> tuple[str, ...]:
        """Return names of services that appear to self-register with service discovery."""
        return tuple(svc.name for svc in self.services if svc.self_registers)


def _find_source_dirs(repo_path: Path, service_name: str) -> tuple[str, ...]:
    found: list[str] = []
    for base_dir in _SOURCE_BASE_DIRS:
        service_dir = repo_path / base_dir / service_name
        if service_dir.exists() and service_dir.is_dir():
            found.append(f"{base_dir}/{service_name}")
    return tuple(found)


def _find_registration_patterns(repo_path: Path, service_name: str) -> tuple[str, ...]:
    found: list[str] = []
    for base_dir in _SOURCE_BASE_DIRS:
        service_dir = repo_path / base_dir / service_name
        if not service_dir.exists() or not service_dir.is_dir():
            continue
        for path in service_dir.rglob("*"):
            if not path.is_file() or path.suffix not in _CODE_SUFFIXES:
                continue
            if any(part in _SKIP_DIRS for part in path.parts):
                continue
            try:
                content = path.read_text()
            except OSError:
                continue
            for pattern in _REGISTRATION_PATTERNS:
                if re.search(pattern, content) and pattern not in found:
                    found.append(pattern)
    return tuple(found)


def _build_service_evidence(repo_path: Path, service_name: str) -> ServiceEvidence:
    source_dirs = _find_source_dirs(repo_path, service_name)
    has_local_source = bool(source_dirs)
    registration_patterns = _find_registration_patterns(repo_path, service_name) if has_local_source else ()
    return ServiceEvidence(
        name=service_name,
        has_local_source=has_local_source,
        source_dirs=source_dirs,
        self_registers=bool(registration_patterns),
        registration_patterns=registration_patterns,
    )


def _discover_service_names(repo_path: Path) -> set[str]:
    names: set[str] = set()
    for base_dir in _SOURCE_BASE_DIRS:
        base = repo_path / base_dir
        if not base.exists() or not base.is_dir():
            continue
        for child in base.iterdir():
            if child.is_dir():
                names.add(child.name)
    return names


def extract_repo_evidence(repo_path: Path) -> RepoEvidence:
    """Extract structured repository evidence for all discovered services.

    Args:
        repo_path: Root of the application repository.

    Returns:
        RepoEvidence with facts about each service found in standard directories.
    """
    service_names = _discover_service_names(repo_path)
    services = tuple(_build_service_evidence(repo_path, name) for name in sorted(service_names))
    return RepoEvidence(services=services)


def _extract_consul_registration_services(health_content: str) -> set[str]:
    """Extract literal service names used in Consul catalog registration checks."""
    services: set[str] = set()

    for match in re.finditer(
        r"^\s*(?P<var>[A-Za-z_][A-Za-z0-9_]*)=\((?P<body>[^)]*)\)",
        health_content,
        flags=re.MULTILINE,
    ):
        var_name = match.group("var")
        if "service" not in var_name.lower():
            continue
        if not re.search(rf"\b{re.escape(var_name)}\b", health_content[match.end() :]):
            continue
        services.update(re.findall(r'["\']([A-Za-z0-9_.-]+)["\']', match.group("body")))

    for match in re.finditer(
        r"Verifying registration of ['\"]?([A-Za-z0-9_.-]+)['\"]? service in Consul",
        health_content,
    ):
        services.add(match.group(1))

    for match in re.finditer(
        r"grep\s+-q\s+['\"]([A-Za-z0-9_.-]+)['\"][^\n]*registered_services",
        health_content,
    ):
        services.add(match.group(1))

    return services


def find_invalid_consul_registration_requirements(
    health_content: str,
    evidence: RepoEvidence,
) -> list[str]:
    """Return services that health_check.sh wrongly requires in Consul service discovery.

    A service is invalid if it has local source code but does not appear to
    self-register with service discovery.

    Args:
        health_content: Contents of health_check.sh.
        evidence: Pre-extracted repository evidence.

    Returns:
        Sorted list of invalid service names.
    """
    if "/v1/catalog/services" not in health_content:
        return []

    checked_services = _extract_consul_registration_services(health_content)
    invalid: list[str] = []
    for service_name in sorted(checked_services):
        svc = evidence.service(service_name)
        if svc is None:
            continue
        if not svc.has_local_source:
            continue
        if svc.self_registers:
            continue
        invalid.append(service_name)
    return invalid
