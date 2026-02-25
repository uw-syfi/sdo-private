"""Fault registry for filtering and selecting faults.

Indexes faults by ID, category, severity, and platform for
efficient lookup and random selection.
"""

import random
from typing import Dict, List, Optional

from app_operator.fault_injection.config import FaultInjectionConfig
from app_operator.fault_injection.models import Fault


class FaultRegistry:
    """Registry of available faults with filtering and selection.

    Indexes faults and provides methods to filter by category, severity,
    platform, and exclusion lists, then sample N faults.
    """

    def __init__(self, faults: List[Fault]):
        self._faults: List[Fault] = list(faults)
        self._by_id: Dict[str, Fault] = {f.fault_id: f for f in faults}

    @property
    def all_faults(self) -> List[Fault]:
        return list(self._faults)

    def get(self, fault_id: str) -> Optional[Fault]:
        return self._by_id.get(fault_id)

    def filter(
        self,
        categories: Optional[List[str]] = None,
        severities: Optional[List[str]] = None,
        platform: Optional[str] = None,
        exclude: Optional[List[str]] = None,
    ) -> List[Fault]:
        """Filter faults by criteria.

        Args:
            categories: Include only these categories (None = all).
            severities: Include only these severities (None = all).
            platform: Include only faults for this platform (None = all).
            exclude: Fault IDs to exclude.

        Returns:
            Filtered list of faults.
        """
        result = self._faults

        if categories:
            cat_set = set(categories)
            result = [f for f in result if f.category.value in cat_set]

        if severities:
            sev_set = set(severities)
            result = [f for f in result if f.severity.value in sev_set]

        if platform:
            result = [f for f in result if f.platform in (platform, "any")]

        if exclude:
            excl_set = set(exclude)
            result = [f for f in result if f.fault_id not in excl_set]

        return result

    def select(
        self,
        n: int,
        config: Optional[FaultInjectionConfig] = None,
        rng: Optional[random.Random] = None,
    ) -> List[Fault]:
        """Select N faults based on config filters.

        Args:
            n: Number of faults to select.
            config: Configuration with filter criteria.
            rng: Random number generator for reproducibility.

        Returns:
            List of selected faults (up to n, may be fewer if not enough match).
        """
        if rng is None:
            rng = random.Random()

        if config is not None:
            candidates = self.filter(
                categories=config.categories or None,
                severities=config.severities or None,
                platform=config.platform,
                exclude=config.exclude_faults or None,
            )
        else:
            candidates = list(self._faults)

        if not candidates:
            return []

        k = min(n, len(candidates))
        return rng.sample(candidates, k)
