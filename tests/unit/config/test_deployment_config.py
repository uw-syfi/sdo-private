import pytest
from app_operator.config import DeploymentConfig, Config


class TestDeploymentConfigValidation:
    """Tests for DeploymentConfig validation."""

    def test_platform_default(self):
        """Default platform should be docker."""
        config = DeploymentConfig()
        assert config.platform == "docker"

    def test_target_default(self):
        """Default target should be local."""
        config = DeploymentConfig()
        assert config.target == "local"

    def test_valid_platforms(self):
        """Valid platforms should be accepted."""
        valid_platforms = ["docker", "k8s"]
        for platform in valid_platforms:
            config = DeploymentConfig(platform=platform)
            assert config.platform == platform

    def test_invalid_platform(self):
        """Invalid platform should raise ValueError."""
        with pytest.raises(ValueError, match="Invalid platform"):
            DeploymentConfig(platform="azure")

    def test_target_remote_raises_error(self):
        """Remote target should raise ValueError."""
        with pytest.raises(
            ValueError, match="Remote deployment is not currently supported"
        ):
            DeploymentConfig(target="remote")

    def test_invalid_target(self):
        """Invalid target should raise ValueError."""
        with pytest.raises(ValueError, match="Invalid target"):
            DeploymentConfig(target="unknown")

    def test_from_dict(self):
        """Config.from_dict should parse deployment section."""
        data = {"deployment": {"platform": "k8s", "target": "local"}}
        config = Config.from_dict(data)
        assert config.deployment.platform == "k8s"
        assert config.deployment.target == "local"
