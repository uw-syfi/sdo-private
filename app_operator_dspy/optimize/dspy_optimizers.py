"""DSPy built-in optimizer wrappers for the SDS operator.

Wraps BootstrapFewShot, MIPROv2, and COPRO to optimize specific
signatures (GenerateDeployScript, RepairDeploymentError) while
freezing the rest of the operator.
"""

import json
import os
import shutil
import tempfile
import time
from dataclasses import dataclass

import dspy

from app_operator_dspy.logger import get_logger
from app_operator_dspy.operator import DSPyOperator
from app_operator_dspy.optimize.metric import deployment_metric
from app_operator_dspy.token_tracker import clear_history, get_token_usage
from app_operator_dspy.tools.context import set_task_repo
from app_operator_dspy.tools.repo import init_submodules

log = get_logger("optimize")

# Predictor name prefixes for the two target signatures.
TARGET_PREFIXES = (
    "deployer.gen_deploy.",
    "deployer.repair_agent.",
)


@dataclass
class OptimizeResult:
    optimizer_name: str
    score_before: float
    score_after: float
    output_path: str


class OperatorWrapper(dspy.Module):
    """Thin wrapper that runs the operator on a single app and returns
    a Prediction enriched with time and token metrics.

    DSPy optimizers call ``module(example)`` during compile, so the
    wrapper translates a dspy.Example (repo_path) into a full operator
    run and attaches efficiency metrics to the result.
    """

    def __init__(
        self,
        operator: DSPyOperator | None = None,
        max_attempts: int = 5,
        monitor_checks: int = 2,
        deploy_timeout: int = 300,
        health_check_timeout: int = 300,
    ):
        super().__init__()
        self.operator = operator or DSPyOperator()
        self.max_attempts = max_attempts
        self.monitor_checks = monitor_checks
        self.deploy_timeout = deploy_timeout
        self.health_check_timeout = health_check_timeout

    def forward(self, repo_path: str) -> dspy.Prediction:
        lm = dspy.settings.lm

        # Copy app to temp dir
        app_name = os.path.basename(repo_path)
        work_dir = tempfile.mkdtemp(prefix="sds_opt_")
        work_repo = os.path.join(work_dir, app_name)
        shutil.copytree(repo_path, work_repo)
        init_submodules(repo_path, work_repo)

        clear_history(lm)
        set_task_repo(work_repo)

        start = time.time()
        try:
            result = self.operator(
                repo_path=work_repo,
                max_deploy_attempts=self.max_attempts,
                monitor_checks=self.monitor_checks,
                deploy_timeout=self.deploy_timeout,
                health_check_timeout=self.health_check_timeout,
            )
            elapsed = time.time() - start
            tokens = get_token_usage(lm)

            return dspy.Prediction(
                success=result.success,
                phase=result.phase,
                attempts=result.attempts,
                statuses=getattr(result, "statuses", []),
                error=getattr(result, "error", None),
                time_seconds=round(elapsed, 1),
                total_tokens=tokens["total_tokens"],
            )
        except Exception as e:
            elapsed = time.time() - start
            tokens = get_token_usage(lm)
            return dspy.Prediction(
                success=False,
                phase="exception",
                attempts=0,
                statuses=[],
                error=f"{type(e).__name__}: {e}",
                time_seconds=round(elapsed, 1),
                total_tokens=tokens["total_tokens"],
            )
        finally:
            _docker_cleanup(work_repo)
            shutil.rmtree(work_dir, ignore_errors=True)

    @property
    def named_predictors_list(self):
        """Expose inner operator's predictors so optimizers can see them."""
        return self.operator.named_predictors()

    def predictors(self):
        return self.operator.predictors()

    def named_predictors(self):
        return self.operator.named_predictors()


def _is_target_predictor(name: str) -> bool:
    return any(name.startswith(p) for p in TARGET_PREFIXES)


