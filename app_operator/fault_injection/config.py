"""Configuration for fault injection.

Follows the same validation patterns as app_operator/dspy_integration/config.py.
"""

from dataclasses import dataclass, field

from app_operator.fault_injection.models import FaultCategory, FaultSeverity
from app_operator.validation import validate_field, validate_type


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

    VALID_PLATFORMS = {"compose", "k8s"}

    enabled: bool = False
    num_faults: int = 2
    categories: list[str] = field(default_factory=list)
    severities: list[str] = field(default_factory=list)
    exclude_faults: list[str] = field(default_factory=list)
    seed: int | None = None
    backup_compose: bool = True
    platform: str = "compose"

    def __post_init__(self):
        """Validate configuration after initialization."""
        validate_field(self.enabled, "enabled", bool)

        validate_type(self.num_faults, "num_faults", int)
        if self.num_faults < 1 or self.num_faults > 5:
            raise ValueError(
                f"num_faults must be between 1 and 5, got {self.num_faults}"
            )

        validate_type(self.categories, "categories", list)
        valid_categories = {c.value for c in FaultCategory}
        for cat in self.categories:
            if cat not in valid_categories:
                raise ValueError(
                    f"Invalid category '{cat}'. "
                    f"Valid categories: {sorted(valid_categories)}"
                )

        validate_type(self.severities, "severities", list)
        valid_severities = {s.value for s in FaultSeverity}
        for sev in self.severities:
            if sev not in valid_severities:
                raise ValueError(
                    f"Invalid severity '{sev}'. "
                    f"Valid severities: {sorted(valid_severities)}"
                )

        validate_field(self.exclude_faults, "exclude_faults", list)
        validate_field(self.seed, "seed", int, nullable=True)
        validate_field(self.backup_compose, "backup_compose", bool)
        validate_field(
            self.platform, "platform", str, valid_values=self.VALID_PLATFORMS
        )
