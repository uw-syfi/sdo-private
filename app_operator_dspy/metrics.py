"""Evaluation metrics for DSPy module optimization."""

from typing import Any

# Penalty per additional attempt beyond the first
_ATTEMPT_PENALTY = 0.2


def deployment_metric(example: Any, prediction: Any, trace: Any = None) -> float:
    """Score a deployment prediction.

    Returns 1.0 for a first-attempt success, with a 0.2 penalty per
    additional attempt. Returns 0.0 for failures.

    Args:
        example: DSPy example (unused, required by DSPy metric protocol).
        prediction: Prediction with ``success`` (bool) and ``attempts`` (int).
        trace: Optional trace (unused).

    Returns:
        Score between 0.0 and 1.0.
    """
    if prediction is None or not getattr(prediction, "success", False):
        return 0.0

    attempts = getattr(prediction, "attempts", 1)
    return max(0.1, 1.0 - (attempts - 1) * _ATTEMPT_PENALTY)


def health_status_metric(example: Any, prediction: Any, trace: Any = None) -> float:
    """Score a health check analysis prediction.

    Returns 1.0 for ``healthy``, 0.5 for ``degraded``, 0.0 for
    ``unhealthy`` or missing status.

    Args:
        example: DSPy example (unused).
        prediction: Prediction with ``status`` field.
        trace: Optional trace (unused).

    Returns:
        Score between 0.0 and 1.0.
    """
    status = getattr(prediction, "status", None)
    return {"healthy": 1.0, "degraded": 0.5, "unhealthy": 0.0}.get(status, 0.0)
