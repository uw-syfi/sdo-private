"""Tests for app_operator_dspy.optimize."""

import pytest

from app_operator_dspy.optimize import OPTIMIZERS, optimize


class TestOptimize:
    def test_unknown_optimizer_raises(self):
        with pytest.raises(ValueError, match="Unknown optimizer"):
            optimize(None, [], lambda *a: 1.0, optimizer="Nonexistent")

    def test_all_registered_optimizers_are_callable(self):
        for name, cls in OPTIMIZERS.items():
            assert callable(cls), f"{name} is not callable"
