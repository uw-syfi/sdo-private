"""Eval-execute optimizer for SDS prompt optimization.

Implements the eval-execute loop:
1. Generate ``n_candidates`` non-elite instruction variants for each prompt.
2. Evaluate those variants plus the unchanged elite baseline on training apps.
3. Keep the best-scoring candidate based on real deployment outcomes.
"""

import hashlib
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import litellm

from app_operator.core import logger
from app_operator.dspy_integration.config import DSPyConfig
from app_operator.dspy_integration.signatures import SIGNATURES, get_signature
from app_operator.experiment_naming import normalize_experiment_token
from app_operator.rate_limit_handler import (
    detect_rate_limit_error,
    exponential_backoff,
)
from app_operator.run_classifier import RunClassification, classify_run
from app_operator.trajectory import extract_rlm_statistics_from_trajectory

_TRAIN_RUN_RE = re.compile(r"^iter(\d+)_c\d+_(.+)$")
_VAL_RUN_RE = re.compile(r"^(.+)_iter(\d+)_val$")
_CANDIDATE_TAG_RE = re.compile(r"^eval_\d+_c\d+$")
_ERROR_LINE_MARKERS = (
    "error",
    "failed",
    "exception",
    "traceback",
    "no such file",
    "not found",
    "permission denied",
    "timeout",
    "timed out",
    "connection refused",
)
_SUCCESS_STATUS_MARKERS = ("success", "succeeded", "completed", "ok", "healthy")
_FAILURE_STATUS_MARKERS = ("fail", "error", "timeout", "crash", "oom", "unhealthy")
_TRAJECTORY_SUMMARY_TARGET_CHARS = 1800
_SUMMARY_SECTION_LIMIT = 5
_SUMMARY_ITEMS_PER_SECTION = 2
_SUMMARY_ITEM_MAX_CHARS = 150
_HISTORICAL_POPULATION_SIZE = 6
_DEPLOY_SCRIPT_MARKERS = (
    "docker compose",
    "docker-compose",
    "kubectl",
    "helm",
    "npm",
    "python",
)
_HEALTH_CHECK_MARKERS = (
    "curl",
    "wget",
    "http",
    "health",
    "status",
    "nc ",
    "pg_isready",
)
_CLASSIFICATION_SIGNAL_WEIGHT = 0.35
_CLASSIFICATION_SCORE_BY_LABEL: dict[str, float] = {
    "true_success": 1.0,
    # Downscored from 0.85: ambiguous recovery — could be a legitimate fix or a
    # weakened health check.  Kept above 0.5 only when no policy regression is detected.
    "recovered_success": 0.5,
    "telemetry_inconsistent": 0.45,
    # health_policy_regression: agent "fixed" the health check instead of the app.
    # Treat as selection-fatal — same as false_positive.
    "health_policy_regression": 0.0,
    "false_positive": 0.0,
    "true_failure": 0.0,
}
_CLASSIFICATION_RELIABILITY_LABELS = frozenset({"false_positive", "telemetry_inconsistent", "health_policy_regression"})

# Instruction patterns that indicate a candidate prompt encodes check-removal
# heuristics.  Any generated variant that matches one of these (case-insensitive)
# is discarded before evaluation so the optimizer cannot learn to delete probes.
_FORBIDDEN_INSTRUCTION_PATTERNS: tuple[str, ...] = (
    "remove the check",
    "remove the health check",
    "remove the probe",
    "delete the check",
    "delete the probe",
    "comment out the check",
    "comment out or delete",
    "skip the probe",
    "skip the check",
    "disable the check",
    "disable the probe",
)


@dataclass
class _TrajectoryEvidence:
    """Compressed evidence extracted from one experiment trajectory."""

    run_name: str
    app_name: str
    run_type: str
    status: str
    attempts: int
    trajectory_insights: list[str] = field(default_factory=list)  # pyright: ignore[reportUnknownVariableType]
    error_insights: list[str] = field(default_factory=list)  # pyright: ignore[reportUnknownVariableType]
    script_insights: list[str] = field(default_factory=list)  # pyright: ignore[reportUnknownVariableType]
    repo_insights: list[str] = field(default_factory=list)  # pyright: ignore[reportUnknownVariableType]
    error_signals: list[str] = field(default_factory=list)  # pyright: ignore[reportUnknownVariableType]
    classification_label: str = ""
    classification_reasons: list[str] = field(default_factory=list)  # pyright: ignore[reportUnknownVariableType]
    weight: float = 1.0


_PROMPT_PHASE_METRICS: dict[str, tuple[str, ...]] = {
    "deployer_generate_script": ("deploy_script_quality",),
    "deployer_generate_deploy_script": ("deploy_script_quality",),
    "deployer_generate_health_check": ("health_check_quality",),
    "deployer_fix_error": ("error_recovery_quality", "run_classification_quality"),
    "rlm_deployer_fix_error": ("error_recovery_quality", "run_classification_quality"),
    "deployer_summarize": ("deployment_summary_quality",),
}


