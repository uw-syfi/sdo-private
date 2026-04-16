"""Unit tests for app_operator.repo_evidence."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

from app_operator.repo_evidence import (
    RepoEvidence,
    ServiceEvidence,
    _extract_consul_registration_services,
    extract_repo_evidence,
    find_invalid_consul_registration_requirements,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_service(
    name: str,
    has_local_source: bool = False,
    self_registers: bool = False,
) -> ServiceEvidence:
    return ServiceEvidence(
        name=name,
        has_local_source=has_local_source,
        source_dirs=(f"cmd/{name}",) if has_local_source else (),
        self_registers=self_registers,
        registration_patterns=(r"\bRegistry\.Register\s*\(",) if self_registers else (),
    )


def make_evidence(*services: ServiceEvidence) -> RepoEvidence:
    return RepoEvidence(services=tuple(services))


# ---------------------------------------------------------------------------
# RepoEvidence helpers
# ---------------------------------------------------------------------------


class TestRepoEvidence:
    def test_service_lookup_found(self):
        svc = make_service("frontend", has_local_source=True)
        ev = make_evidence(svc)
        assert ev.service("frontend") is svc

    def test_service_lookup_missing(self):
        ev = make_evidence(make_service("frontend", has_local_source=True))
        assert ev.service("nonexistent") is None

    def test_service_names_with_local_source(self):
        ev = make_evidence(
            make_service("frontend", has_local_source=True),
            make_service("consul", has_local_source=False),
            make_service("search", has_local_source=True),
        )
        assert ev.service_names_with_local_source() == ("frontend", "search")

    def test_service_names_that_self_register(self):
        ev = make_evidence(
            make_service("frontend", has_local_source=True, self_registers=False),
            make_service("search", has_local_source=True, self_registers=True),
            make_service("profile", has_local_source=True, self_registers=True),
        )
        assert set(ev.service_names_that_self_register()) == {"profile", "search"}


# ---------------------------------------------------------------------------
# extract_repo_evidence
# ---------------------------------------------------------------------------


class TestExtractRepoEvidence:
    def test_empty_repo(self, tmp_path: Path):
        ev = extract_repo_evidence(tmp_path)
        assert ev.services == ()

    def test_discovers_cmd_services(self, tmp_path: Path):
        (tmp_path / "cmd" / "frontend").mkdir(parents=True)
        (tmp_path / "cmd" / "search").mkdir(parents=True)
        ev = extract_repo_evidence(tmp_path)
        names = {svc.name for svc in ev.services}
        assert names == {"frontend", "search"}

    def test_discovers_services_dir(self, tmp_path: Path):
        (tmp_path / "services" / "geo").mkdir(parents=True)
        ev = extract_repo_evidence(tmp_path)
        assert any(svc.name == "geo" for svc in ev.services)

    def test_service_with_local_source_no_registration(self, tmp_path: Path):
        service_dir = tmp_path / "cmd" / "frontend"
        service_dir.mkdir(parents=True)
        (service_dir / "main.go").write_text("package main\nfunc main() {}")
        ev = extract_repo_evidence(tmp_path)
        svc = ev.service("frontend")
        assert svc is not None
        assert svc.has_local_source is True
        assert svc.self_registers is False

    def test_service_with_self_registration(self, tmp_path: Path):
        service_dir = tmp_path / "cmd" / "search"
        service_dir.mkdir(parents=True)
        (service_dir / "main.go").write_text("package main\nfunc main() { Registry.Register(svc) }")
        ev = extract_repo_evidence(tmp_path)
        svc = ev.service("search")
        assert svc is not None
        assert svc.self_registers is True

    def test_skips_vendor_dirs(self, tmp_path: Path):
        service_dir = tmp_path / "cmd" / "profile"
        service_dir.mkdir(parents=True)
        vendor_dir = service_dir / "vendor" / "consul"
        vendor_dir.mkdir(parents=True)
        (vendor_dir / "client.go").write_text("package consul\nfunc Reg() { Registry.Register(x) }")
        (service_dir / "main.go").write_text("package main\nfunc main() {}")
        ev = extract_repo_evidence(tmp_path)
        svc = ev.service("profile")
        assert svc is not None
        assert svc.self_registers is False

    def test_ignores_non_code_files(self, tmp_path: Path):
        service_dir = tmp_path / "cmd" / "frontend"
        service_dir.mkdir(parents=True)
        (service_dir / "README.md").write_text("Registry.Register(x)")
        ev = extract_repo_evidence(tmp_path)
        svc = ev.service("frontend")
        assert svc is not None
        assert svc.self_registers is False


# ---------------------------------------------------------------------------
# _extract_consul_registration_services
# ---------------------------------------------------------------------------


class TestExtractConsulRegistrationServices:
    def test_extracts_from_bash_array(self):
        content = """
