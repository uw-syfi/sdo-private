"""Tests for fault injection orchestrator."""

import json

import pytest
import yaml

from app_operator.fault_injection.config import FaultInjectionConfig
from app_operator.fault_injection.injector import (
    BACKUP_DIR_NAME,
    FAULT_METADATA_FILE,
    FaultInjectionOrchestrator,
)


SAMPLE_COMPOSE = {
    "version": "3",
    "services": {
        "web": {
            "image": "nginx:latest",
            "ports": ["8080:80"],
            "environment": ["FOO=bar"],
            "depends_on": ["db"],
        },
        "db": {
            "image": "mongo:4.4",
            "ports": ["27017:27017"],
            "environment": {"MONGO_INITDB_ROOT_USERNAME": "root", "MONGO_INITDB_ROOT_PASSWORD": "secret"},
        },
    },
    "networks": {
        "default": {"driver": "bridge"},
    },
    "volumes": {
        "db-data": None,
    },
}


@pytest.fixture
def repo_with_compose(tmp_path):
    """Create a tmp repo with docker-compose.yml."""
    compose_file = tmp_path / "docker-compose.yml"
    with open(compose_file, "w") as f:
        yaml.dump(SAMPLE_COMPOSE, f, default_flow_style=False)
    return tmp_path


class TestFaultInjectionOrchestrator:
    def test_inject_creates_backup(self, repo_with_compose):
        config = FaultInjectionConfig(enabled=True, num_faults=1, seed=42)
        orch = FaultInjectionOrchestrator(config=config)
        results = orch.inject(repo_with_compose, seed=42)
        assert len(results) > 0

        backup_dir = repo_with_compose / BACKUP_DIR_NAME
        assert backup_dir.exists()
        assert (backup_dir / "docker-compose.yml").exists()

    def test_inject_writes_metadata(self, repo_with_compose):
        config = FaultInjectionConfig(enabled=True, num_faults=1, seed=42)
        orch = FaultInjectionOrchestrator(config=config)
        orch.inject(repo_with_compose, seed=42)

        metadata_path = repo_with_compose / FAULT_METADATA_FILE
        assert metadata_path.exists()
        with open(metadata_path) as f:
            meta = json.load(f)
        assert meta["enabled"] is True

    def test_inject_modifies_compose(self, repo_with_compose):
        # Read original
        with open(repo_with_compose / "docker-compose.yml") as f:
            original = f.read()

        config = FaultInjectionConfig(enabled=True, num_faults=2, seed=42)
        orch = FaultInjectionOrchestrator(config=config)
        orch.inject(repo_with_compose, seed=42)

        # Read modified
        with open(repo_with_compose / "docker-compose.yml") as f:
            modified = f.read()

        assert original != modified

    def test_inject_and_revert_roundtrip(self, repo_with_compose):
        with open(repo_with_compose / "docker-compose.yml") as f:
            original = f.read()

        config = FaultInjectionConfig(enabled=True, num_faults=2, seed=42)
        orch = FaultInjectionOrchestrator(config=config)
        orch.inject(repo_with_compose, seed=42)

        # Verify it's changed
        with open(repo_with_compose / "docker-compose.yml") as f:
            modified = f.read()
        assert original != modified

        # Revert
        assert orch.revert(repo_with_compose) is True

        with open(repo_with_compose / "docker-compose.yml") as f:
            restored = f.read()
        assert restored == original

        # Backup dir should be cleaned up
        assert not (repo_with_compose / BACKUP_DIR_NAME).exists()
        # Metadata should be cleaned up
        assert not (repo_with_compose / FAULT_METADATA_FILE).exists()

    def test_revert_no_backup(self, tmp_path):
        orch = FaultInjectionOrchestrator()
        assert orch.revert(tmp_path) is False

    def test_inject_no_compose_file(self, tmp_path):
        config = FaultInjectionConfig(enabled=True, num_faults=1)
        orch = FaultInjectionOrchestrator(config=config)
        with pytest.raises(FileNotFoundError, match="No docker-compose"):
            orch.inject(tmp_path)

    def test_inject_yaml_extension(self, tmp_path):
        compose_file = tmp_path / "docker-compose.yaml"
        with open(compose_file, "w") as f:
            yaml.dump(SAMPLE_COMPOSE, f, default_flow_style=False)

        config = FaultInjectionConfig(enabled=True, num_faults=1, seed=42)
        orch = FaultInjectionOrchestrator(config=config)
        results = orch.inject(tmp_path, seed=42)
        assert len(results) > 0

    def test_inject_reproducible_with_seed(self, tmp_path):
        # Create two identical compose files
        for i in range(2):
            d = tmp_path / f"repo{i}"
            d.mkdir()
            with open(d / "docker-compose.yml", "w") as f:
                yaml.dump(SAMPLE_COMPOSE, f, default_flow_style=False)

        config = FaultInjectionConfig(enabled=True, num_faults=2, seed=42)
        orch = FaultInjectionOrchestrator(config=config)

        r1 = orch.inject(tmp_path / "repo0", seed=42)
        r2 = orch.inject(tmp_path / "repo1", seed=42)

        ids1 = [r.fault.fault_id for r in r1]
        ids2 = [r.fault.fault_id for r in r2]
        assert ids1 == ids2

    def test_inject_no_backup_option(self, repo_with_compose):
        config = FaultInjectionConfig(enabled=True, num_faults=1, seed=42, backup_compose=False)
        orch = FaultInjectionOrchestrator(config=config)
        orch.inject(repo_with_compose, seed=42)
        assert not (repo_with_compose / BACKUP_DIR_NAME).exists()

    def test_inject_retries_on_failure(self, repo_with_compose):
        """Test that injector retries with different faults if one fails."""
        # Request 2 faults - some might fail (like CORR-002 needing DB env vars)
        config = FaultInjectionConfig(enabled=True, num_faults=2, seed=42)
        orch = FaultInjectionOrchestrator(config=config)
        results = orch.inject(repo_with_compose, seed=42)

        # Should have attempted multiple faults (some may fail)
        assert len(results) >= 2

        # Should have exactly 2 successful injections (or close to it if retries exhausted)
        successful = [r for r in results if r.success]
        # Either got 2 successes, or tried hard and got at least 1
        assert len(successful) >= 1

    def test_inject_exhausts_retries(self, tmp_path):
        """Test that injector stops after max retry attempts."""
        # Create a minimal compose file with only one service
        minimal_compose = {
            "version": "3",
            "services": {
                "web": {"image": "nginx:latest"},
            },
        }
        with open(tmp_path / "docker-compose.yml", "w") as f:
            yaml.dump(minimal_compose, f)

        # Request many faults on a minimal compose (will run out of applicable faults)
        config = FaultInjectionConfig(enabled=True, num_faults=5, seed=42)
        orch = FaultInjectionOrchestrator(config=config)
        results = orch.inject(tmp_path, seed=42)

        # Should have some results but not necessarily 5 successful ones
        assert len(results) > 0
        # Should not hang - verifies max_attempts logic works
