"""Tests for GEPAConfig validation."""

import pytest

from app_operator.config import GEPAConfig


class TestGEPAConfigIntegerFields:
    """Test integer field validation for all positive-int fields."""

    POSITIVE_INT_FIELDS = [
        "max_steps",
        "num_candidates",
        "minibatch_size",
        "validation_size",
        "patience",
        "checkpoint_interval",
    ]

    @pytest.mark.parametrize("field_name", POSITIVE_INT_FIELDS)
    def test_zero_rejected(self, field_name):
        with pytest.raises(ValueError, match=f"{field_name} must be positive"):
            GEPAConfig(**{field_name: 0})  # type: ignore[arg-type]

    def test_string_type_rejected(self):
        with pytest.raises(TypeError, match="max_steps must be int"):
            GEPAConfig(max_steps="5")  # type: ignore[arg-type]


class TestGEPAConfigProbabilityFields:
    """Test probability field validation."""

    @pytest.mark.parametrize("field_name", ["mutation_probability", "diversity_probability"])
    def test_out_of_range_rejected(self, field_name):
        with pytest.raises(ValueError, match=f"{field_name} must be in range"):
            GEPAConfig(**{field_name: -0.1})  # type: ignore[arg-type]

    def test_string_type_rejected(self):
        with pytest.raises(TypeError, match="mutation_probability must be numeric"):
            GEPAConfig(mutation_probability="0.5")  # type: ignore[arg-type]


class TestGEPAConfigReflectionProvider:
    """Test reflection_provider validation."""

    def test_case_insensitive(self):
        config = GEPAConfig(reflection_provider="Anthropic")
        assert config.reflection_provider == "anthropic"

    def test_invalid_provider_rejected(self):
        with pytest.raises(ValueError, match="Invalid reflection_provider"):
            GEPAConfig(reflection_provider="invalid_provider")

    def test_non_string_rejected(self):
        with pytest.raises(TypeError, match="reflection_provider must be str"):
            GEPAConfig(reflection_provider=123)  # type: ignore[arg-type]


class TestGEPAConfigSeed:
    """Test seed validation."""

    def test_string_rejected(self):
        with pytest.raises(TypeError, match="seed must be int or None"):
            GEPAConfig(seed="42")  # type: ignore[arg-type]