services=("frontend" "search" "profile")
for s in "${services[@]}"; do
  curl /v1/catalog/services
done
"""
        result = _extract_consul_registration_services(content)
        assert "frontend" in result
        assert "search" in result

    def test_extracts_from_verifying_comment(self):
        content = """
echo "Verifying registration of search service in Consul"
curl /v1/catalog/services
"""
        result = _extract_consul_registration_services(content)
        assert "search" in result

    def test_extracts_from_grep_pattern(self):
        content = 'grep -q "geo" <<< "$registered_services"'
        result = _extract_consul_registration_services(content)
        assert "geo" in result

    def test_empty_when_no_consul_check(self):
        content = "docker compose ps"
        result = _extract_consul_registration_services(content)
        assert result == set()


# ---------------------------------------------------------------------------
# find_invalid_consul_registration_requirements
# ---------------------------------------------------------------------------


class TestFindInvalidConsulRegistrationRequirements:
    def test_no_consul_check_returns_empty(self):
        evidence = make_evidence(make_service("frontend", has_local_source=True))
        result = find_invalid_consul_registration_requirements("docker compose ps", evidence)
        assert result == []

    def test_service_with_no_local_source_is_skipped(self):
        # consul itself has no local source — not our concern
        evidence = make_evidence(make_service("consul", has_local_source=False))
        content = 'grep -q "consul" <<< "$registered_services"\ncurl /v1/catalog/services'
        result = find_invalid_consul_registration_requirements(content, evidence)
        assert result == []

    def test_self_registering_service_is_valid(self):
        evidence = make_evidence(make_service("search", has_local_source=True, self_registers=True))
        content = 'grep -q "search" <<< "$registered_services"\ncurl /v1/catalog/services'
        result = find_invalid_consul_registration_requirements(content, evidence)
        assert result == []

    def test_non_self_registering_local_service_is_invalid(self):
        evidence = make_evidence(make_service("frontend", has_local_source=True, self_registers=False))
        content = 'grep -q "frontend" <<< "$registered_services"\ncurl /v1/catalog/services'
        result = find_invalid_consul_registration_requirements(content, evidence)
        assert result == ["frontend"]

    def test_unknown_service_is_skipped(self):
        # Service appears in health check but is not in repo evidence at all
        evidence = make_evidence()
        content = 'grep -q "redis" <<< "$registered_services"\ncurl /v1/catalog/services'
        result = find_invalid_consul_registration_requirements(content, evidence)
        assert result == []

    def test_mixed_services_returns_only_invalid(self):
        evidence = make_evidence(
            make_service("frontend", has_local_source=True, self_registers=False),
            make_service("search", has_local_source=True, self_registers=True),
        )
        content = (
            'grep -q "frontend" <<< "$registered_services"\n'
            'grep -q "search" <<< "$registered_services"\n'
            "curl /v1/catalog/services"
        )
        result = find_invalid_consul_registration_requirements(content, evidence)
        assert result == ["frontend"]
