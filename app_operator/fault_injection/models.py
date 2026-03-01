"""Data models for fault injection.

Defines fault categories, severities, fault descriptors, and injection results.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class FaultCategory(str, Enum):
    """Categories of faults inspired by SREGym taxonomy."""

    MISCONFIGURATION = "misconfiguration"
    SECURITY = "security"
    METASTABLE = "metastable"
    CORRELATED = "correlated"
    INFRASTRUCTURE = "infrastructure"


class FaultSeverity(str, Enum):
    """Severity levels for faults."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass(frozen=True)
class Fault:
    """Descriptor for a single fault type.

    Attributes:
        fault_id: Unique identifier (e.g., "MISC-001").
        name: Human-readable name (e.g., "wrong_port_mapping").
        category: Fault category.
        severity: Fault severity.
        description: What the fault does and how it manifests.
        applicable_services: Service name patterns this fault can target.
            Empty list means applicable to any service.
        platform: Target platform ("compose", "k8s", or "any").
    """

    fault_id: str
    name: str
    category: FaultCategory
    severity: FaultSeverity
    description: str
    applicable_services: tuple[str, ...] = ()
    platform: str = "compose"


@dataclass
class FaultResult:
    """Result of injecting a single fault.

    Attributes:
        fault: The fault that was injected.
        target_service: The service that was modified.
        modified_fields: Description of what was changed.
        success: Whether the injection succeeded.
        error_message: Error description if injection failed.
    """

    fault: Fault
    target_service: str
    modified_fields: dict[str, Any] = field(default_factory=dict)
    success: bool = True
    error_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to a JSON-serializable dictionary."""
        return {
            "fault_id": self.fault.fault_id,
            "fault_name": self.fault.name,
            "category": self.fault.category.value,
            "severity": self.fault.severity.value,
            "target_service": self.target_service,
            "modified_fields": self.modified_fields,
            "success": self.success,
            "error_message": self.error_message,
        }