def compile_with_optimizer(
    optimizer_name: str,
    trainset: list[dspy.Example],
    valset: list[dspy.Example] | None = None,
    output_dir: str = "results/optimized",
    **kwargs,
) -> OptimizeResult:
    """Compile the operator using a DSPy optimizer.

    Args:
        optimizer_name: One of 'bootstrap', 'mipro', 'copro'.
        trainset: Training examples (dspy.Example with repo_path).
        valset: Validation examples (optional, used by MIPROv2).
        output_dir: Where to save the optimized module state.
        **kwargs: Extra args passed to the optimizer constructor.

    Returns:
        OptimizeResult with before/after scores and output path.
    """
    log.info("initializing {} optimizer", optimizer_name)

    wrapper = OperatorWrapper()

    # Evaluate baseline score on trainset
    log.info("evaluating baseline on {} training apps...", len(trainset))
    baseline_scores = []
    for ex in trainset:
        pred = wrapper(repo_path=ex.repo_path)
        score = deployment_metric(ex, pred)
        log.info("  {} — {:.3f}", os.path.basename(ex.repo_path), score)
        baseline_scores.append(score)
    score_before = sum(baseline_scores) / len(baseline_scores) if baseline_scores else 0.0
    log.info("baseline score: {:.3f}", score_before)

    # Create optimizer
    optimizer = _make_optimizer(optimizer_name, **kwargs)

    # Compile — this runs the operator on training examples
    log.info("compiling with {} ...", optimizer_name)
    compile_kwargs = {"trainset": trainset}
    if valset and optimizer_name == "mipro":
        compile_kwargs["valset"] = valset
        compile_kwargs["num_trials"] = kwargs.get("num_trials", 15)
        compile_kwargs["minibatch_size"] = min(len(valset), kwargs.get("minibatch_size", 4))
        compile_kwargs["minibatch_full_eval_steps"] = kwargs.get("minibatch_full_eval_steps", 3)
    if optimizer_name == "copro":
        compile_kwargs["eval_kwargs"] = {}

    compiled = optimizer.compile(wrapper, **compile_kwargs)

    # Evaluate optimized score
    log.info("evaluating optimized model on {} training apps...", len(trainset))
    opt_scores = []
    for ex in trainset:
        pred = compiled(repo_path=ex.repo_path)
        score = deployment_metric(ex, pred)
        log.info("  {} — {:.3f}", os.path.basename(ex.repo_path), score)
        opt_scores.append(score)
    score_after = sum(opt_scores) / len(opt_scores) if opt_scores else 0.0
    log.info("optimized score: {:.3f}", score_after)

    # Save
    os.makedirs(output_dir, exist_ok=True)
    state_path = os.path.join(output_dir, f"{optimizer_name}_optimized.json")
    compiled.save(state_path)
    log.info("saved optimized state to {}", state_path)

    # Save summary
    summary = {
        "optimizer": optimizer_name,
        "score_before": round(score_before, 4),
        "score_after": round(score_after, 4),
        "improvement": round(score_after - score_before, 4),
        "train_apps": [os.path.basename(ex.repo_path) for ex in trainset],
        "kwargs": {k: str(v) for k, v in kwargs.items()},
    }
    with open(os.path.join(output_dir, f"{optimizer_name}_summary.json"), "w") as f:
        json.dump(summary, f, indent=2)

    return OptimizeResult(
        optimizer_name=optimizer_name,
        score_before=score_before,
        score_after=score_after,
        output_path=state_path,
    )


def _make_optimizer(name: str, **kwargs):
    if name == "bootstrap":
        return dspy.BootstrapFewShot(
            metric=deployment_metric,
            max_bootstrapped_demos=kwargs.get("max_bootstrapped_demos", 2),
            max_labeled_demos=kwargs.get("max_labeled_demos", 2),
            max_rounds=kwargs.get("max_rounds", 1),
        )
    elif name == "mipro":
        return dspy.MIPROv2(
            metric=deployment_metric,
            auto=None,
            num_candidates=kwargs.get("num_candidates", 3),
            num_threads=kwargs.get("num_threads", 1),
            verbose=kwargs.get("verbose", True),
        )
    elif name == "copro":
        return dspy.COPRO(
            metric=deployment_metric,
            breadth=kwargs.get("breadth", 5),
            depth=kwargs.get("depth", 2),
        )
    else:
        raise ValueError(f"Unknown optimizer: {name}. Use 'bootstrap', 'mipro', or 'copro'.")


def _docker_cleanup(repo_path: str) -> None:
    """Best-effort cleanup of containers from this app's deployment."""
    import subprocess
    project = os.path.basename(repo_path).lower()
    try:
        subprocess.run(
            ["docker", "compose", "--project-name", project, "down", "-v", "--remove-orphans"],
            cwd=repo_path, capture_output=True, timeout=60,
        )
    except Exception:
        pass
    try:
        subprocess.run(["docker", "container", "prune", "-f"], capture_output=True, timeout=30)
        subprocess.run(["docker", "network", "prune", "-f"], capture_output=True, timeout=30)
    except Exception:
        pass
