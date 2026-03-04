"""Configuration for fault injection.

Note: The canonical definition of FaultInjectionConfig lives in
app_operator.config to keep the foundational config module free of
application-layer imports.  This module re-exports it so that existing
callers of ``app_operator.fault_injection.config`` continue to work unchanged.
"""

from app_operator.config import FaultInjectionConfig

__all__ = ["FaultInjectionConfig"]
