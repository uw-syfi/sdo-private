"""Fault report for trajectory metadata bridge.

Converts fault injection results into metadata dictionaries suitable
for embedding in trajectory JSON files.
"""

from app_operator.fault_injection.models import FaultResult
from app_operator.types import FaultInjectionMetadata


class FaultReport:
    """Converts fault injection results to trajectory-compatible metadata."""

    @staticmethod
    def to_trajectory_metadata(results: list[FaultResult]) -> FaultInjectionMetadata:
        """Convert fault results to a metadata dict for trajectory JSON.

        Args:
            results: List of fault injection results.

        Returns:
            Dict suitable for trajectory["metadata"]["fault_injection"].
        """
        successful = [r for r in results if r.success]
        failed = [r for r in results if not r.success]

        return {
            "enabled": True,
            "num_faults_requested": len(results),
            "num_faults_injected": len(successful),
            "faults": [r.to_dict() for r in successful],
            "failed_injections": [r.to_dict() for r in failed],
            "fault_ids": [r.fault.fault_id for r in successful],
            "categories": sorted({r.fault.category.value for r in successful}),
            "severities": sorted({r.fault.severity.value for r in successful}),
        }
