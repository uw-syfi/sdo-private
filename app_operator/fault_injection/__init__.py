"""Fault injection module for SDS training data diversification.

Provides Docker Compose fault injection inspired by SREGym's fault taxonomy
to generate diverse failure scenarios.
"""

from app_operator.fault_injection.config import FaultInjectionConfig
from app_operator.fault_injection.injector import FaultInjectionOrchestrator
from app_operator.fault_injection.models import (
    Fault,
    FaultCategory,
    FaultResult,
    FaultSeverity,
)
from app_operator.fault_injection.registry import FaultRegistry
from app_operator.fault_injection.reporter import FaultReport

__all__ = [
    "Fault",
    "FaultCategory",
    "FaultInjectionConfig",
    "FaultInjectionOrchestrator",
    "FaultRegistry",
    "FaultReport",
    "FaultResult",
    "FaultSeverity",
]
