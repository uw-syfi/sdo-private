"""Tests for token cost calculation."""

import pytest

from app_operator.dspy_integration._cost import (
    MODEL_PRICING,
    calculate_cost,
    get_model_pricing,
)


class TestCalculateCost:
    """Tests for calculate_cost function."""

    def test_claude_sonnet_cost(self):
        """Test cost calculation for Claude Sonnet."""
        # Claude Sonnet: $3/1M input, $15/1M output
        cost = calculate_cost("claude-sonnet-4-5", 1_000_000, 1_000_000)
        assert cost == 18.00  # 3 + 15

    def test_claude_opus_cost(self):
        """Test cost calculation for Claude Opus."""
        # Claude Opus: $15/1M input, $75/1M output
        cost = calculate_cost("claude-opus-4-5", 1_000_000, 1_000_000)
        assert cost == 90.00  # 15 + 75

    def test_gpt4_cost(self):
        """Test cost calculation for GPT-4."""
        # GPT-4: $30/1M input, $60/1M output
        cost = calculate_cost("gpt-4", 1_000_000, 1_000_000)
        assert cost == 90.00  # 30 + 60

    def test_gemini_pro_cost(self):
        """Test cost calculation for Gemini Pro."""
        # Gemini 1.5 Pro: $1.25/1M input, $5/1M output
        cost = calculate_cost("gemini-1.5-pro", 1_000_000, 1_000_000)
        assert cost == 6.25  # 1.25 + 5

    def test_small_token_count(self):
        """Test cost calculation with small token counts."""
        # Claude Sonnet: $3/1M input, $15/1M output
        # 1000 tokens input = $0.003, 500 tokens output = $0.0075
        cost = calculate_cost("claude-sonnet-4-5", 1_000, 500)
        assert pytest.approx(cost, 0.0001) == 0.0105

    def test_zero_tokens(self):
        """Test cost calculation with zero tokens."""
        cost = calculate_cost("claude-sonnet-4-5", 0, 0)
        assert cost == 0.0

    def test_input_only(self):
        """Test cost calculation with only input tokens."""
        # Claude Sonnet: $3/1M input
        cost = calculate_cost("claude-sonnet-4-5", 100_000, 0)
        assert pytest.approx(cost, 0.0001) == 0.30

    def test_output_only(self):
        """Test cost calculation with only output tokens."""
        # Claude Sonnet: $15/1M output
        cost = calculate_cost("claude-sonnet-4-5", 0, 50_000)
        assert pytest.approx(cost, 0.0001) == 0.75

    def test_realistic_scenario(self):
        """Test cost calculation for realistic usage."""
        # Claude Sonnet with 5000 input, 2000 output tokens
        # Input: (5000/1M) * $3 = $0.015
        # Output: (2000/1M) * $15 = $0.030
        # Total: $0.045
        cost = calculate_cost("claude-sonnet-4-5", 5_000, 2_000)
        assert pytest.approx(cost, 0.0001) == 0.045

    def test_unknown_model_returns_zero(self):
        """Test that unknown model returns 0.0 with a warning."""
        cost = calculate_cost("unknown-model", 1_000, 1_000)
        assert cost == 0.0

    def test_prefixed_model_normalizes(self):
        """Test that provider-prefixed model names are normalized."""
        # "anthropic/claude-sonnet-4-5" should resolve to "claude-sonnet-4-5"
        cost = calculate_cost("anthropic/claude-sonnet-4-5", 1_000_000, 1_000_000)
        assert cost == 18.00

    def test_prefixed_gemini_model(self):
        """Test that gemini-prefixed model names are normalized."""
        cost = calculate_cost("gemini/gemini-2.0-flash", 1_000_000, 1_000_000)
        assert cost == 0.50  # 0.10 + 0.40

    def test_all_models_have_pricing(self):
        """Test that all models in MODEL_PRICING can be calculated."""
        for model in MODEL_PRICING:
            cost = calculate_cost(model, 1_000, 1_000)
            assert cost > 0.0


class TestGetModelPricing:
    """Tests for get_model_pricing function."""

    def test_claude_sonnet_pricing(self):
        """Test getting pricing for Claude Sonnet."""
        pricing = get_model_pricing("claude-sonnet-4-5")
        assert pricing == {"input": 3.00, "output": 15.00}

    def test_claude_opus_pricing(self):
        """Test getting pricing for Claude Opus."""
        pricing = get_model_pricing("claude-opus-4-5")
        assert pricing == {"input": 15.00, "output": 75.00}

    def test_gpt4_pricing(self):
        """Test getting pricing for GPT-4."""
        pricing = get_model_pricing("gpt-4")
        assert pricing == {"input": 30.00, "output": 60.00}

    def test_gemini_pro_pricing(self):
        """Test getting pricing for Gemini Pro."""
        pricing = get_model_pricing("gemini-1.5-pro")
        assert pricing == {"input": 1.25, "output": 5.00}

    def test_unknown_model_returns_none(self):
        """Test that unknown model returns None."""
        pricing = get_model_pricing("unknown-model")
        assert pricing is None

    def test_pricing_structure(self):
        """Test that all pricing entries have correct structure."""
        for _model, pricing in MODEL_PRICING.items():
            assert isinstance(pricing, dict)
            assert "input" in pricing
            assert "output" in pricing
            assert isinstance(pricing["input"], (int, float))
            assert isinstance(pricing["output"], (int, float))
            assert pricing["input"] > 0
            assert pricing["output"] > 0
