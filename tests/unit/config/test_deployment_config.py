import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from app_operator.config import Config, DeploymentConfig


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
        with pytest.raises(ValueError, match="Remote deployment is not currently supported"):
            DeploymentConfig(target="remote")

    def test_invalid_target(self):
        """Invalid target should raise ValueError."""
        with pytest.raises(ValueError, match="Invalid target"):
            DeploymentConfig(target="unknown")

    def test_from_dict(self):
        """Config.from_dict should parse deployment section."""
        data = {
            "agent": {"backend": "codex", "model": "test-model"},
            "deployment": {"platform": "k8s", "target": "local"},
        }
        config = Config.from_dict(data)
        assert config.deployment.platform == "k8s"
        assert config.deployment.target == "local"


class TestDeploymentConfigProperty:
    """Property-based tests for DeploymentConfig validation."""

    @given(platform=st.sampled_from(["docker", "k8s"]))
    @settings(max_examples=10, deadline=1000)
    def test_any_valid_platform_accepted(self, platform):
        """Any valid platform string should be accepted and stored as-is."""
        config = DeploymentConfig(platform=platform)
        assert config.platform == platform

    @given(platform=st.text(min_size=1, max_size=50))
    @settings(max_examples=50, deadline=1000)
    def test_any_invalid_platform_rejected(self, platform):
        """Any platform string not in the valid set should raise ValueError."""
        assume(platform.lower() not in {"docker", "k8s"})
        with pytest.raises(ValueError, match="Invalid platform"):
            DeploymentConfig(platform=platform)

    @given(platform=st.sampled_from(["DOCKER", "K8S", "Docker", "K8s"]))
    @settings(max_examples=10, deadline=1000)
    def test_case_sensitivity_for_platform(self, platform):
        """Platform validation is case-sensitive; mixed-case values are rejected."""
        with pytest.raises(ValueError, match="Invalid platform"):
            DeploymentConfig(platform=platform)

    @given(st.just("local"))
    @settings(max_examples=1, deadline=1000)
    def test_local_target_always_accepted(self, target):
        """The 'local' target should always be accepted."""
        config = DeploymentConfig(target=target)
        assert config.target == "local"

    @given(target=st.text(min_size=1, max_size=50))
    @settings(max_examples=50, deadline=1000)
    def test_non_local_target_always_rejected(self, target):
        """Any target other than 'local' should raise ValueError.

        Note: 'remote' is explicitly blocked even though it is technically in
        VALID_TARGETS, so all non-'local' values raise ValueError.
        """
        assume(target != "local")
        with pytest.raises(ValueError, match="(Invalid target|Remote deployment)"):
            DeploymentConfig(target=target)
