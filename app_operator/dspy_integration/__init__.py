"""DSPy integration module for prompt optimization.

This module provides DSPy-based prompt optimization capabilities for SDS agents,
including offline optimization, online learning, and automatic rollback.

Public API (eager — no heavy deps):
    - DSPyConfig, DSPyOptimizationConfig, DSPyAutoRollbackConfig

Public API (lazy — imports dspy/litellm on first access):
    - MetricsAggregator, PromptOptimizer, SIGNATURES, EvalExecuteOptimizer
"""

from app_operator.dspy_integration.config import (
    DSPyAutoRollbackConfig,
    DSPyConfig,
    DSPyOptimizationConfig,
)

__all__ = [
    "DSPyConfig",
    "DSPyOptimizationConfig",
    "DSPyAutoRollbackConfig",
    # Lazy-loaded (heavy deps: dspy, litellm)
    "MetricsAggregator",
    "PromptOptimizer",
    "SIGNATURES",
    "EvalExecuteOptimizer",
]

_LAZY_IMPORTS: dict[str, tuple[str, str]] = {
    "MetricsAggregator": ("app_operator.dspy_integration.metrics_aggregator", "MetricsAggregator"),
    "PromptOptimizer": ("app_operator.dspy_integration.optimizer", "PromptOptimizer"),
    "SIGNATURES": ("app_operator.dspy_integration.signatures", "SIGNATURES"),
    "EvalExecuteOptimizer": ("app_operator.dspy_integration.eval_execute", "EvalExecuteOptimizer"),
}


def __getattr__(name: str):
    if name in _LAZY_IMPORTS:
        module_path, attr = _LAZY_IMPORTS[name]
        import importlib

        mod = importlib.import_module(module_path)
        return getattr(mod, attr)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
