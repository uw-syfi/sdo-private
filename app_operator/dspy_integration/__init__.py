"""DSPy integration module for prompt optimization.

This module provides DSPy-based prompt optimization capabilities for SDS agents,
including offline optimization, online learning, and automatic rollback.

Public API:
    - DSPyConfig: Configuration dataclass for DSPy settings
    - PromptOptimizer: Orchestrates DSPy optimization workflows
    - DSPyFeedbackCollector: Collects online learning feedback
    - PerformanceMonitor: Monitors performance and triggers rollback
    - MetricsAggregator: Analyzes trajectory data
"""

from app_operator.dspy_integration.config import (
    DSPyConfig,
    DSPyOptimizationConfig,
    DSPyAutoRollbackConfig,
)

__all__ = [
    "DSPyConfig",
    "DSPyOptimizationConfig",
    "DSPyAutoRollbackConfig",
]
