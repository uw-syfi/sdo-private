"""Evaluator that runs the operator pipeline and scores candidates."""

import os
import shutil
import tempfile
import time

import dspy

from app_operator_dspy.logger import get_logger
from app_operator_dspy.operator import DSPyOperator
from app_operator_dspy.optimize.gepa.adapter import OPTIMIZABLE_SIGNATURES, apply_candidate
from app_operator_dspy.optimize.gepa.candidate import PromptCandidate
from app_operator_dspy.optimize.gepa.reflector import ExecutionTrace
from app_operator_dspy.optimize.metric import deployment_metric
from app_operator_dspy.token_tracker import clear_history, get_token_usage
from app_operator_dspy.tools.context import set_task_repo
from app_operator_dspy.tools.repo import init_submodules

log = get_logger("gepa.evaluator")


class SignatureEvaluator:
    """Evaluates a prompt candidate by running the operator on apps."""

    def __init__(
        self,
        max_attempts: int = 5,
        monitor_checks: int = 2,
        deploy_timeout: int = 300,
        health_check_timeout: int = 300,
    ):
        self.max_attempts = max_attempts
        self.monitor_checks = monitor_checks
        self.deploy_timeout = deploy_timeout
        self.health_check_timeout = health_check_timeout

    def evaluate(
        self,
        candidate: PromptCandidate,
        app_paths: list[str],
    ) -> tuple[float, list[ExecutionTrace]]:
        """Run operator with candidate's instruction on a set of apps.

        Args:
            candidate: The prompt candidate to evaluate.
            app_paths: Paths to application directories.

        Returns:
            (overall_score, traces) — average score and per-app traces.
        """
        # Apply candidate instruction to the signature (skip for joint candidates
        # where the caller already applied instructions via _apply_joint).
        if candidate.signature_name in OPTIMIZABLE_SIGNATURES:
            apply_candidate(candidate)

        traces = []
        scores = []

        for app_path in app_paths:
            app_name = os.path.basename(app_path)
            log.info("  evaluating {} on {}...", candidate.id, app_name)

            trace = self._run_single(app_path)
            traces.append(trace)
            scores.append(trace.score)

            status = "PASS" if trace.success else "FAIL"
            log.info("    {} — score={:.3f}", status, trace.score)

        overall = sum(scores) / len(scores) if scores else 0.0
        candidate.overall_score = overall
        candidate.scores = {
            "success_rate": sum(1 for t in traces if t.success) / len(traces) if traces else 0,
            "mean_score": overall,
        }

        return overall, traces

    def _run_single(self, app_path: str) -> ExecutionTrace:
        """Run the operator on a single app and return a trace."""
        app_name = os.path.basename(app_path)
        lm = dspy.settings.lm

        work_dir = tempfile.mkdtemp(prefix="sds_gepa_")
        work_repo = os.path.join(work_dir, app_name)
        shutil.copytree(app_path, work_repo)
        init_submodules(app_path, work_repo)

        clear_history(lm)
        operator = DSPyOperator()
        set_task_repo(work_repo)

        start = time.time()
        try:
            result = operator(
                repo_path=work_repo,
                max_deploy_attempts=self.max_attempts,
                monitor_checks=self.monitor_checks,
                deploy_timeout=self.deploy_timeout,
                health_check_timeout=self.health_check_timeout,
            )
            elapsed = time.time() - start
            tokens = get_token_usage(lm)

            pred = dspy.Prediction(
                success=result.success,
                phase=result.phase,
                attempts=result.attempts,
                statuses=getattr(result, "statuses", []),
                error=getattr(result, "error", None),
                time_seconds=round(elapsed, 1),
                total_tokens=tokens["total_tokens"],
            )
            score = deployment_metric(dspy.Example(repo_path=app_path), pred)

            return ExecutionTrace(
                app_name=app_name,
                success=result.success,
                phase=result.phase,
                attempts=result.attempts,
                error=getattr(result, "error", None),
                score=score,
            )
        except Exception as e:
            elapsed = time.time() - start
            return ExecutionTrace(
                app_name=app_name,
                success=False,
                phase="exception",
                attempts=0,
                error=f"{type(e).__name__}: {e}",
                score=0.0,
            )
        finally:
            self._cleanup(work_repo)
            shutil.rmtree(work_dir, ignore_errors=True)

    def _cleanup(self, repo_path: str) -> None:
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
