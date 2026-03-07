"""Optimization harness for DSPy modules."""

import dspy

OPTIMIZERS = {
    "BootstrapFewShot": dspy.BootstrapFewShot,
    "MIPROv2": dspy.MIPROv2,
}


def optimize(
    module: dspy.Module,
    trainset: list,
    metric,
    optimizer: str = "BootstrapFewShot",
    **kwargs,
) -> dspy.Module:
    """Compile a DSPy module with the chosen optimizer.

    Args:
        module: The DSPy module to optimize.
        trainset: List of ``dspy.Example`` training instances.
        metric: Callable ``(example, prediction, trace) -> float``.
        optimizer: Name of the optimizer (see ``OPTIMIZERS``).
        **kwargs: Additional keyword arguments passed to the optimizer.

    Returns:
        The optimized (compiled) module.

    Raises:
        ValueError: If the optimizer name is not recognized.
    """
    if optimizer not in OPTIMIZERS:
        raise ValueError(f"Unknown optimizer {optimizer!r}. Choose from: {sorted(OPTIMIZERS)}")

    opt = OPTIMIZERS[optimizer](metric=metric, **kwargs)
    return opt.compile(module, trainset=trainset)
