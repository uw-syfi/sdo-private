"""Train/eval dataset management for prompt optimization.

Provides standardized app splits and creates dspy.Example objects
for use with DSPy optimizers and GEPA.
"""

import os

import dspy

# Default splits chosen for tech-stack diversity in both sets.
# Train includes baseline pass + fail apps for learning from both.
# Eval includes unseen apps to verify generalization.
TRAIN_APPS = [
    "apps/deathstarbench/hotelReservation",
    "apps/deathstarbench/mediaMicroservices",
    "apps/pitstop",
    "apps/sockshop",
    "apps/teastore",
    "apps/fleetcast",
]

EVAL_APPS = [
    "apps/deathstarbench/socialNetwork",
    "apps/deathstarbench/daprApps_v1",
    "apps/onlineboutique",
    "apps/teastore-saga",
]


def make_examples(
    app_paths: list[str],
    repo_root: str | None = None,
) -> list[dspy.Example]:
    """Create dspy.Example objects from app paths.

    Args:
        app_paths: Relative or absolute paths to app directories.
        repo_root: If provided, resolve relative paths against this root.

    Returns:
        List of dspy.Example with repo_path as input field.
    """
    examples = []
    for path in app_paths:
        if repo_root and not os.path.isabs(path):
            path = os.path.join(repo_root, path)
        path = os.path.abspath(path)
        if not os.path.isdir(path):
            continue
        ex = dspy.Example(repo_path=path).with_inputs("repo_path")
        examples.append(ex)
    return examples


def get_train_examples(repo_root: str | None = None) -> list[dspy.Example]:
    """Return training set examples."""
    return make_examples(TRAIN_APPS, repo_root)


def get_eval_examples(repo_root: str | None = None) -> list[dspy.Example]:
    """Return evaluation set examples."""
    return make_examples(EVAL_APPS, repo_root)
