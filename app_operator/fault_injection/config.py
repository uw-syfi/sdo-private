"""Configuration for fault injection.

Follows the same validation patterns as app_operator/dspy_integration/config.py.
"""

from dataclasses import dataclass, field
from typing import List, Optional

from app_operator.fault_injection.models import FaultCategory, FaultSeverity


@dataclass
class FaultInjectionConfig:
    """Configuration for fault injection in training data collection.

    Attributes:
        enabled: Whether fault injection is active.
        num_faults: Number of faults to inject per run (1-5).
        categories: Which fault categories to include. Empty means all.
        severities: Which severity levels to include. Empty means all.
        exclude_faults: Fault IDs to exclude from selection.
        seed: Random seed for reproducible fault selection.
        backup_compose: Whether to back up compose files before injection.
        platform: Target platform for fault selection ("compose" or "k8s").
    """

    enabled: bool = False
    num_faults: int = 2
    categories: List[str] = field(default_factory=list)
    severities: List[str] = field(default_factory=list)
    exclude_faults: List[str] = field(default_factory=list)
    seed: Optional[int] = None
    backup_compose: bool = True
    platform: str = "compose"

    def __post_init__(self):
        """Validate configuration after initialization."""
        if not isinstance(self.enabled, bool):
            raise TypeError(
                f"enabled must be bool, got {type(self.enabled).__name__}"
            )

        if not isinstance(self.num_faults, int):
            raise TypeError(
                f"num_faults must be int, got {type(self.num_faults).__name__}"
            )
        if self.num_faults < 1 or self.num_faults > 5:
            raise ValueError(
                f"num_faults must be between 1 and 5, got {self.num_faults}"
            )

        if not isinstance(self.categories, list):
            raise TypeError(
                f"categories must be list, got {type(self.categories).__name__}"
            )
        valid_categories = {c.value for c in FaultCategory}
        for cat in self.categories:
            if cat not in valid_categories:
                raise ValueError(
                    f"Invalid category '{cat}'. "
                    f"Valid categories: {sorted(valid_categories)}"
                )

        if not isinstance(self.severities, list):
            raise TypeError(
                f"severities must be list, got {type(self.severities).__name__}"
            )
        valid_severities = {s.value for s in FaultSeverity}
        for sev in self.severities:
            if sev not in valid_severities:
                raise ValueError(
                    f"Invalid severity '{sev}'. "
                    f"Valid severities: {sorted(valid_severities)}"
                )

        if not isinstance(self.exclude_faults, list):
            raise TypeError(
                f"exclude_faults must be list, got {type(self.exclude_faults).__name__}"
            )

        if self.seed is not None and not isinstance(self.seed, int):
            raise TypeError(
                f"seed must be int or None, got {type(self.seed).__name__}"
            )

        if not isinstance(self.backup_compose, bool):
            raise TypeError(
                f"backup_compose must be bool, got {type(self.backup_compose).__name__}"
            )

        valid_platforms = {"compose", "k8s"}
        if not isinstance(self.platform, str):
            raise TypeError(
                f"platform must be str, got {type(self.platform).__name__}"
            )
        if self.platform not in valid_platforms:
            raise ValueError(
                f"Invalid platform '{self.platform}'. "
                f"Valid platforms: {sorted(valid_platforms)}"
            )
