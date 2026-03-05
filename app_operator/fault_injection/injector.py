"""Fault injection orchestrator.

Coordinates fault selection, injection into Docker Compose files,
backup, and revert operations.
"""

import json
import os
import random
import shutil
import tempfile
from pathlib import Path

from app_operator.fault_injection.compose_faults import COMPOSE_FAULTS, ComposeFaultInjector
from app_operator.fault_injection.config import FaultInjectionConfig
from app_operator.fault_injection.models import FaultResult
from app_operator.fault_injection.registry import FaultRegistry
from app_operator.fault_injection.reporter import FaultReport

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]

BACKUP_DIR_NAME = ".sds-fault-backup"
FAULT_METADATA_FILE = ".sds/fault_injection.json"


class FaultInjectionOrchestrator:
    """Orchestrates fault injection into Docker Compose files.

    Finds the compose file, backs it up, selects faults from the registry,
    injects them, and writes the modified file back.
    """

    def __init__(self, config: FaultInjectionConfig | None = None):
        self.config = config or FaultInjectionConfig()
        self.registry = FaultRegistry(COMPOSE_FAULTS)

    def inject(self, repo_path: Path, seed: int | None = None) -> list[FaultResult]:
        """Inject faults into the Docker Compose file.

        Args:
            repo_path: Path to the repository containing docker-compose.yml.
            seed: Random seed (overrides config.seed if provided).

        Returns:
            List of FaultResult describing injected faults.
        """
        if yaml is None:
            raise ImportError("PyYAML is required for fault injection. Install with: pip install pyyaml")

        repo_path = Path(repo_path)
        compose_file = self._find_compose_file(repo_path)
        if compose_file is None:
            raise FileNotFoundError(f"No docker-compose.yml or docker-compose.yaml found in {repo_path}")

        # Back up original
        if self.config.backup_compose:
            self._backup(repo_path, compose_file)

        # Parse YAML
        with open(compose_file) as f:
            compose_data = yaml.safe_load(f)

        if not isinstance(compose_data, dict) or "services" not in compose_data:
            raise ValueError(f"Invalid compose file: {compose_file}")

        # Select and inject faults with retry logic
        effective_seed = seed if seed is not None else self.config.seed
        rng = random.Random(effective_seed)
        injector = ComposeFaultInjector(rng=rng)

        results: list[FaultResult] = []
        attempted_fault_ids: list[str] = []
        max_attempts = self.config.num_faults * 5  # Allow up to 5x attempts

        attempts = 0
        while len([r for r in results if r.success]) < self.config.num_faults:
            if attempts >= max_attempts:
                break  # Prevent infinite loop

            # Select new fault (excluding already attempted)
            # Create a temporary config with exclusions
            temp_config = FaultInjectionConfig(
                enabled=self.config.enabled,
                num_faults=1,  # Select one at a time
                categories=self.config.categories,
                severities=self.config.severities,
                exclude_faults=self.config.exclude_faults + attempted_fault_ids,
                seed=self.config.seed,
                backup_compose=self.config.backup_compose,
                platform=self.config.platform,
            )

            candidates = self.registry.select(n=1, config=temp_config, rng=rng)
            if not candidates:
                break  # No more faults to try

            fault = candidates[0]
            attempted_fault_ids.append(fault.fault_id)

            # Try to inject
            result = injector.inject(fault, compose_data)
            results.append(result)

            attempts += 1

        # Write modified compose file only if at least one injection succeeded
        if any(r.success for r in results):
            fd, tmp_path = tempfile.mkstemp(dir=str(compose_file.parent), suffix=".tmp")
            try:
                with os.fdopen(fd, "w") as f:
                    yaml.dump(compose_data, f, default_flow_style=False, sort_keys=False)
                os.replace(tmp_path, str(compose_file))
            except Exception:
                os.unlink(tmp_path)
                raise

        # Write metadata for trajectory pickup
        self._write_metadata(repo_path, results)

        return results

    def revert(self, repo_path: Path) -> bool:
        """Revert compose file from backup.

        Args:
            repo_path: Path to the repository.

        Returns:
            True if backup was restored, False if no backup found.
        """
        repo_path = Path(repo_path)
        backup_dir = repo_path / BACKUP_DIR_NAME

        if not backup_dir.exists():
            return False

        # Restore all backed-up files
        restored = False
        for backup_file in backup_dir.iterdir():
            if backup_file.is_file():
                if os.sep in backup_file.name or "/" in backup_file.name:
                    continue
                dest = repo_path / backup_file.name
                if not dest.resolve().is_relative_to(repo_path.resolve()):
                    continue
                shutil.copy2(backup_file, dest)
                restored = True

        # Clean up backup dir
        if restored:
            shutil.rmtree(backup_dir)

        # Clean up metadata
        metadata_file = repo_path / FAULT_METADATA_FILE
        if metadata_file.exists():
            metadata_file.unlink()

        return restored

    @staticmethod
    def _find_compose_file(repo_path: Path) -> Path | None:
        """Find docker-compose file in the repository."""
        for name in ("docker-compose.yml", "docker-compose.yaml"):
            candidate = repo_path / name
            if candidate.exists():
                return candidate
        return None

    @staticmethod
    def _backup(repo_path: Path, compose_file: Path) -> None:
        """Back up the compose file."""
        backup_dir = repo_path / BACKUP_DIR_NAME
        backup_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(compose_file, backup_dir / compose_file.name)

    @staticmethod
    def _write_metadata(repo_path: Path, results: list[FaultResult]) -> None:
        """Write fault injection metadata for trajectory pickup."""
        sds_dir = repo_path / ".sds"
        sds_dir.mkdir(parents=True, exist_ok=True)

        metadata = FaultReport.to_trajectory_metadata(results)
        metadata_path = sds_dir / "fault_injection.json"
        with open(metadata_path, "w") as f:
            json.dump(metadata, f, indent=2)