class EvalExecuteOptimizer:
    """Optimize prompts by generating candidate instructions and evaluating them live.

    Strategy (eval-execute loop):
    1. Generate ``n_candidates`` non-elite instruction variants per prompt.
    2. Evaluate those variants plus the unchanged elite baseline when available.
    3. Score by deployment success (operator exit code 0 = success).
    4. Save the best candidate to output_dir.
    """

    def __init__(
        self,
        config: DSPyConfig,
        prompts_dir: Path,
        project_root: Path,
        n_candidates: int = 4,
        vertex_location: str | None = None,
    ):
        self.config = config
        self.prompts_dir = Path(prompts_dir)
        self.optimized_dir = self.prompts_dir / "optimized"
        self.project_root = Path(project_root)
        self.n_candidates = n_candidates
        self.vertex_location = vertex_location

    def optimize(
        self,
        prompt_names: list[str],
        train_apps: list[Path],
        work_dir: Path,
        output_dir: Path,
        iteration: int,
        current_version: str | None,
        provider: str,
        max_retries: int = 3,
        rate_limit_backoff: int = 60,
        inter_run_delay: int = 30,
        provider_override: str | None = None,
        model_override: str | None = None,
        output_prefix: str | None = None,
    ) -> dict[str, Any]:
        """Run the eval-execute optimization loop.

        Args:
            prompt_names: Names of prompts to optimize.
            train_apps: List of training app paths.
            work_dir: Working directory for temporary experiment dirs.
            output_dir: Output directory for the winning candidate.
            iteration: Current iteration number (for naming temp dirs).
            current_version: Version of prompts currently in use (or None for seeds).
            provider: LLM provider name (for rate limit detection).
            max_retries: Max retries per operator run.
            rate_limit_backoff: Extra backoff seconds when rate limited.
            inter_run_delay: Delay (seconds) between consecutive runs.
            provider_override: If set, override the ``[agent] provider`` in
                each experiment directory's ``sds.toml``.
            model_override: If set, override the ``[agent] model`` in each
                experiment directory's ``sds.toml``.
            output_prefix: If set, namespace temporary candidate directories
                under ``optimized/<prefix>/`` to avoid collisions between
                concurrent experiments.

        Returns:
            {"success": bool, ...}
        """
        invalid = [p for p in prompt_names if p not in SIGNATURES]
        if invalid:
            return {
                "success": False,
                "error": f"Invalid prompt names: {invalid}. Valid: {sorted(SIGNATURES.keys())}",
            }

        random_seed = int(os.environ.get("SDS_E2E_RANDOM_SEED", str(int(time.time() * 1000))))
        random.seed(random_seed)

        # 1. Load current instructions for all prompts
        current_instructions: dict[str, str] = {}
        for pname in prompt_names:
            current_instructions[pname] = self._load_current_instruction(pname, current_version)
            logger.info(f"[EvalExecute] Current instruction for {pname}: {current_instructions[pname][:120]}...")

        trajectory_context = self._build_recent_trajectory_context(
            work_dir=work_dir,
            iteration=iteration,
            prompt_names=prompt_names,
        )
        historical_population = self._load_historical_candidate_population(
            prompt_names=prompt_names,
            before_iteration=iteration,
            output_prefix=output_prefix,
            top_k=max(_HISTORICAL_POPULATION_SIZE, self.n_candidates),
        )
        recombination_info: dict[str, Any] = {
            "used": False,
            "reason": "",
            "parent_candidates": [],
        }
        scoped_optimized_dir = self._scoped_optimized_dir(output_prefix)
        training_fingerprint = self._compute_training_trajectories_fingerprint(work_dir, iteration)
        base_parent_candidate_ids = self._resolve_parent_candidate_ids(current_version, output_prefix)

        # Cold start bootstrap: when there is no optimized baseline and no
        # trajectory evidence yet, run a single seed candidate first to collect
        # real execution data before asking the teacher LM for variants.
        has_evidence = any(bool(context.strip()) for context in trajectory_context.values())
        cold_start_seed_only = current_version is None and not has_evidence
        if cold_start_seed_only:
            logger.info(
                "[EvalExecute] Cold start detected (no prior trajectory evidence). "
                "Running seed-only bootstrap candidate."
            )
            candidates = [dict(current_instructions)]
        else:
            logger.info(
                "[EvalExecute] Generating {} mutation candidate variants per prompt...",
                self.n_candidates,
            )
            generated_candidates = self._generate_candidates(prompt_names, current_instructions, trajectory_context)
            # Elitism: prepend the unchanged current instructions so the
            # proven-best prompt competes against all requested mutations.
            # This keeps ``n_candidates`` aligned with "number of challengers"
            # instead of silently consuming one slot for the elite baseline.
            candidates = [dict(current_instructions), *generated_candidates]
            logger.info(
                "[EvalExecute] Elitism: prepended unchanged baseline to {} generated mutations",
                len(generated_candidates),
            )
            candidates, recombination_info = self._maybe_inject_recombined_candidate(
                candidates=candidates,
                prompt_names=prompt_names,
                trajectory_context=trajectory_context,
                historical_population=historical_population,
                iteration=iteration,
            )
        logger.info(f"[EvalExecute] Generated {len(candidates)} candidates")
        if recombination_info.get("used"):
            self._append_lineage_event(
                output_prefix=output_prefix,
                event_type="candidate_recombined",
                payload={
                    "iteration": iteration,
                    "parent_candidates": recombination_info.get("parent_candidates", []),
                    "reason": recombination_info.get("reason", ""),
                },
            )

        candidate_lineage_nodes: list[dict[str, Any]] = []
        for c_idx, candidate in enumerate(candidates):
            candidate_tag = f"eval_{iteration}_c{c_idx + 1}"
            candidate_id = self._candidate_id_for_tag(candidate_tag, output_prefix)
            if cold_start_seed_only:
                operator = "seed"
            elif c_idx == 0 and len(candidates) > 1:
                operator = "elitist"
            else:
                operator = "mutation"
            parent_candidate_ids = list(base_parent_candidate_ids)
            if recombination_info.get("used") and c_idx == len(candidates) - 1:
                operator = "recombination"
                parent_candidate_ids = [
                    self._candidate_id_for_tag(str(parent), output_prefix)
                    for parent in recombination_info.get("parent_candidates", [])
                ]
                parent_candidate_ids = [parent for parent in parent_candidate_ids if parent]

            node = {
                "candidate_id": candidate_id,
                "candidate_tag": candidate_tag,
                "parent_candidate_ids": parent_candidate_ids,
                "operator": operator,
                "source_version": current_version or "",
                "prompt_instruction_sha256": {
                    pname: hashlib.sha256(str(candidate.get(pname, "")).encode("utf-8")).hexdigest()
                    for pname in prompt_names
                },
            }
            candidate_lineage_nodes.append(node)

        # 3. Evaluate each candidate by running the operator on all training apps.
        # Keep the previous winner only as historical metadata; c1 is always a
        # real rerun of the incumbent prompt in the current iteration.
        historical_best = self._load_historical_best_summary(current_version, output_prefix)
        if historical_best is not None:
            logger.info(
                "[EvalExecute] Historical best loaded from {} with score={:.4f}",
                historical_best["source_version"],
                historical_best["score"],
            )
        scores: list[float] = []
        candidate_summaries: list[dict[str, Any]] = []
        for c_idx, candidate in enumerate(candidates):
            logger.info(f"[EvalExecute] Evaluating candidate {c_idx + 1}/{len(candidates)}...")

            candidate_tag = f"eval_{iteration}_c{c_idx + 1}"
            if output_prefix:
                candidate_version = f"{output_prefix}/{candidate_tag}"
            else:
                candidate_version = candidate_tag
            self._write_candidate(candidate, candidate_version)
            candidate_module_sha = self._hash_candidate_modules(candidate_version, prompt_names)
            candidate_lineage_nodes[c_idx]["module_sha256"] = candidate_module_sha
            self._append_lineage_event(
                output_prefix=output_prefix,
                event_type="candidate_generated",
                payload={
                    "iteration": iteration,
                    "candidate_id": candidate_lineage_nodes[c_idx]["candidate_id"],
                    "candidate_tag": candidate_tag,
                    "operator": candidate_lineage_nodes[c_idx]["operator"],
                    "parent_candidate_ids": candidate_lineage_nodes[c_idx]["parent_candidate_ids"],
                    "prompt_instruction_sha256": candidate_lineage_nodes[c_idx]["prompt_instruction_sha256"],
                    "module_sha256": candidate_module_sha,
                    "source_version": current_version or "",
                    "random_seed": random_seed,
                },
            )

            successful_runs = 0
            total_runs = 0
            rlm_scores: list[float] = []
            run_outcomes: list[dict[str, Any]] = []
            for app_path in train_apps:
                outcome = self._run_candidate_on_app(
                    app_path=app_path,
                    work_dir=work_dir,
                    iteration=iteration,
                    c_idx=c_idx,
                    candidate_version=candidate_version,
                    provider=provider,
                    max_retries=max_retries,
                    rate_limit_backoff=rate_limit_backoff,
                    provider_override=provider_override,
                    model_override=model_override,
                    inter_run_delay=inter_run_delay,
                )
                total_runs += 1
                if outcome["success"]:
                    successful_runs += 1
                if outcome["rlm_score"] is not None:
                    rlm_scores.append(outcome["rlm_score"])
                run_outcomes.append(outcome)

            success_rate = successful_runs / total_runs if total_runs > 0 else 0.0
            classification_scores = [
                float(outcome["classification_score"])
                for outcome in run_outcomes
                if isinstance(outcome.get("classification_score"), int | float)
            ]
            classification_score = (
                (sum(classification_scores) / len(classification_scores)) if classification_scores else None
            )
            # Blend RLM efficiency into the score when available (10% weight)
            if rlm_scores:
                avg_rlm = sum(rlm_scores) / len(rlm_scores)
                base_score = 0.9 * success_rate + 0.1 * avg_rlm
                logger.info(f"[EvalExecute] Candidate {c_idx + 1} RLM efficiency: {avg_rlm:.2f}")
            else:
                base_score = success_rate
            if classification_score is not None:
                base_score = self._blend_base_and_classification_scores(base_score, classification_score)
                logger.info(
                    "[EvalExecute] Candidate {} classification quality: {:.2f}",
                    c_idx + 1,
                    classification_score,
                )
            phase_score = self._compute_prompt_aligned_phase_score(
                prompt_names=prompt_names,
                run_outcomes=run_outcomes,
            )
            score = self._blend_base_and_phase_scores(base_score, phase_score)
            scores.append(score)
            per_app_scores = {}
            for outcome in run_outcomes:
                app_s = 1.0 if outcome["success"] else 0.0
                rlm_s = outcome.get("rlm_score")
                if rlm_s is not None:
                    app_s = 0.9 * app_s + 0.1 * rlm_s
                cls_s = outcome.get("classification_score")
                if isinstance(cls_s, int | float):
                    app_s = self._blend_base_and_classification_scores(app_s, float(cls_s))
                per_app_scores[outcome["app_name"]] = app_s

            candidate_summaries.append(
                {
                    "candidate_index": c_idx + 1,
                    "candidate_id": candidate_lineage_nodes[c_idx]["candidate_id"],
                    "score": score,
                    "base_score": base_score,
                    "phase_score": phase_score,
                    "success_rate": success_rate,
                    "successful_runs": successful_runs,
                    "total_runs": total_runs,
                    "avg_rlm_score": (sum(rlm_scores) / len(rlm_scores) if rlm_scores else None),
                    "classification_score": classification_score,
                    "classification_counts": self._classification_counts(run_outcomes),
                    "per_app_scores": per_app_scores,
                    "run_outcomes": run_outcomes,
                }
            )
            candidate_lineage_nodes[c_idx]["score"] = score
            candidate_lineage_nodes[c_idx]["success_rate"] = success_rate
            candidate_lineage_nodes[c_idx]["classification_counts"] = self._classification_counts(run_outcomes)
            self._append_lineage_event(
                output_prefix=output_prefix,
                event_type="candidate_evaluated",
                payload={
                    "iteration": iteration,
                    "candidate_id": candidate_lineage_nodes[c_idx]["candidate_id"],
                    "score": score,
                    "success_rate": success_rate,
                    "phase_score": phase_score,
                    "classification_counts": candidate_lineage_nodes[c_idx]["classification_counts"],
                },
            )
            logger.info(
                f"[EvalExecute] Candidate {c_idx + 1} score: {score:.2f} "
                f"({successful_runs}/{total_runs} runs succeeded)"
            )
            if phase_score is not None:
                logger.info(
                    "[EvalExecute] Candidate {} phase-aligned component: {:.2f}",
                    c_idx + 1,
                    phase_score,
                )

        if not scores:
            return {"success": False, "error": "No candidates were evaluated"}

        # 4. Pick the best candidate
        best_idx, selection_info = self._select_best_candidate(
            candidates=candidates,
            prompt_names=prompt_names,
            scores=scores,
            candidate_summaries=candidate_summaries,
        )
        best_score = scores[best_idx]
        best_candidate = candidates[best_idx]
        best_candidate_id = str(candidate_lineage_nodes[best_idx]["candidate_id"])
        if output_prefix:
            best_version = f"{output_prefix}/eval_{iteration}_c{best_idx + 1}"
        else:
            best_version = f"eval_{iteration}_c{best_idx + 1}"

        logger.info(
            f"[EvalExecute] Best candidate: {best_idx + 1} with score {best_score:.2f} "
            f"(selection_mode={selection_info['selection_mode']})"
        )
        self._append_lineage_event(
            output_prefix=output_prefix,
            event_type="candidate_selected",
            payload={
                "iteration": iteration,
                "selected_candidate_id": best_candidate_id,
                "best_score": best_score,
                "selection": selection_info,
            },
        )

        # 5. Copy winning candidate to output_dir
        src = self.optimized_dir / best_version
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        for f in src.iterdir():
            shutil.copy2(f, output_dir / f.name)

        promoted_module_sha = self._hash_candidate_modules(
            best_version,
            prompt_names,
            destination_dir=output_dir,
        )

        metadata = {
            "version": output_dir.name,
            "method": "eval_execute",
            "iteration": iteration,
            "n_candidates": len(candidates),
            "n_mutation_candidates_requested": self.n_candidates,
            "best_candidate_index": best_idx + 1,
            "best_score": best_score,
            "all_scores": scores,
            "candidate_app_scores": [summary.get("per_app_scores", {}) for summary in candidate_summaries],
            "candidate_run_labels": [summary.get("classification_counts", {}) for summary in candidate_summaries],
            "historical_best": historical_best,
            "selection": selection_info,
            "recombination": recombination_info,
            "lineage": {
                "selected_candidate_id": best_candidate_id,
                "base_parent_candidate_ids": base_parent_candidate_ids,
                "lineage_file": str(scoped_optimized_dir / "lineage.jsonl"),
                "candidate_nodes": candidate_lineage_nodes,
            },
            "reproducibility": {
                "random_seed": random_seed,
                "training_trajectories_hash": training_fingerprint["sha256"],
                "training_trajectory_count": training_fingerprint["count"],
                "training_trajectory_files_sample": training_fingerprint["sample_files"],
                "optimizer": self.config.optimization.optimizer,
                "teacher_model": self.config.optimization.teacher_model,
                "selection_mode": self.config.optimization.selection_mode,
                "selection_top_k": self.config.optimization.selection_top_k,
                "phase_signal_weight": self.config.optimization.phase_signal_weight,
                "n_candidates_requested": self.n_candidates,
                "n_candidates_evaluated": len(candidates),
                "train_apps": sorted(normalize_experiment_token(app.name) for app in train_apps),
            },
            "prompts": {
                pname: {
                    "optimized": True,
                    "instruction": best_candidate.get(pname, ""),
                }
                for pname in prompt_names
            },
        }
        (output_dir / "metadata.json").write_text(json.dumps(metadata, indent=2))

        # Update 'latest' symlink — scoped per prefix to avoid collisions
        if output_prefix:
            latest = self.optimized_dir / output_prefix / "latest"
            relative_target = output_dir.name  # e.g. "v1"
        else:
            latest = self.optimized_dir / "latest"
            relative_target = output_dir.relative_to(self.optimized_dir)
        if latest.exists() or latest.is_symlink():
            latest.unlink()
        try:
            latest.symlink_to(relative_target)
        except OSError:
            pass

        self._append_lineage_event(
            output_prefix=output_prefix,
            event_type="promoted_to_version",
            payload={
                "iteration": iteration,
                "selected_candidate_id": best_candidate_id,
                "output_version": output_dir.name,
                "output_dir": str(output_dir),
                "module_sha256": promoted_module_sha,
                "metadata_file": str(output_dir / "metadata.json"),
            },
        )

        logger.info(f"[EvalExecute] Saved best candidate to {output_dir}")

        return {
            "success": True,
            "output_dir": str(output_dir),
            "best_candidate": best_idx + 1,
            "best_score": best_score,
            "all_scores": scores,
            "selection": selection_info,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _run_candidate_on_app(
        self,
        *,
        app_path: Path,
        work_dir: Path,
        iteration: int,
        c_idx: int,
        candidate_version: str,
        provider: str,
        max_retries: int,
        rate_limit_backoff: int,
        provider_override: str | None,
        model_override: str | None,
        inter_run_delay: int,
    ) -> dict[str, Any]:
        """Deploy one candidate against one training app and return the outcome dict."""
        app_name = normalize_experiment_token(app_path.name)
        exp_dir = work_dir / f"iter{iteration}_c{c_idx + 1}_{app_name}"

        cmd = [sys.executable, "-m", "app_operator", "run", str(exp_dir)]
        operation_name = f"candidate_{c_idx + 1}_{app_name}"

        success = False
        error_msg: str | None = None
        for _attempt in range(max_retries + 1):
            # Always start from a clean state so a rate-limited retry never
            # inherits a .sds directory that has already hit the attempt ceiling.
            self._cleanup_experiment_containers(exp_dir)
            try:
                self._reset_experiment_dir(app_path=app_path, exp_dir=exp_dir, attempt=_attempt)
                self._write_sds_toml(
                    exp_dir,
                    candidate_version,
                    provider_override=provider_override,
                    model_override=model_override,
                )
            except OSError as exc:
                error_msg = f"{operation_name} could not prepare run directory: {exc!s}"
                logger.error(error_msg)
                break

            logger.info(
                "[EvalExecute] Running operator on {} (attempt {}/{})...",
                exp_dir.name,
                _attempt + 1,
                max_retries + 1,
            )
            try:
                proc = self._run_candidate_subprocess(cmd)
            except (OSError, subprocess.SubprocessError) as exc:
                error_msg = f"{operation_name} raised exception: {exc!s}"
                logger.error(error_msg)
                break

            stderr = proc.stderr or ""
            rate_limit_err = detect_rate_limit_error(stderr, proc.returncode, provider)

            if rate_limit_err and _attempt < max_retries:
                delay = rate_limit_backoff + exponential_backoff(_attempt)
                logger.warning(
                    f"{operation_name} hit rate limit (attempt {_attempt + 1}/{max_retries + 1}). "
                    f"{rate_limit_err.message}. Resetting state and waiting {delay}s before retry..."
                )
                time.sleep(delay)
                continue

            if rate_limit_err:
                error_msg = (
                    f"{operation_name} failed after {max_retries + 1} attempts "
                    f"due to rate limiting: {rate_limit_err.message}"
                )
                logger.error(error_msg)
                break

            if proc.returncode != 0:
                error_msg = f"{operation_name} failed with exit code {proc.returncode}"
                logger.error(error_msg)
                if stderr:
                    logger.error(f"Error output: {stderr}")
            else:
                if _attempt > 0:
                    logger.info(f"{operation_name} succeeded after {_attempt} retry(ies)")
                success = True
            break

        if not success:
            logger.warning(f"[EvalExecute] Run failed for {exp_dir.name}: {error_msg}")

        rlm_score = self._score_rlm_trajectory(exp_dir)
        phase_metrics = self._collect_phase_metrics(exp_dir)
        classification_label, classification_score, classification_reasons = self._classify_run_signal(exp_dir)
        if classification_score is not None:
            phase_metrics["run_classification_quality"] = classification_score
        if classification_label in _CLASSIFICATION_RELIABILITY_LABELS:
            logger.warning(
                "[EvalExecute] Classifier flagged {} as {} ({})",
                exp_dir.name,
                classification_label,
                "; ".join(classification_reasons) if classification_reasons else "no reason provided",
            )

        # Stop containers immediately so they don't hold ports
        self._cleanup_experiment_containers(exp_dir)

        if inter_run_delay > 0:
            time.sleep(inter_run_delay)

        return {
            "app_name": app_name,
            "success": success,
            "error": error_msg or "",
            "rlm_score": rlm_score,
            "phase_metrics": phase_metrics,
            "classification_label": classification_label,
            "classification_score": classification_score,
            "classification_reasons": classification_reasons,
        }

    def _reset_experiment_dir(self, *, app_path: Path, exp_dir: Path, attempt: int) -> None:
        """Recreate the experiment directory, archiving stale retries if needed."""
        if exp_dir.exists():
            self._remove_or_archive_exp_dir(exp_dir, attempt)
        shutil.copytree(app_path, exp_dir)
        self._clean_exp_dir(exp_dir)

    def _remove_or_archive_exp_dir(self, exp_dir: Path, attempt: int) -> None:
        """Remove an existing run directory or archive it when container-owned files block deletion."""
        try:
            shutil.rmtree(exp_dir)
            return
        except OSError as exc:
            archived_dir = self._next_stale_exp_dir(exp_dir, attempt)
            try:
                exp_dir.rename(archived_dir)
            except OSError as archive_exc:
                raise OSError(
                    f"failed to reset {exp_dir}: delete failed with {exc!s}; archive failed with {archive_exc!s}"
                ) from archive_exc
            logger.warning(
                "[EvalExecute] Could not delete {} during retry reset ({}). Archived it to {} and continuing.",
                exp_dir,
                exc,
                archived_dir,
            )

    @staticmethod
    def _next_stale_exp_dir(exp_dir: Path, attempt: int) -> Path:
        """Choose a unique sibling path for an archived stale experiment directory."""
        candidate = exp_dir.parent / f"{exp_dir.name}.stale_attempt_{attempt}"
        suffix = 2
        while candidate.exists():
            candidate = exp_dir.parent / f"{exp_dir.name}.stale_attempt_{attempt}_{suffix}"
            suffix += 1
        return candidate

    def _run_candidate_subprocess(self, cmd: list[str]) -> subprocess.CompletedProcess[str]:
        """Run a single operator subprocess invocation.

        Extracted as a method so tests can monkeypatch it on the instance
        without touching the generic subprocess module.
        """
        return subprocess.run(cmd, capture_output=True, text=True)

    def _classify_run_signal(self, exp_dir: Path) -> tuple[str | None, float | None, list[str]]:
        """Classify a run directory and return (label, score, reasons)."""
        if not (exp_dir / ".sds").exists():
            return None, None, []

        try:
            classification = classify_run(exp_dir)
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as e:
            logger.warning("[EvalExecute] Failed to classify run {}: {}", exp_dir.name, e)
            return None, None, []

        if not self._has_classification_signal(classification):
            return None, None, []

        label = classification.label.strip() if classification.label else ""
        if not label:
            return None, None, []

        score = self._classification_score_for_label(label)
        reasons = self._dedupe_limit(list(classification.reasons), limit=2)
        return label, score, reasons

    @staticmethod
    def _has_classification_signal(classification: RunClassification) -> bool:
        """Return True when run-classifier output is based on non-empty telemetry."""
        return bool(
            classification.trajectory_status is not None
            or classification.log_deploy_attempts > 0
            or classification.log_health_checks > 0
            or classification.log_health_rechecks > 0
            or classification.final_health_exit_code is not None
            or classification.health_contradictions
            or classification.monitor_concerns
        )

    @staticmethod
    def _classification_score_for_label(label: str) -> float:
        """Map classifier label to normalized score."""
        return float(_CLASSIFICATION_SCORE_BY_LABEL.get(label, 0.5))

    @staticmethod
    def _classification_counts(run_outcomes: list[dict[str, Any]]) -> dict[str, int]:
        """Count run-classifier labels across candidate outcomes."""
        counts: dict[str, int] = {}
        for outcome in run_outcomes:
            label = str(outcome.get("classification_label") or "").strip()
            if not label:
                continue
            counts[label] = counts.get(label, 0) + 1
        return counts

    def _load_current_instruction(self, prompt_name: str, current_version: str | None) -> str:
        """Return the instruction string currently used for *prompt_name*."""
        if current_version:
            optimized = self._load_optimized_instruction(prompt_name, current_version)
            if optimized:
                return optimized
        return self._seed_instruction(prompt_name)

    def _load_optimized_instruction(self, prompt_name: str, current_version: str) -> str | None:
        """Load a previously saved optimized instruction, or return None."""
        from app_operator.dspy_integration._loader import resolve_version

        resolved = resolve_version(self.optimized_dir, current_version)
        if not resolved:
            return None
        module_file = self.optimized_dir / resolved / f"{prompt_name}.dspy.json"
        if not module_file.exists():
            return None
        state = json.loads(module_file.read_text())
        return state.get("optimized_instruction") or None

    def _load_historical_best_summary(
        self,
        current_version: str | None,
        output_prefix: str | None,
    ) -> dict[str, Any] | None:
        """Load summary metadata for the previous iteration's selected winner.

        Reads the previous version's metadata to retrieve the best score and
        classification/per-app summaries. This is retained for reporting and
        comparison only; the incumbent prompt is still rerun as candidate c1.

        Returns a dict with ``score``, ``per_app_scores``,
        ``classification_counts``, and ``source_version``, or None if no prior
        metadata is available.
        """
        if not current_version:
            return None
        from app_operator.dspy_integration._loader import resolve_version

        scoped_dir = self._scoped_optimized_dir(output_prefix)
        version_leaf = current_version.rsplit("/", 1)[-1]
        resolved = resolve_version(scoped_dir, version_leaf)
        if not resolved:
            return None
        metadata_path = scoped_dir / resolved / "metadata.json"
        if not metadata_path.exists():
            return None
        try:
            metadata: dict[str, Any] = json.loads(metadata_path.read_text())
        except (OSError, json.JSONDecodeError):
            return None

        best_score = metadata.get("best_score")
        if not isinstance(best_score, int | float):
            return None

        # Per-app scores for the winning candidate from the previous iteration
        best_candidate_index = metadata.get("best_candidate_index", 1)
        if isinstance(best_candidate_index, int) and best_candidate_index > 0:
            best_idx = best_candidate_index - 1  # 1-indexed in metadata
        else:
            best_idx = 0
        candidate_app_scores: list[Any] = metadata.get("candidate_app_scores", [])
        per_app_scores: dict[str, float] = {}
        if 0 <= best_idx < len(candidate_app_scores):
            raw_per_app_scores = candidate_app_scores[best_idx]
            if isinstance(raw_per_app_scores, dict):
                raw_per_app_dict = cast("dict[Any, Any]", raw_per_app_scores)
                for app_name, app_score in raw_per_app_dict.items():
                    if isinstance(app_score, int | float):
                        per_app_scores[str(app_name)] = float(app_score)

        # Classification counts for the winning candidate
        candidate_run_labels: list[Any] = metadata.get("candidate_run_labels", [])
        classification_counts: dict[str, int] = {}
        if 0 <= best_idx < len(candidate_run_labels):
            raw_classification_counts = candidate_run_labels[best_idx]
            if isinstance(raw_classification_counts, dict):
                raw_cls_dict = cast("dict[Any, Any]", raw_classification_counts)
                for label, count in raw_cls_dict.items():
                    if isinstance(count, int | float):
                        classification_counts[str(label)] = int(count)

        return {
            "score": float(best_score),
            "per_app_scores": per_app_scores,
            "classification_counts": classification_counts,
            "source_version": current_version,
            "best_candidate_index": best_idx + 1,
        }

    def _seed_instruction(self, prompt_name: str) -> str:
        """Return a minimal seed instruction for *prompt_name*."""
        defaults = {
            "deployer_fix_error": ("A deployment has failed. Analyze the error and fix the deployment scripts."),
            "deployer_summarize": ("Summarize the deployment outcome and any issues encountered."),
            "deployer_system": ("You are a deployment assistant. Help deploy applications correctly."),
            "deployer_generate_script": "Generate a deployment script for the application.",
            "deployer_generate_deploy_script": "Generate a deploy.sh script for the application.",
            "deployer_generate_health_check": ("Generate a health_check.sh script for the application."),
            "code_analyzer_system": ("Analyze the codebase and identify deployment requirements."),
            "code_analyzer_user": "Identify potential deployment issues in the codebase.",
            "monitor_analyze_health": ("Analyze the health check results and report on application status."),
        }
        return defaults.get(prompt_name, f"Perform the {prompt_name} task.")

    @staticmethod
    def _normalize_model_name(model: str) -> str:
        """Normalize short model names into litellm provider/model format."""
        if "/" in model:
            return model
        lower = model.lower()
        if "claude" in lower:
            return f"anthropic/{model}"
        if "gemini" in lower:
            return f"gemini/{model}"
        if "gpt" in lower or "o1" in lower:
            return f"openai/{model}"
        return model

    def _select_best_candidate(
        self,
        candidates: list[dict[str, str]],
        prompt_names: list[str],
        scores: list[float],
        candidate_summaries: list[dict[str, Any]],
    ) -> tuple[int, dict[str, Any]]:
        """Select best candidate using score, llm, or hybrid strategy."""
        ranked_indices = sorted(
            range(len(scores)),
            key=lambda i: scores[i],
            reverse=True,
        )
        score_best_idx = ranked_indices[0]
        top_k = min(
            len(scores),
            max(1, self.config.optimization.selection_top_k),
        )
        selection_mode = self.config.optimization.selection_mode
        selection_info: dict[str, Any] = {
            "selection_mode": selection_mode,
            "score_best_candidate": score_best_idx + 1,
            "score_ranking": [idx + 1 for idx in ranked_indices],
            "top_k": top_k,
            "llm_used": False,
            "llm_choice": None,
            "fallback_to_score": False,
            "llm_reason": "",
        }

        if selection_mode == "score":
            return score_best_idx, selection_info

        if selection_mode == "hybrid":
            candidate_pool = ranked_indices[:top_k]
        else:  # llm mode
            candidate_pool = ranked_indices

        if len(candidate_pool) <= 1:
            selection_info["fallback_to_score"] = True
            selection_info["llm_reason"] = "Insufficient candidate pool for LLM selection."
            return score_best_idx, selection_info

        llm_choice, llm_info = self._judge_candidates_with_llm(
            candidate_pool=candidate_pool,
            prompt_names=prompt_names,
            candidates=candidates,
            candidate_summaries=candidate_summaries,
        )
        selection_info.update(llm_info)
        selection_info["llm_used"] = True

        if llm_choice is None:
            selection_info["fallback_to_score"] = True
            return score_best_idx, selection_info
        return llm_choice, selection_info

    def _judge_candidates_with_llm(
        self,
        candidate_pool: list[int],
        prompt_names: list[str],
        candidates: list[dict[str, str]],
        candidate_summaries: list[dict[str, Any]],
    ) -> tuple[int | None, dict[str, Any]]:
        """Ask teacher LLM to choose the best candidate from candidate_pool."""

        def _fmt_number(value: Any) -> str:
            if isinstance(value, int | float):
                return f"{float(value):.4f}"
            return "n/a"

        summary_by_index = {int(s["candidate_index"]) - 1: s for s in candidate_summaries}
        sections: list[str] = []
        for idx in candidate_pool:
            summary = summary_by_index.get(idx, {})
            run_outcomes: list[dict[str, Any]] = summary.get("run_outcomes", [])
            failures: list[str] = []
            classifier_evidence: list[str] = []
            for outcome in run_outcomes:
                label = str(outcome.get("classification_label") or "").strip()
                reasons = [str(r).strip() for r in list(outcome.get("classification_reasons") or []) if str(r).strip()]
                reliability_issue = label in _CLASSIFICATION_RELIABILITY_LABELS
                app_name = str(outcome.get("app_name", "unknown"))

                if label:
                    reason_text = reasons[0] if reasons else "no classifier reason captured"
                    classifier_evidence.append(f"{app_name}: {label} ({reason_text})")
                if outcome.get("success") and not reliability_issue:
                    continue

                error = str(outcome.get("error") or "unknown failure").strip()
                if reliability_issue and reasons:
                    error = f"{label}: {reasons[0]}"
                elif label and label != "true_failure":
                    error = f"{label}: {error}"
                if len(error) > 220:
                    error = error[:217].rstrip() + "..."
                failures.append(f"{app_name}: {error}")
                if len(failures) >= 3:
                    break

            successful_runs = summary.get("successful_runs")
            total_runs = summary.get("total_runs")
            success_ratio = (
                f"{successful_runs}/{total_runs}"
                if isinstance(successful_runs, int) and isinstance(total_runs, int)
                else "n/a"
            )
            section_lines = [
                f"Candidate {idx + 1}:",
                f"- score: {_fmt_number(summary.get('score'))}",
                f"- success_rate: {success_ratio} ({_fmt_number(summary.get('success_rate'))})",
                f"- avg_rlm_score: {_fmt_number(summary.get('avg_rlm_score'))}",
                f"- classification_score: {_fmt_number(summary.get('classification_score'))}",
                f"- phase_score: {_fmt_number(summary.get('phase_score'))}",
            ]
            classification_counts = summary.get("classification_counts")
            if isinstance(classification_counts, dict) and classification_counts:
                cc_dict = cast("dict[str, Any]", classification_counts)
                label_summary = ", ".join(f"{label}={count}" for label, count in sorted(cc_dict.items()))
                section_lines.append(f"- run_labels: {label_summary}")
            if classifier_evidence:
                section_lines.append("- classifier_evidence:")
                section_lines.extend(f"  - {item}" for item in classifier_evidence[:5])
            else:
                section_lines.append("- classifier_evidence: none")
            for pname in prompt_names:
                instruction = candidates[idx].get(pname, "")
                section_lines.append(f"- instruction ({pname}):\n{instruction}")
            if failures:
                section_lines.append("- key_failures:")
                section_lines.extend(f"  - {failure}" for failure in failures)
            else:
                section_lines.append("- key_failures: none")
            sections.append("\n".join(section_lines))

        model = self._normalize_model_name(self.config.optimization.teacher_model)
        system_msg = (
            "You are a strict SDS candidate judge. "
            "Apply this decision rubric in order: "
            "(1) Reliability gate: strongly avoid candidates with false_positive, "
            "telemetry_inconsistent, or health_policy_regression signals "
            "unless every candidate has similar reliability issues. "
            "health_policy_regression means the agent weakened the health check instead of "
            "fixing the deployment — this is reward hacking and must never be selected. "
            "(2) Among reliability-safe candidates, prefer higher score/success_rate/classification_score. "
            "(3) Use full instruction quality and concrete failure patterns only as tie-breakers. "
            "Never choose outside the candidate pool."
        )
        user_msg = (
            "Choose exactly one candidate from the pool below.\n"
            "Decision rubric (apply in order):\n"
            "1. Reliability first: disqualify candidates with false_positive, "
            "telemetry_inconsistent, or health_policy_regression labels. "
            "health_policy_regression is selection-fatal: it means the agent deleted or weakened "
            "health checks to pass evaluation rather than fixing the actual deployment issue.\n"
            "2. Then optimize quantitative performance (score, success_rate, classification_score, phase_score).\n"
            "3. Then compare instruction quality and failure specificity.\n"
            "Return ONLY a valid JSON object with keys: "
            '{"chosen_candidate": <integer>, "reason": "<brief>", "confidence": <0_to_1>}.\n\n'
            f"Candidate pool: {[idx + 1 for idx in candidate_pool]}\n\n"
            "Candidate evidence:\n" + "\n\n".join(sections)
        )

        llm_info: dict[str, Any] = {
            "llm_choice": None,
            "llm_reason": "",
            "llm_confidence": None,
        }
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_msg},
            ],
            "cache": {"no-cache": True},
        }
        location = self.vertex_location or os.environ.get("VERTEX_LOCATION")
        if location:
            kwargs["vertex_location"] = location

        try:
            response = litellm.completion(**kwargs)  # type: ignore[reportUnknownMemberType]
            raw = cast("str", response.choices[0].message.content or "")  # type: ignore[reportAttributeAccessIssue]
            llm_info["llm_raw"] = raw
            json_match = re.search(r"\{.*\}", raw, re.DOTALL)
            if not json_match:
                llm_info["llm_reason"] = "Judge response did not contain JSON."
                return None, llm_info
            parsed = json.loads(json_match.group())
            chosen = int(parsed.get("chosen_candidate"))
            if chosen - 1 not in candidate_pool:
                llm_info["llm_reason"] = (
                    f"Judge selected candidate {chosen} outside pool {[idx + 1 for idx in candidate_pool]}."
                )
                return None, llm_info
            llm_info["llm_choice"] = chosen
            llm_info["llm_reason"] = str(parsed.get("reason", "")).strip()
            confidence = parsed.get("confidence")
            if isinstance(confidence, int | float):
                llm_info["llm_confidence"] = max(0.0, min(1.0, float(confidence)))
            return chosen - 1, llm_info
        except (json.JSONDecodeError, ValueError, TypeError, KeyError, ConnectionError, TimeoutError) as e:
            llm_info["llm_reason"] = f"Judge call failed: {e}"
            return None, llm_info

    def _generate_candidates(
        self,
        prompt_names: list[str],
        current_instructions: dict[str, str],
        trajectory_context: dict[str, str] | None = None,
    ) -> list[dict[str, str]]:
        """Generate ``self.n_candidates`` mutation candidate dicts."""
        per_prompt: dict[str, list[str]] = {}
        for pname in prompt_names:
            prompt_context = ""
            if trajectory_context:
                prompt_context = trajectory_context.get(pname, "")
            per_prompt[pname] = self._generate_instruction_variants(
                pname,
                current_instructions[pname],
                self.n_candidates,
                trajectory_context=prompt_context,
            )

        candidates: list[dict[str, str]] = []
        for c_idx in range(self.n_candidates):
            candidate: dict[str, str] = {}
            for pname in prompt_names:
                variants = per_prompt[pname]
                candidate[pname] = variants[c_idx] if c_idx < len(variants) else current_instructions[pname]
            candidates.append(candidate)
        return candidates

    def _load_historical_candidate_population(
        self,
        prompt_names: list[str],
        before_iteration: int,
        output_prefix: str | None,
        top_k: int,
    ) -> list[dict[str, Any]]:
        """Load top-scoring prior candidates (including non-winners) from metadata."""
        scoped_optimized_dir = self._scoped_optimized_dir(output_prefix)
        if not scoped_optimized_dir.exists():
            return []

        population: list[dict[str, Any]] = []
        for metadata_file in sorted(scoped_optimized_dir.glob("v*/metadata.json")):
            try:
                metadata: dict[str, Any] = json.loads(metadata_file.read_text())
            except Exception:
                continue

            meta_iteration = metadata.get("iteration")
            if not isinstance(meta_iteration, int):
                match = re.match(r"^v(\d+)$", metadata_file.parent.name)
                meta_iteration = int(match.group(1)) if match else 0
            if meta_iteration <= 0 or meta_iteration >= before_iteration:
                continue

            all_scores_raw = metadata.get("all_scores")
            if not isinstance(all_scores_raw, list):
                continue
            all_scores: list[Any] = cast("list[Any]", all_scores_raw)

            candidate_app_scores_raw = metadata.get("candidate_app_scores")
            if not isinstance(candidate_app_scores_raw, list):
                candidate_app_scores: list[Any] = []
            else:
                candidate_app_scores = cast("list[Any]", candidate_app_scores_raw)

            for idx, raw_score in enumerate(all_scores, start=1):
                try:
                    score = float(raw_score)
                except (TypeError, ValueError):
                    continue

                candidate_tag = f"eval_{meta_iteration}_c{idx}"
                candidate_dir = scoped_optimized_dir / candidate_tag
                if not candidate_dir.exists():
                    continue

                instructions: dict[str, str] = {}
                missing_prompt = False
                for prompt_name in prompt_names:
                    module_file = candidate_dir / f"{prompt_name}.dspy.json"
                    if not module_file.exists():
                        missing_prompt = True
                        break
                    try:
                        state = json.loads(module_file.read_text())
                    except Exception:
                        missing_prompt = True
                        break
                    instruction = str(state.get("optimized_instruction") or "").strip()
                    if not instruction:
                        missing_prompt = True
                        break
                    instructions[prompt_name] = instruction

                if missing_prompt:
                    continue

                per_app: dict[str, float] = {}
                raw_app_scores = candidate_app_scores[idx - 1] if idx - 1 < len(candidate_app_scores) else None
                if isinstance(raw_app_scores, dict):
                    per_app = cast("dict[str, float]", raw_app_scores)

                population.append(
                    {
                        "iteration": meta_iteration,
                        "candidate_index": idx,
                        "candidate_version": candidate_tag,
                        "score": score,
                        "per_app_scores": per_app,
                        "instructions": instructions,
                    }
                )

        population.sort(
            key=lambda item: (
                -float(item["score"]),
                -int(item["iteration"]),
                int(item["candidate_index"]),
            )
        )
        return population[: max(0, top_k)]

    def _select_recombination_parents(
        self,
        historical_population: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Select two distinct parents via score-weighted sampling."""
        weights = [max(float(p["score"]), 0.01) for p in historical_population]
        parent_a = random.choices(historical_population, weights=weights, k=1)[0]
        remaining = [p for p in historical_population if p is not parent_a]
        if not remaining:
            return parent_a, parent_a
        rem_weights = [max(float(p["score"]), 0.01) for p in remaining]
        parent_b = random.choices(remaining, weights=rem_weights, k=1)[0]
        return parent_a, parent_b

    def _maybe_inject_recombined_candidate(
        self,
        candidates: list[dict[str, str]],
        prompt_names: list[str],
        trajectory_context: dict[str, str],
        historical_population: list[dict[str, Any]],
        iteration: int,
    ) -> tuple[list[dict[str, str]], dict[str, Any]]:
        """Occasionally replace one candidate with a cross-iteration recombination."""
        info: dict[str, Any] = {
            "used": False,
            "reason": "",
            "parent_candidates": [],
        }
        if not candidates:
            info["reason"] = "No generated candidates."
            return candidates, info
        if iteration < 2:
            info["reason"] = "First iteration has no prior candidate pool."
            return candidates, info
        if len(historical_population) < 2:
            info["reason"] = "Not enough historical candidates for recombination."
            return candidates, info
        if iteration % 2 != 0:
            info["reason"] = "Recombination is scheduled every other iteration."
            return candidates, info

        parent_a, parent_b = self._select_recombination_parents(historical_population)
        recombined = self._compose_recombined_candidate(
            prompt_names=prompt_names,
            parent_a=parent_a,
            parent_b=parent_b,
            trajectory_context=trajectory_context,
        )
        if not recombined:
            info["reason"] = "Failed to build recombined candidate."
            return candidates, info

        updated_candidates = list(candidates)
        updated_candidates[-1] = recombined
        info["used"] = True
        info["reason"] = "Injected one cross-candidate recombination into candidate pool."
        info["parent_candidates"] = [
            str(parent_a.get("candidate_version", "")),
            str(parent_b.get("candidate_version", "")),
        ]
        logger.info(
            "[EvalExecute] Injected recombined candidate from {} + {}",
            info["parent_candidates"][0],
            info["parent_candidates"][1],
        )
        return updated_candidates, info

    def _compose_recombined_candidate(
        self,
        prompt_names: list[str],
        parent_a: dict[str, Any],
        parent_b: dict[str, Any],
        trajectory_context: dict[str, str],
    ) -> dict[str, str]:
        """Compose a new candidate by merging two historically strong candidates."""
        parent_a_instructions = parent_a.get("instructions", {})
        parent_b_instructions = parent_b.get("instructions", {})
        if not isinstance(parent_a_instructions, dict) or not isinstance(parent_b_instructions, dict):
            return {}
        parent_a_instr_dict = cast("dict[str, Any]", parent_a_instructions)
        parent_b_instr_dict = cast("dict[str, Any]", parent_b_instructions)

        recombined: dict[str, str] = {}
        for prompt_name in prompt_names:
            parent_a_instruction = str(parent_a_instr_dict.get(prompt_name) or "").strip()
            parent_b_instruction = str(parent_b_instr_dict.get(prompt_name) or "").strip()
            if not parent_a_instruction and not parent_b_instruction:
                continue
            if not parent_a_instruction:
                recombined[prompt_name] = parent_b_instruction
                continue
            if not parent_b_instruction:
                recombined[prompt_name] = parent_a_instruction
                continue

            recombined[prompt_name] = self._recombine_instruction(
                prompt_name=prompt_name,
                parent_a_instruction=parent_a_instruction,
                parent_b_instruction=parent_b_instruction,
                trajectory_context=trajectory_context.get(prompt_name, ""),
            )

        return recombined

    def _recombine_instruction(
        self,
        prompt_name: str,
        parent_a_instruction: str,
        parent_b_instruction: str,
        trajectory_context: str,
    ) -> str:
        """Merge two instructions into one stronger prompt using teacher model."""
        model = self._normalize_model_name(self.config.optimization.teacher_model)
        system_msg = (
            "You merge prompt instructions for an SDS deployment agent. "
            "Preserve concrete, high-signal guidance that improves runtime success."
        )
        context_block = ""
        if trajectory_context:
            context_block = f"Recent evidence summary:\n{trajectory_context}\n\n"
        user_msg = (
            f"Prompt name: {prompt_name}\n\n"
            "Parent instruction A:\n"
            f"{parent_a_instruction}\n\n"
            "Parent instruction B:\n"
            f"{parent_b_instruction}\n\n"
            f"{context_block}"
            "Write one merged instruction that combines the strongest elements of A and B. "
            "Return only the merged instruction text."
        )
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_msg},
            ],
            "cache": {"no-cache": True},
        }
        location = self.vertex_location or os.environ.get("VERTEX_LOCATION")
        if location:
            kwargs["vertex_location"] = location

        try:
            response = litellm.completion(**kwargs)  # type: ignore[reportUnknownMemberType]
            merged = self._extract_completion_text(response).strip()
            merged = self._strip_code_fences(merged)
            if merged:
                return merged
        except Exception as e:
            logger.warning(f"[EvalExecute] Failed to recombine instruction for {prompt_name}: {e}")
        return parent_a_instruction

    def _generate_instruction_variants(
        self,
        prompt_name: str,
        current_instruction: str,
        n: int,
        trajectory_context: str = "",
    ) -> list[str]:
        """Use the teacher LLM to produce *n* instruction variants."""
        model = self._normalize_model_name(self.config.optimization.teacher_model)

        system_msg = (
            "You are a prompt optimization expert helping improve AI deployment agent "
            "instructions to increase deployment success rates."
        )
        evidence_block = ""
        if trajectory_context:
            evidence_block = (
                "Evidence from prior SDS runs (compressed):\n"
                f"{trajectory_context}\n\n"
                "Use this evidence to address recurring failures and avoid previously "
                "ineffective behaviors.\n\n"
            )
        user_msg = (
            f"Current instruction for the '{prompt_name}' step:\n"
            f"{current_instruction}\n\n"
            f"{evidence_block}"
            f"Generate exactly {n} improved variants. "
            'Return ONLY a JSON array of strings, e.g. ["variant1", "variant2"]. '
            "Each variant should be 1-3 sentences and focus on clearer guidance for "
            "fixing deployment errors."
        )

        kwargs: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_msg},
            ],
            "cache": {"no-cache": True},
        }
        location = self.vertex_location or os.environ.get("VERTEX_LOCATION")
        if location:
            kwargs["vertex_location"] = location

        try:
            response = litellm.completion(**kwargs)  # type: ignore[reportUnknownMemberType]
            raw = cast("str", response.choices[0].message.content or "")  # type: ignore[reportAttributeAccessIssue]
            json_match = re.search(r"\[.*?\]", raw, re.DOTALL)
            if json_match:
                parsed = json.loads(json_match.group())
                if isinstance(parsed, list) and parsed:
                    parsed_list = cast("list[Any]", parsed)
                    filtered = self._filter_forbidden_variants(parsed_list, current_instruction, prompt_name)
                    return self._pad_variants(filtered, n, current_instruction)
        except (json.JSONDecodeError, ValueError, ConnectionError, TimeoutError) as e:
            logger.warning(f"[EvalExecute] Failed to generate variants for {prompt_name}: {e}")

        # Fallback: repeat the current instruction
        return [current_instruction] * n

    @staticmethod
    def _filter_forbidden_variants(
        variants: list[Any],
        fallback: str,
        prompt_name: str,
    ) -> list[str]:
        """Replace any variant containing a forbidden instruction pattern with the fallback.

        Forbidden patterns indicate the LLM has encoded check-removal heuristics
        (e.g. "remove the check", "skip the probe") into the candidate prompt.
        Such variants would reward health-gate weakening and must never be evaluated.
        """
        result: list[str] = []
        for variant in variants:
            text = str(variant)
            lower = text.lower()
            rejected = False
            for pattern in _FORBIDDEN_INSTRUCTION_PATTERNS:
                if pattern in lower:
                    logger.warning(
                        "[EvalExecute] Rejected candidate instruction for '{}' — contains forbidden pattern '{}': {!r}",
                        prompt_name,
                        pattern,
                        text[:120],
                    )
                    rejected = True
                    break
            result.append(fallback if rejected else text)
        return result

    @staticmethod
    def _pad_variants(variants: list[Any], n: int, fallback: str) -> list[str]:
        """Pad *variants* with *fallback* until it has exactly *n* entries."""
        result = [str(v) for v in variants]
        while len(result) < n:
            result.append(fallback)
        return result[:n]

    def _build_recent_trajectory_context(
        self,
        work_dir: Path,
        iteration: int,
        prompt_names: list[str],
    ) -> dict[str, str]:
        """Build per-prompt trajectory context from recent experiment runs."""
        evidences = self._collect_recent_trajectory_evidence(work_dir, iteration)
        if not evidences:
            return dict.fromkeys(prompt_names, "")

        run_summaries = self._rank_weighted_notes(
            [
                (
                    e.weight,
                    (
                        f"{e.app_name} via {e.run_name}: status={e.status}, attempts={e.attempts}"
                        + (f", classification={e.classification_label}" if e.classification_label else "")
                        + (f", classifier_signal={e.classification_reasons[0]}" if e.classification_reasons else "")
                    ),
                )
                for e in evidences
            ]
        )
        trajectory_notes = self._rank_weighted_notes(
            [(e.weight, note) for e in evidences for note in e.trajectory_insights]
        )
        error_notes = self._rank_weighted_notes(
            [(e.weight, note) for e in evidences for note in (e.error_insights + e.error_signals)]
            + [
                (
                    e.weight * 1.15,
                    f"{e.app_name} via {e.run_name}: [{e.classification_label}] {note}",
                )
                for e in evidences
                if e.classification_label and e.classification_reasons
                for note in e.classification_reasons
            ]
        )
        script_notes = self._rank_weighted_notes([(e.weight, note) for e in evidences for note in e.script_insights])
        repo_notes = self._rank_weighted_notes([(e.weight, note) for e in evidences for note in e.repo_insights])
        validation_failure_notes = self._rank_weighted_notes(
            [
                (
                    e.weight * 1.25,
                    f"{e.app_name} via {e.run_name}: {note}",
                )
                for e in evidences
                if e.run_type == "validation" and self._is_failure_status(e.status)
                for note in (
                    self._dedupe_limit(
                        e.error_signals + e.error_insights + e.trajectory_insights,
                        limit=3,
                    )
                    or [f"status={e.status} (no detailed error captured)"]
                )
            ]
        )
        for note in validation_failure_notes:
            logger.info(
                "[EvalExecute] Validation failure signal captured: {}",
                note,
            )

        context_by_prompt: dict[str, str] = {}
        for prompt_name in prompt_names:
            sections = self._context_sections_for_prompt(
                prompt_name,
                run_summaries=run_summaries,
                validation_failure_notes=validation_failure_notes,
                trajectory_notes=trajectory_notes,
                error_notes=error_notes,
                script_notes=script_notes,
                repo_notes=repo_notes,
            )
            context = self._summarize_context_sections(prompt_name, sections)
            if context:
                logger.info(
                    "[EvalExecute] Using {} chars of trajectory context for {}",
                    len(context),
                    prompt_name,
                )
            context_by_prompt[prompt_name] = context

        return context_by_prompt

    def _blend_base_and_phase_scores(
        self,
        base_score: float,
        phase_score: float | None,
    ) -> float:
        """Blend end-to-end score with prompt-aligned phase score when available.

        When base_score is zero (complete failure with no classification signal),
        the phase weight is capped to prevent script-quality heuristics from
        creating an artificial score floor that makes all failures look equivalent.
        """
        if phase_score is None:
            return base_score
        weight = float(self.config.optimization.phase_signal_weight)
        if base_score == 0.0:
            weight = min(weight, 0.15)
        return ((1.0 - weight) * base_score) + (weight * phase_score)

    @staticmethod
    def _blend_base_and_classification_scores(
        base_score: float,
        classification_score: float,
    ) -> float:
        """Blend deployment success score with run-classifier reliability score."""
        return ((1.0 - _CLASSIFICATION_SIGNAL_WEIGHT) * base_score) + (
            _CLASSIFICATION_SIGNAL_WEIGHT * classification_score
        )

    def _compute_prompt_aligned_phase_score(
        self,
        prompt_names: list[str],
        run_outcomes: list[dict[str, Any]],
    ) -> float | None:
        """Compute phase score using only metrics relevant to the optimized prompts."""
        per_run_scores: list[float] = []
        for outcome in run_outcomes:
            phase_metrics = outcome.get("phase_metrics")
            if not isinstance(phase_metrics, dict) or not phase_metrics:
                continue
            phase_metrics_dict = cast("dict[str, Any]", phase_metrics)

            prompt_scores: list[float] = []
            for prompt_name in prompt_names:
                metric_names = _PROMPT_PHASE_METRICS.get(prompt_name, ())
                if not metric_names:
                    continue
                metric_values = [float(phase_metrics_dict[name]) for name in metric_names if name in phase_metrics_dict]
                if metric_values:
                    prompt_scores.append(sum(metric_values) / len(metric_values))
            if prompt_scores:
                per_run_scores.append(sum(prompt_scores) / len(prompt_scores))

        if not per_run_scores:
            return None
        return sum(per_run_scores) / len(per_run_scores)

    def _collect_phase_metrics(self, exp_dir: Path) -> dict[str, float]:
        """Collect phase-level quality metrics from generated scripts + trajectory."""
        metrics: dict[str, float] = {}
        sds_dir = exp_dir / ".sds"
        deploy_script = sds_dir / "deploy.sh"
        health_script = sds_dir / "health_check.sh"

        deploy_quality = self._score_script_quality(deploy_script, _DEPLOY_SCRIPT_MARKERS)
        if deploy_quality is not None:
            metrics["deploy_script_quality"] = deploy_quality

        health_quality = self._score_script_quality(health_script, _HEALTH_CHECK_MARKERS)
        if health_quality is not None:
            metrics["health_check_quality"] = health_quality

        trajectory = self._load_latest_trajectory(exp_dir)
        if trajectory is not None:
            metrics.update(self._phase_metrics_from_trajectory(trajectory))

        return metrics

    @staticmethod
    def _score_script_quality(path: Path, markers: tuple[str, ...]) -> float | None:
        """Heuristic script quality score in [0, 1], or None when script is absent."""
        if not path.exists():
            return None

        try:
            content = path.read_text()
        except Exception:
            return 0.0

        text = content.strip()
        if not text:
            return 0.0

        score = 0.4
        lines = text.splitlines()
        first_line = lines[0] if lines else ""
        if first_line.startswith("#!"):
            score += 0.2
        lowered = text.lower()
        if any(marker in lowered for marker in markers):
            score += 0.25
        if "set -e" in lowered:
            score += 0.1
        if os.access(path, os.X_OK):
            score += 0.05
        return min(1.0, score)

    @staticmethod
    def _load_latest_trajectory(exp_dir: Path) -> dict[str, Any] | None:
        """Load latest trajectory JSON from experiment directory."""
        traj_dir = exp_dir / ".sds" / "trajectories"
        if not traj_dir.exists():
            return None
        traj_files = sorted(traj_dir.glob("trajectory_*.json"), reverse=True)
        if not traj_files:
            return None
        try:
            return json.loads(traj_files[0].read_text())
        except Exception:
            return None

    def _phase_metrics_from_trajectory(self, trajectory: dict[str, Any]) -> dict[str, float]:
        """Extract phase-level quality metrics from one run trajectory."""
        metrics: dict[str, float] = {}
        deployment = trajectory.get("deployment", [])
        attempts = len(deployment)
        status = str(trajectory.get("metadata", {}).get("status", "unknown"))

        error_signals = 0
        last_assistant_content = ""
        for convo in deployment:
            for msg in convo.get("messages", []):
                role = str(msg.get("role", ""))
                if role == "tool_call":
                    for field in ("stderr", "stdout"):
                        error_signals += len(self._extract_error_signal_lines(str(msg.get(field) or "")))
                elif role == "assistant":
                    content = str(msg.get("content") or "").strip()
                    if content:
                        last_assistant_content = content

        if attempts > 0:
            if self._is_success_status(status):
                if attempts == 1 and error_signals == 0:
                    error_recovery = 0.8
                elif attempts <= 3:
                    error_recovery = 1.0
                else:
                    error_recovery = max(0.5, 1.0 - (0.1 * (attempts - 3)))
            else:
                error_recovery = 0.3 if attempts > 1 else 0.0
            metrics["error_recovery_quality"] = max(0.0, min(1.0, error_recovery))

        if last_assistant_content:
            summary_len = len(last_assistant_content)
            if summary_len >= 160:
                summary_quality = 1.0
            elif summary_len >= 80:
                summary_quality = 0.75
            elif summary_len >= 30:
                summary_quality = 0.5
            else:
                summary_quality = 0.25
            metrics["deployment_summary_quality"] = summary_quality

        return metrics

    @staticmethod
    def _context_sections_for_prompt(
        prompt_name: str,
        *,
        run_summaries: list[str],
        validation_failure_notes: list[str],
        trajectory_notes: list[str],
        error_notes: list[str],
        script_notes: list[str],
        repo_notes: list[str],
    ) -> list[tuple[str, list[str]]]:
        """Return the ordered evidence sections to include for *prompt_name*."""
        if prompt_name == "subagent_trajectory_analyst":
            return [
                ("Recent run outcomes", run_summaries),
                ("Validation failure (generalization signal)", validation_failure_notes),
                ("Trajectory analyst findings", trajectory_notes),
                ("Recurring error patterns", error_notes),
            ]
        if prompt_name == "subagent_error_log_analyst":
            return [
                ("Recent run outcomes", run_summaries),
                ("Validation failure (generalization signal)", validation_failure_notes),
                ("Error-log analyst findings", error_notes),
            ]
        if prompt_name == "subagent_script_analyst":
            return [
                ("Recent run outcomes", run_summaries),
                ("Validation failure (generalization signal)", validation_failure_notes),
                ("Script analyst findings", script_notes),
                ("Recurring error patterns", error_notes),
            ]
        if prompt_name == "subagent_repo_analyst":
            return [
                ("Recent run outcomes", run_summaries),
                ("Validation failure (generalization signal)", validation_failure_notes),
                ("Repository analyst findings", repo_notes),
                ("Recurring error patterns", error_notes),
            ]
        if prompt_name == "subagent_root_synthesis":
            return [
                ("Recent run outcomes", run_summaries),
                ("Validation failure (generalization signal)", validation_failure_notes),
                ("Trajectory findings", trajectory_notes),
                ("Error findings", error_notes),
                ("Script findings", script_notes),
                ("Repository findings", repo_notes),
            ]
        # Default: general prompts get run outcomes, errors, and script context.
        return [
            ("Recent run outcomes", run_summaries),
            ("Validation failure (generalization signal)", validation_failure_notes),
            ("Recurring error patterns", error_notes),
            ("Script findings", script_notes),
        ]

    def _collect_recent_trajectory_evidence(
        self,
        work_dir: Path,
        iteration: int,
        max_runs: int = 8,
    ) -> list[_TrajectoryEvidence]:
        """Collect compressed evidence from recent train/validation trajectories."""
        if not work_dir.exists():
            return []

        candidate_dirs: list[Path] = []
        for child in work_dir.iterdir():
            if not child.is_dir():
                continue
            if not _TRAIN_RUN_RE.match(child.name) and not _VAL_RUN_RE.match(child.name):
                continue
            run_iter, _app_name = self._parse_run_dir_name(child.name)
            if run_iter is None or run_iter > iteration:
                continue
            candidate_dirs.append(child)

        candidate_dirs.sort(key=lambda p: p.stat().st_mtime, reverse=True)

        evidences: list[_TrajectoryEvidence] = []
        for recency_index, run_dir in enumerate(candidate_dirs[:max_runs]):
            evidence = self._summarize_run_trajectory(run_dir)
            if evidence:
                evidence.weight = self._evidence_weight(evidence.status, recency_index)
                evidences.append(evidence)
        return evidences

    def _parse_run_dir_name(self, run_name: str) -> tuple[int | None, str]:
        """Parse iteration/app from experiment run directory name."""
        m_train = _TRAIN_RUN_RE.match(run_name)
        if m_train:
            return int(m_train.group(1)), m_train.group(2)

        m_val = _VAL_RUN_RE.match(run_name)
        if m_val:
            return int(m_val.group(2)), m_val.group(1)

        return None, run_name

    def _summarize_run_trajectory(self, run_dir: Path) -> _TrajectoryEvidence | None:
        """Summarize one run trajectory into compact textual evidence."""
        traj_dir = run_dir / ".sds" / "trajectories"
        if not traj_dir.exists():
            return None

        traj_files = sorted(traj_dir.glob("trajectory_*.json"), reverse=True)
        if not traj_files:
            return None

        try:
            trajectory: dict[str, Any] = json.loads(traj_files[0].read_text())
        except (json.JSONDecodeError, OSError, ValueError) as e:
            logger.warning(f"[EvalExecute] Failed to parse trajectory {traj_files[0]}: {e}")
            return None

        _iter, app_name = self._parse_run_dir_name(run_dir.name)
        deployment: list[dict[str, Any]] = trajectory.get("deployment", [])
        run_type = "validation" if _VAL_RUN_RE.match(run_dir.name) else "training"
        evidence = _TrajectoryEvidence(
            run_name=run_dir.name,
            app_name=app_name,
            run_type=run_type,
            status=str(trajectory.get("metadata", {}).get("status", "unknown")),
            attempts=len(deployment),
        )

        for convo in deployment:
            for msg in convo.get("messages", []):
                self._process_trajectory_message(msg, evidence)

        label, _score, reasons = self._classify_run_signal(run_dir)
        if label:
            evidence.classification_label = label
            evidence.classification_reasons = reasons

        return evidence

    def _process_trajectory_message(
        self,
        msg: dict[str, Any],
        evidence: _TrajectoryEvidence,
    ) -> None:
        """Classify one trajectory message and append insights to *evidence*."""
        role = msg.get("role")
        content = str(msg.get("content") or "")

        if role == "assistant" and content:
            lower = content.lower()
            snippet = self._extract_analysis_snippet(content)
            if snippet:
                self._classify_assistant_snippet(snippet, lower, evidence)

        if role == "tool_call":
            for field in ("stderr", "stdout"):
                evidence.error_signals.extend(self._extract_error_signal_lines(str(msg.get(field) or "")))

    @staticmethod
    def _classify_assistant_snippet(
        snippet: str,
        lower: str,
        evidence: _TrajectoryEvidence,
    ) -> None:
        """Route *snippet* into the appropriate evidence insight list."""
        if "[subagent trajectory analyst]" in lower or "[hybrid pre-analysis: trajectory]" in lower:
            evidence.trajectory_insights.append(snippet)
        elif "[subagent error_log analyst]" in lower or "[hybrid pre-analysis: error_log]" in lower:
            evidence.error_insights.append(snippet)
        elif (
            "[subagent script analyst]" in lower
            or "[hybrid pre-analysis: script]" in lower
            or "auto-validation warning" in lower
        ):
            evidence.script_insights.append(snippet)
        elif "[subagent repo analyst]" in lower or "[hybrid pre-analysis: repo]" in lower:
            evidence.repo_insights.append(snippet)

    @staticmethod
    def _evidence_weight(status: str, recency_index: int) -> float:
        """Weight evidence by recency and failure status.

        More recent trajectories get higher base weight. Failed runs are
        boosted so recurring failure patterns are more likely to influence
        subsequent candidate generation.
        """
        recency_weight = max(0.45, 1.0 - (0.10 * recency_index))
        normalized = status.strip().lower()
        if any(marker in normalized for marker in _FAILURE_STATUS_MARKERS):
            status_weight = 1.4
        elif any(marker in normalized for marker in _SUCCESS_STATUS_MARKERS):
            status_weight = 1.0
        else:
            status_weight = 1.15
        return recency_weight * status_weight

    @staticmethod
    def _is_failure_status(status: str) -> bool:
        """Return True when a status string indicates failure."""
        normalized = status.strip().lower()
        return any(marker in normalized for marker in _FAILURE_STATUS_MARKERS)

    @staticmethod
    def _is_success_status(status: str) -> bool:
        """Return True when a status string indicates success."""
        normalized = status.strip().lower()
        return any(marker in normalized for marker in _SUCCESS_STATUS_MARKERS)

    @staticmethod
    def _rank_weighted_notes(
        weighted_items: list[tuple[float, str]],
        limit: int = 8,
    ) -> list[str]:
        """Rank note texts by weight, deduping by text with highest weight."""
        best_by_text: dict[str, tuple[float, int]] = {}
        for index, (weight, text) in enumerate(weighted_items):
            clean = text.strip()
            if not clean:
                continue
            existing = best_by_text.get(clean)
            if existing is None:
                best_by_text[clean] = (weight, index)
                continue
            best_weight, first_seen = existing
            if weight > best_weight:
                best_by_text[clean] = (weight, first_seen)

        ranked = sorted(
            best_by_text.items(),
            key=lambda item: (-item[1][0], item[1][1], item[0]),
        )
        return [text for text, _meta in ranked[:limit]]

    @staticmethod
    def _extract_analysis_snippet(content: str, max_chars: int = 260) -> str:
        """Extract concise text from an assistant analysis message."""
        lines = [ln.strip() for ln in content.splitlines() if ln.strip()]
        if lines and lines[0].startswith("["):
            lines = lines[1:]
        if not lines:
            return ""
        text = " ".join(lines)
        if len(text) > max_chars:
            return text[: max_chars - 3].rstrip() + "..."
        return text

    @staticmethod
    def _extract_error_signal_lines(text: str, max_lines: int = 3) -> list[str]:
        """Extract representative failure lines from command output."""
        if not text:
            return []

        signals: list[str] = []
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            lower = line.lower()
            if any(marker in lower for marker in _ERROR_LINE_MARKERS):
                if len(line) > 220:
                    line = line[:217].rstrip() + "..."
                signals.append(line)

        deduped: list[str] = []
        seen: set[str] = set()
        for signal in signals:
            if signal in seen:
                continue
            seen.add(signal)
            deduped.append(signal)
            if len(deduped) >= max_lines:
                break
        return deduped

    @staticmethod
    def _dedupe_limit(items: list[str], limit: int = 8) -> list[str]:
        """Deduplicate while preserving order and limiting length."""
        out: list[str] = []
        seen: set[str] = set()
        for item in items:
            clean = item.strip()
            if not clean or clean in seen:
                continue
            seen.add(clean)
            out.append(clean)
            if len(out) >= limit:
                break
        return out

    def _format_context_sections(
        self,
        sections: list[tuple[str, list[str]]],
        per_section_limit: int = 8,
    ) -> str:
        """Render compact context text from titled evidence sections."""
        lines: list[str] = []
        for title, items in sections:
            chosen = self._dedupe_limit(items, limit=per_section_limit)
            if not chosen:
                continue
            lines.append(f"{title}:")
            lines.extend(f"- {item}" for item in chosen)

        if not lines:
            return ""

        return "\n".join(lines)

    def _summarize_context_sections(
        self,
        prompt_name: str,
        sections: list[tuple[str, list[str]]],
    ) -> str:
        """Summarize evidence into a bounded structured format."""
        raw_context = self._format_context_sections(sections)
        if not raw_context:
            return ""
        if len(raw_context) <= _TRAJECTORY_SUMMARY_TARGET_CHARS:
            return raw_context

        fallback = self._deterministic_context_summary(sections)
        model = self._normalize_model_name(self.config.optimization.teacher_model)
        system_msg = (
            "You summarize optimization evidence for SDS prompt tuning. "
            "Preserve concrete failure signatures and keep output compact."
        )
        user_msg = (
            f"Prompt: {prompt_name}\n\n"
            "Summarize the evidence below into a structured block with section headers and bullets.\n"
            "Requirements:\n"
            "- Keep section titles.\n"
            "- Keep at most 2 bullets per section.\n"
            f"- Keep total length <= {_TRAJECTORY_SUMMARY_TARGET_CHARS} characters.\n"
            "- Preserve concrete failure details verbatim when possible.\n\n"
            "Evidence:\n"
            f"{raw_context}"
        )
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_msg},
            ],
            "cache": {"no-cache": True},
        }
        location = self.vertex_location or os.environ.get("VERTEX_LOCATION")
        if location:
            kwargs["vertex_location"] = location

        try:
            response = litellm.completion(**kwargs)  # type: ignore[reportUnknownMemberType]
            summary = self._extract_completion_text(response).strip()
            summary = self._strip_code_fences(summary)
            if summary and len(summary) <= _TRAJECTORY_SUMMARY_TARGET_CHARS:
                return summary
        except Exception as e:
            logger.warning(
                "[EvalExecute] Failed to summarize long trajectory context for {}: {}",
                prompt_name,
                e,
            )

        return fallback

    def _deterministic_context_summary(
        self,
        sections: list[tuple[str, list[str]]],
    ) -> str:
        """Build a fixed-size structured summary without truncating raw text blindly."""
        compact_sections: list[tuple[str, list[str]]] = []
        for title, items in sections:
            chosen = self._dedupe_limit(items, limit=_SUMMARY_ITEMS_PER_SECTION)
            if not chosen:
                continue
            compact_items: list[str] = []
            for item in chosen:
                text = item.strip()
                if len(text) > _SUMMARY_ITEM_MAX_CHARS:
                    text = text[: _SUMMARY_ITEM_MAX_CHARS - 3].rstrip() + "..."
                compact_items.append(text)
            compact_sections.append((title, compact_items))
            if len(compact_sections) >= _SUMMARY_SECTION_LIMIT:
                break
        return self._format_context_sections(
            compact_sections,
            per_section_limit=_SUMMARY_ITEMS_PER_SECTION,
        )

    @staticmethod
    def _extract_completion_text(response: Any) -> str:
        """Extract message text from a completion response safely."""
        choices = getattr(response, "choices", None)
        if not choices:
            return ""
        try:
            message = getattr(choices[0], "message", None)
        except (IndexError, TypeError):
            return ""
        content = getattr(message, "content", "")
        return "" if content is None else str(content)

    @staticmethod
    def _strip_code_fences(text: str) -> str:
        """Drop wrapping Markdown code fences from model output."""
        stripped = text.strip()
        if stripped.startswith("```"):
            stripped = re.sub(r"^```[a-zA-Z0-9_-]*\n?", "", stripped)
            stripped = re.sub(r"\n?```$", "", stripped)
        return stripped.strip()

    @staticmethod
    def _candidate_id_for_tag(candidate_tag: str, output_prefix: str | None) -> str:
        """Return fully scoped candidate ID for lineage logs."""
        tag = candidate_tag.strip()
        if not tag:
            return tag
        if "/" in tag:
            return tag
        if output_prefix:
            return f"{output_prefix}/{tag}"
        return tag

    def _resolve_parent_candidate_ids(
        self,
        current_version: str | None,
        output_prefix: str | None,
    ) -> list[str]:
        """Resolve parent candidate IDs from the current promoted version."""
        if not current_version:
            return []
        version_leaf = current_version.rsplit("/", 1)[-1]
        if _CANDIDATE_TAG_RE.match(version_leaf):
            return [self._candidate_id_for_tag(version_leaf, output_prefix)]

        scoped_dir = self._scoped_optimized_dir(output_prefix)
        metadata_file = scoped_dir / version_leaf / "metadata.json"
        if not metadata_file.exists():
            return [current_version]
        try:
            metadata = json.loads(metadata_file.read_text())
        except (json.JSONDecodeError, OSError, ValueError):
            return [current_version]

        lineage = metadata.get("lineage", {})
        if isinstance(lineage, dict):
            lineage_dict = cast("dict[str, Any]", lineage)
            selected = str(lineage_dict.get("selected_candidate_id") or "").strip()
            if selected:
                return [self._candidate_id_for_tag(selected, output_prefix)]

        iter_num = metadata.get("iteration")
        best_idx = metadata.get("best_candidate_index")
        if isinstance(iter_num, int) and isinstance(best_idx, int):
            return [self._candidate_id_for_tag(f"eval_{iter_num}_c{best_idx}", output_prefix)]

        return [current_version]

    @staticmethod
    def _sha256_file(path: Path) -> str:
        digest = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _hash_candidate_modules(
        self,
        candidate_version: str,
        prompt_names: list[str],
        destination_dir: Path | None = None,
    ) -> dict[str, str]:
        """Compute per-prompt module hashes for candidate artifacts."""
        if destination_dir is None:
            base_dir = self.optimized_dir / candidate_version
        else:
            base_dir = destination_dir
        hashes: dict[str, str] = {}
        for prompt_name in prompt_names:
            module_file = base_dir / f"{prompt_name}.dspy.json"
            if module_file.exists():
                hashes[prompt_name] = self._sha256_file(module_file)
        return hashes

    def _compute_training_trajectories_fingerprint(
        self,
        work_dir: Path,
        iteration: int,
    ) -> dict[str, Any]:
        """Fingerprint trajectory files used as optimization evidence."""
        digest = hashlib.sha256()
        files: list[Path] = []
        if work_dir.exists():
            for child in sorted(work_dir.iterdir()):
                if not child.is_dir():
                    continue
                match = _TRAIN_RUN_RE.match(child.name)
                if not match:
                    continue
                run_iter = int(match.group(1))
                if run_iter > iteration:
                    continue
                traj_dir = child / ".sds" / "trajectories"
                if not traj_dir.exists():
                    continue
                files.extend(sorted(traj_dir.glob("trajectory_*.json")))

        for file_path in files:
            rel = str(file_path.relative_to(work_dir))
            digest.update(rel.encode("utf-8"))
            digest.update(b"\0")
            with open(file_path, "rb") as f:
                for chunk in iter(lambda: f.read(65536), b""):
                    digest.update(chunk)

        return {
            "sha256": digest.hexdigest(),
            "count": len(files),
            "sample_files": [str(path.relative_to(work_dir)) for path in files[:10]],
        }

    def _append_lineage_event(
        self,
        *,
        output_prefix: str | None,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        """Append one lineage event to family-scoped lineage.jsonl."""
        lineage_path = self._scoped_optimized_dir(output_prefix) / "lineage.jsonl"
        lineage_path.parent.mkdir(parents=True, exist_ok=True)
        event = {
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "event_type": event_type,
            "payload": payload,
        }
        with open(lineage_path, "a") as f:
            f.write(json.dumps(event, sort_keys=True) + "\n")

    def _scoped_optimized_dir(self, output_prefix: str | None) -> Path:
        """Return optimized directory root scoped by optional output prefix."""
        if output_prefix:
            return self.optimized_dir / output_prefix
        return self.optimized_dir

    def _write_candidate(self, candidate: dict[str, str], version: str) -> None:
        """Write .dspy.json files for all prompts in *candidate* to *version* dir."""
        version_dir = self.optimized_dir / version
        version_dir.mkdir(parents=True, exist_ok=True)
        for prompt_name, instruction in candidate.items():
            sig = get_signature(prompt_name)
            state: dict[str, Any] = {
                "prompt_name": prompt_name,
                "signature": sig.__name__,
                "demos": [],
                "optimized_instruction": instruction,
            }
            (version_dir / f"{prompt_name}.dspy.json").write_text(json.dumps(state, indent=2))

    def _clean_exp_dir(self, exp_dir: Path) -> None:
        """Remove .git and .sds from a copied experiment dir and re-init git."""
        git_dir = exp_dir / ".git"
        if git_dir.exists():
            if git_dir.is_dir():
                shutil.rmtree(git_dir)
            else:
                git_dir.unlink()
        sds_dir = exp_dir / ".sds"
        if sds_dir.exists():
            shutil.rmtree(sds_dir)
        subprocess.run(
            ["git", "init", "-b", "main"],
            cwd=exp_dir,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def _cleanup_experiment_containers(self, exp_dir: Path) -> None:
        """Stop and remove any Docker containers left over from a previous run of exp_dir.

        Uses the docker compose project label (com.docker.compose.project) to find
        containers belonging to this experiment slot and force-removes them.
        """
        project_names = self._candidate_project_names(exp_dir.name)
        try:
            compose_file = self._find_compose_file(exp_dir)
            if compose_file:
                for project_name in project_names:
                    self._run_compose_down(project_name, compose_file)

            container_ids: set[str] = set()
            for project_name in project_names:
                ids = self._list_compose_container_ids(project_name)
                container_ids.update(ids)

            if container_ids:
                logger.info(
                    f"[EvalExecute] Removing {len(container_ids)} leftover containers for projects {project_names}..."
                )
                subprocess.run(
                    ["docker", "rm", "-f"] + sorted(container_ids),
                    capture_output=True,
                    text=True,
                )
                logger.info(f"[EvalExecute] Cleaned up containers for {project_names}")
        except (OSError, subprocess.SubprocessError) as e:
            logger.warning(f"[EvalExecute] Could not clean up containers for {project_names}: {e}")

    @staticmethod
    def _run_compose_down(project_name: str, compose_file: Path) -> None:
        """Run ``docker compose down`` for a given project and compose file."""
        cmd = [
            "docker",
            "compose",
            "--project-name",
            project_name,
            "-f",
            str(compose_file),
            "down",
            "--remove-orphans",
            "--timeout",
            "0",
        ]
        subprocess.run(cmd, capture_output=True, text=True)

    @staticmethod
    def _list_compose_container_ids(project_name: str) -> list[str]:
        """Return container IDs belonging to *project_name* via docker ps filter."""
        cmd = [
            "docker",
            "ps",
            "-a",
            "-q",
            "--filter",
            f"label=com.docker.compose.project={project_name}",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        return [cid for cid in result.stdout.strip().splitlines() if cid]

    @staticmethod
    def _candidate_project_names(exp_dir_name: str) -> list[str]:
        """Return plausible Docker Compose project names for an experiment slot."""
        raw = exp_dir_name.lower()
        normalized = re.sub(r"[^a-z0-9_-]", "", raw)
        hyphenized = normalized.replace("_", "-")
        names: list[str] = []
        for name in (normalized, hyphenized):
            if name and name not in names:
                names.append(name)
        return names

    @staticmethod
    def _find_compose_file(exp_dir: Path) -> Path | None:
        """Locate a compose file in an experiment directory."""
        for candidate in ("docker-compose.yml", "compose.yml", "compose.yaml"):
            compose_file = exp_dir / candidate
            if compose_file.exists():
                return compose_file
        return None

    def _score_rlm_trajectory(self, exp_dir: Path) -> float | None:
        """Extract RLM efficiency score from a run's trajectory, if available.

        Returns a score between 0.0 and 1.0, or None if no RLM data was found.
        """
        sds_dir = exp_dir / ".sds"
        traj_dir = sds_dir / "trajectories"
        if not traj_dir.exists():
            return None

        # Find the most recent trajectory file
        traj_files = sorted(traj_dir.glob("trajectory_*.json"), reverse=True)
        if not traj_files:
            return None

        try:
            traj_data = json.loads(traj_files[0].read_text())
            stats = extract_rlm_statistics_from_trajectory(traj_data)

            if stats.get("total_calls", 0) == 0:
                return None  # Not an RLM run

            # Score based on RLM efficiency (same logic as RLMEfficiencyMetric)
            total_calls = stats.get("total_calls", 0)
            code_execs = stats.get("code_executions", 0)
            recursive = stats.get("recursive_calls", 0)
            max_depth = stats.get("max_depth_reached", 0)

            # Calls score: prefer 2-10 calls
            calls_score = min(1.0, total_calls / 10) if total_calls <= 10 else max(0.0, 1.0 - (total_calls - 10) / 10)

            # Depth score: shallow is better
            depth_score = 1.0 if max_depth <= 3 else max(0.0, 1.0 - (max_depth - 3) / 3)

            # Ratio score: prefer more code than recursive calls
            if recursive > 0:
                ratio_score = min(1.0, (code_execs / recursive) / 2.0)
            else:
                ratio_score = 1.0 if code_execs > 0 else 0.5

            return calls_score * 0.4 + depth_score * 0.3 + ratio_score * 0.3

        except (json.JSONDecodeError, OSError, KeyError, ValueError) as e:
            logger.warning(f"[EvalExecute] Failed to score RLM trajectory: {e}")
            return None

    def _write_sds_toml(
        self,
        app_dir: Path,
        optimized_version: str,
        provider_override: str | None = None,
        model_override: str | None = None,
    ) -> None:
        """Write (or update) sds.toml in *app_dir* to use *optimized_version*.

        When *provider_override* or *model_override* is given, the
        corresponding value inside the ``[agent]`` section is replaced so
        the experiment directory uses the specified setting regardless of
        what the root ``sds.toml`` says.
        """
        sds_toml = app_dir / "sds.toml"

        # Prefer existing local config; fall back to project-root config
        content = ""
        if sds_toml.exists():
            content = sds_toml.read_text()
        else:
            global_sds = self.project_root / "sds.toml"
            if global_sds.exists():
                content = global_sds.read_text()

        # Strip existing [dspy] section
        if "[dspy]" in content:
            lines = content.splitlines()
            new_lines: list[str] = []
            skip = False
            for line in lines:
                if line.strip() == "[dspy]":
                    skip = True
                    continue
                if skip and line.strip().startswith("["):
                    skip = False
                if not skip:
                    new_lines.append(line)
            content = "\n".join(new_lines)

        def _upsert_agent_setting(current: str, key: str, value: str) -> str:
            """Replace key in [agent]; insert it when missing."""
            assignment = f'{key} = "{value}"'
            lines = current.splitlines()
            agent_start = None
            agent_end = len(lines)
            key_pattern = re.compile(rf"^(\s*{re.escape(key)}\s*=\s*).*$")

            for idx, line in enumerate(lines):
                if line.strip() == "[agent]":
                    agent_start = idx
                    for end_idx in range(idx + 1, len(lines)):
                        if lines[end_idx].strip().startswith("["):
                            agent_end = end_idx
                            break
                    break

            if agent_start is None:
                if current and not current.endswith("\n"):
                    current += "\n"
                return current + f"\n[agent]\n{assignment}\n"

            for idx in range(agent_start + 1, agent_end):
                match = key_pattern.match(lines[idx])
                if match:
                    lines[idx] = f'{match.group(1)}"{value}"'
                    return "\n".join(lines)

            lines.insert(agent_end, assignment)
            return "\n".join(lines)

        # Override provider if requested
        if provider_override:
            content = _upsert_agent_setting(content, "provider", provider_override)

        # Override model if requested
        if model_override:
            content = _upsert_agent_setting(content, "model", model_override)

        dspy_section = f'\n[dspy]\nuse_optimized = true\noptimized_version = "{optimized_version}"\n'
        sds_toml.write_text(content + dspy_section)
