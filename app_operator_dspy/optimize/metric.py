"""Composite deployment metric with partial credit for prompt optimization.

Scores a deployment run on a 0.0–1.0 scale. Failed deployments get
partial credit based on how far they progressed, giving the optimizer
a gradient signal even when the final result is failure.
"""

import re

import dspy

# --- Phase progression weights ---
# Each phase reached earns points, even on failure.
_PHASE_SCORES = {
    "exception": 0.0,       # crashed before anything ran
    "code_analysis": 0.05,  # analysis ran but deployer never started
    "deployment": 0.10,     # deployer ran (scripts generated)
    "monitoring": 0.50,     # deploy succeeded, reached monitoring
}

# --- Deployment progress milestones (parsed from error output) ---
_MILESTONES = [
    (r"(?:Creating|Created)\s+\w+", 0.05),                      # containers created
    (r"(?:Starting|Started)\s+\w+", 0.05),                      # containers starting
    (r"Container\s+\S+\s+(?:Running|Started|Created)", 0.05),   # compose v2 progress
    (r"Running \d+/\d+", 0.03),                                 # compose build progress
    (r"(?:Successfully built|DONE|exporting to image)", 0.03),   # build completed
    (r"Network\s+\S+\s+Created", 0.02),                         # network created
]
_MAX_MILESTONE_BONUS = 0.15  # cap milestone bonuses

# --- Success efficiency weights ---
_MAX_ATTEMPTS = 5
_REF_TIME_SECONDS = 1800.0
_REF_TOKENS = 1_500_000


def deployment_metric(
    example: dspy.Example,
    pred: dspy.Prediction,
    trace=None,
) -> float:
    """Score a deployment prediction with partial credit.

    Successful deployments score 0.50–1.00 based on efficiency.
    Failed deployments score 0.00–0.40 based on progress made.

    Args:
        example: Input example (contains repo_path at minimum).
        pred: Operator result with success, phase, attempts, error, and
              optionally time_seconds and total_tokens.
        trace: DSPy trace (unused, required by optimizer contract).

    Returns:
        Score in [0.0, 1.0].
    """
    success = getattr(pred, "success", False)
    phase = getattr(pred, "phase", "exception")
    attempts = getattr(pred, "attempts", 0)
    error = getattr(pred, "error", "") or ""

    if success:
        return _score_success(pred)

    return _score_failure(phase, attempts, error)


def _score_success(pred: dspy.Prediction) -> float:
    """Score a successful deployment: 0.50–1.00."""
    score = 0.50

    # Attempt efficiency: 1 attempt → +0.20, 5 attempts → +0.00
    attempts = getattr(pred, "attempts", _MAX_ATTEMPTS)
    if _MAX_ATTEMPTS > 1:
        attempt_bonus = max(0.0, 1.0 - (attempts - 1) / (_MAX_ATTEMPTS - 1))
        score += 0.20 * attempt_bonus

    # Monitoring health: all healthy → +0.15, mixed → +0.08
    statuses = getattr(pred, "statuses", [])
    if statuses:
        healthy_ratio = sum(1 for s in statuses if s == "healthy") / len(statuses)
        score += 0.15 * healthy_ratio

    # Time efficiency
    time_seconds = getattr(pred, "time_seconds", None)
    if time_seconds is not None:
        time_bonus = max(0.0, 1.0 - time_seconds / _REF_TIME_SECONDS)
        score += 0.08 * time_bonus

    # Token efficiency
    total_tokens = getattr(pred, "total_tokens", None)
    if total_tokens is not None:
        token_bonus = max(0.0, 1.0 - total_tokens / _REF_TOKENS)
        score += 0.07 * token_bonus

    return round(min(score, 1.0), 4)


def _score_failure(phase: str, attempts: int, error: str) -> float:
    """Score a failed deployment based on progress: 0.00–0.40."""
    # Base score from phase reached
    score = _PHASE_SCORES.get(phase, 0.0)

    # Attempt progress: more attempts = got further through the repair loop
    if attempts > 0 and phase == "deployment":
        score += min(0.10, attempts * 0.02)

    # Parse error output for deployment progress milestones
    milestone_bonus = 0.0
    for pattern, bonus in _MILESTONES:
        if re.search(pattern, error, re.IGNORECASE):
            milestone_bonus += bonus
    score += min(milestone_bonus, _MAX_MILESTONE_BONUS)

    return round(min(score, 0.40), 4)
