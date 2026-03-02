"""DSPy configuration dataclasses.

Configuration for DSPy prompt optimization, including optimization settings,
auto-rollback parameters, and metric weights.

Note: The canonical definitions live in app_operator.config to keep the
foundational config module free of application-layer imports.  This module
re-exports them so that existing callers of
``app_operator.dspy_integration.config`` continue to work unchanged.
"""

from app_operator.config import (
    DSPyConfig,
    DSPyOptimizationConfig,
    DSPyAutoRollbackConfig,
)

__all__ = [
    "DSPyConfig",
    "DSPyOptimizationConfig",
    "DSPyAutoRollbackConfig",
]
