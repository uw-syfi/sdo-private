from abc import ABC, abstractmethod
from pathlib import Path

from app_operator.core import Config, FileSystemInterface, logger


class OperatorBase(ABC):
    """Abstract base class for all operator implementations."""

    repo_path: Path
    sds_dir: Path
    config: Config
    filesystem: FileSystemInterface

    def _persist_deployment_config(self) -> None:
        """Persist deployment preference to .sds/config.toml."""
        if not self.filesystem.exists(self.sds_dir):
            self.filesystem.mkdir(self.sds_dir)

        sds_config_path = self.sds_dir / "config.toml"
        if not self.filesystem.exists(sds_config_path):
            logger.info(f"Creating deployment config at {sds_config_path}")
            config_content = (
                "[deployment]\n"
                f'platform = "{self.config.deployment.platform}"\n'
                f'target = "{self.config.deployment.target}"\n'
            )
            self.filesystem.write_text(sds_config_path, config_content)

    @abstractmethod
    def run(self) -> None:
        """Run the operator.

        Returns normally on success.  Raises an exception on failure:
        ``DeploymentError`` for terminal deployment failures,
        ``MonitoringError`` when post-deploy monitoring is unhealthy,
        other ``SdsOperatorError`` subclasses for domain errors, or
        ``KeyboardInterrupt`` for user-initiated shutdown.  The CLI
        boundary (``commands/run.py``) translates exceptions into
        process exit codes.
        """
        ...
