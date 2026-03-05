"""Eval-execute optimizer for SDS prompt optimization.

Implements the eval-execute loop:
1. Generate n_candidates instruction variants for each prompt using the teacher LLM.
2. Evaluate each candidate by running the SDS operator on training apps.
3. Keep the best-scoring candidate based on real deployment outcomes.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import litellm

from app_operator.dspy_integration.config import DSPyConfig
from app_operator.dspy_integration.signatures import SIGNATURES, get_signature
from app_operator.experiment_naming import normalize_experiment_token
from app_operator.logger import logger
from app_operator.rate_limit_handler import run_subprocess_with_rate_limit_handling
from app_operator.rlm.metrics import extract_rlm_statistics_from_trajectory

_TRAIN_RUN_RE = re.compile(r"^iter(\d+)_c\d+_(.+)$")
_VAL_RUN_RE = re.compile(r"^(.+)_iter(\d+)_val$")
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


@dataclass
class _TrajectoryEvidence:
    """Compressed evidence extracted from one experiment trajectory."""

    run_name: str
    app_name: str
    status: str
    attempts: int
    trajectory_insights: list[str] = field(default_factory=list)
    error_insights: list[str] = field(default_factory=list)
    script_insights: list[str] = field(default_factory=list)
    repo_insights: list[str] = field(default_factory=list)
    error_signals: list[str] = field(default_factory=list)
    weight: float = 1.0


class EvalExecuteOptimizer:
    """Optimize prompts by generating candidate instructions and evaluating them live.

    Strategy (eval-execute loop):
    1. Generate n_candidates instruction variants per prompt using the teacher LLM.
    2. For each candidate set, run the SDS operator on every training app.
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

        # 1. Load current instructions for all prompts
        current_instructions: dict[str, str] = {}
        for pname in prompt_names:
            current_instructions[pname] = self._load_current_instruction(pname, current_version)
            logger.info(
                f"[EvalExecute] Current instruction for {pname}: "
                f"{current_instructions[pname][:120]}..."
            )

        trajectory_context = self._build_recent_trajectory_context(
            work_dir=work_dir,
            iteration=iteration,
            prompt_names=prompt_names,
        )

        # Cold start bootstrap: when there is no optimized baseline and no
        # trajectory evidence yet, run a single seed candidate first to collect
        # real execution data before asking the teacher LM for variants.
        has_evidence = any(
            bool(context.strip()) for context in trajectory_context.values()
        )
        if current_version is None and not has_evidence:
            logger.info(
                "[EvalExecute] Cold start detected (no prior trajectory evidence). "
                "Running seed-only bootstrap candidate."
            )
            candidates = [dict(current_instructions)]
        else:
            logger.info(
                "[EvalExecute] Generating %d candidate variants per prompt...",
                self.n_candidates,
            )
            candidates = self._generate_candidates(
                prompt_names, current_instructions, trajectory_context
            )
        logger.info(f"[EvalExecute] Generated {len(candidates)} candidates")

        # 3. Evaluate each candidate by running the operator on all training apps
        scores: list[float] = []
        candidate_summaries: list[dict[str, Any]] = []
        for c_idx, candidate in enumerate(candidates):
            logger.info(
                f"[EvalExecute] Evaluating candidate {c_idx + 1}/{len(candidates)}..."
            )

            candidate_tag = f"eval_{iteration}_c{c_idx + 1}"
            if output_prefix:
                candidate_version = f"{output_prefix}/{candidate_tag}"
            else:
                candidate_version = candidate_tag
            self._write_candidate(candidate, candidate_version)

            successful_runs = 0
            total_runs = 0
            rlm_scores: list[float] = []
            run_outcomes: list[dict[str, Any]] = []
            for app_path in train_apps:
                app_name = normalize_experiment_token(app_path.name)
                exp_dir = work_dir / f"iter{iteration}_c{c_idx + 1}_{app_name}"

                self._cleanup_experiment_containers(exp_dir)
                if exp_dir.exists():
                    shutil.rmtree(exp_dir)
                shutil.copytree(app_path, exp_dir)
                self._clean_exp_dir(exp_dir)
                self._write_sds_toml(
                    exp_dir, candidate_version,
                    provider_override=provider_override,
                    model_override=model_override,
                )

                logger.info(f"[EvalExecute] Running operator on {exp_dir.name}...")
                cmd = [sys.executable, "-m", "app_operator", "run", str(exp_dir)]
                _result, success, error_msg = run_subprocess_with_rate_limit_handling(
                    cmd=cmd,
                    provider=provider,
                    max_retries=max_retries,
                    rate_limit_backoff=rate_limit_backoff,
                    operation_name=f"candidate_{c_idx + 1}_{app_name}",
                )
                total_runs += 1
                if success:
                    successful_runs += 1
                else:
                    logger.warning(
                        f"[EvalExecute] Run failed for {exp_dir.name}: {error_msg}"
                    )

                # Collect RLM efficiency score from trajectory if available
                rlm_score = self._score_rlm_trajectory(exp_dir)
                if rlm_score is not None:
                    rlm_scores.append(rlm_score)
                run_outcomes.append(
                    {
                        "app_name": app_name,
                        "success": success,
                        "error": error_msg or "",
                        "rlm_score": rlm_score,
                    }
                )

                # Stop containers immediately so they don't hold ports
                self._cleanup_experiment_containers(exp_dir)

                if inter_run_delay > 0:
                    time.sleep(inter_run_delay)

            success_rate = successful_runs / total_runs if total_runs > 0 else 0.0
            # Blend RLM efficiency into the score when available (10% weight)
            if rlm_scores:
                avg_rlm = sum(rlm_scores) / len(rlm_scores)
                score = 0.9 * success_rate + 0.1 * avg_rlm
                logger.info(
                    f"[EvalExecute] Candidate {c_idx + 1} RLM efficiency: {avg_rlm:.2f}"
                )
            else:
                score = success_rate
            scores.append(score)
            candidate_summaries.append(
                {
                    "candidate_index": c_idx + 1,
                    "score": score,
                    "success_rate": success_rate,
                    "successful_runs": successful_runs,
                    "total_runs": total_runs,
                    "avg_rlm_score": (
                        sum(rlm_scores) / len(rlm_scores) if rlm_scores else None
                    ),
                    "run_outcomes": run_outcomes,
                }
            )
            logger.info(
                f"[EvalExecute] Candidate {c_idx + 1} score: {score:.2f} "
                f"({successful_runs}/{total_runs} runs succeeded)"
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
        if output_prefix:
            best_version = f"{output_prefix}/eval_{iteration}_c{best_idx + 1}"
        else:
            best_version = f"eval_{iteration}_c{best_idx + 1}"

        logger.info(
            f"[EvalExecute] Best candidate: {best_idx + 1} with score {best_score:.2f} "
            f"(selection_mode={selection_info['selection_mode']})"
        )

        # 5. Copy winning candidate to output_dir
        src = self.optimized_dir / best_version
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        for f in src.iterdir():
            shutil.copy2(f, output_dir / f.name)

        metadata = {
            "version": output_dir.name,
            "method": "eval_execute",
            "iteration": iteration,
            "n_candidates": len(candidates),
            "best_candidate_index": best_idx + 1,
            "best_score": best_score,
            "all_scores": scores,
            "selection": selection_info,
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

    def _load_current_instruction(
        self, prompt_name: str, current_version: str | None
    ) -> str:
        """Return the instruction string currently used for *prompt_name*."""
        if current_version:
            from app_operator.dspy_integration.loader import resolve_version

            resolved = resolve_version(self.optimized_dir, current_version)
            if resolved:
                module_file = self.optimized_dir / resolved / f"{prompt_name}.dspy.json"
                if module_file.exists():
                    state = json.loads(module_file.read_text())
                    if state.get("optimized_instruction"):
                        return state["optimized_instruction"]

        return self._seed_instruction(prompt_name)

    def _seed_instruction(self, prompt_name: str) -> str:
        """Return a minimal seed instruction for *prompt_name*."""
        defaults = {
            "deployer_fix_error": (
                "A deployment has failed. Analyze the error and fix the deployment scripts."
            ),
            "deployer_summarize": (
                "Summarize the deployment outcome and any issues encountered."
            ),
            "deployer_system": (
                "You are a deployment assistant. Help deploy applications correctly."
            ),
            "deployer_generate_script": "Generate a deployment script for the application.",
            "deployer_generate_deploy_script": "Generate a deploy.sh script for the application.",
            "deployer_generate_health_check": (
                "Generate a health_check.sh script for the application."
            ),
            "code_analyzer_system": (
                "Analyze the codebase and identify deployment requirements."
            ),
            "code_analyzer_user": "Identify potential deployment issues in the codebase.",
            "monitor_analyze_health": (
                "Analyze the health check results and report on application status."
            ),
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
        summary_by_index = {
            int(s["candidate_index"]) - 1: s for s in candidate_summaries
        }
        sections: list[str] = []
        for idx in candidate_pool:
            summary = summary_by_index.get(idx, {})
            run_outcomes = summary.get("run_outcomes", [])
            failures = []
            for outcome in run_outcomes:
                if outcome.get("success"):
                    continue
                error = str(outcome.get("error") or "unknown failure").strip()
                if len(error) > 220:
                    error = error[:217].rstrip() + "..."
                failures.append(f"{outcome.get('app_name', 'unknown')}: {error}")
                if len(failures) >= 3:
                    break

            prompt_preview = candidates[idx].get(prompt_names[0], "")
            prompt_preview = " ".join(prompt_preview.split())
            if len(prompt_preview) > 180:
                prompt_preview = prompt_preview[:177].rstrip() + "..."

            section_lines = [
                f"Candidate {idx + 1}:",
                f"- score: {summary.get('score', 0.0):.4f}",
                (
                    "- success_rate: "
                    f"{summary.get('successful_runs', 0)}/{summary.get('total_runs', 0)} "
                    f"({summary.get('success_rate', 0.0):.4f})"
                ),
                (
                    "- avg_rlm_score: "
                    f"{summary.get('avg_rlm_score'):.4f}"
                    if summary.get("avg_rlm_score") is not None
                    else "- avg_rlm_score: n/a"
                ),
                f"- instruction_preview ({prompt_names[0]}): {prompt_preview}",
            ]
            if failures:
                section_lines.append("- key_failures:")
                for failure in failures:
                    section_lines.append(f"  - {failure}")
            else:
                section_lines.append("- key_failures: none")
            sections.append("\n".join(section_lines))

        model = self._normalize_model_name(self.config.optimization.teacher_model)
        system_msg = (
            "You are selecting the strongest prompt candidate for an SDS deployment agent. "
            "Prioritize objective runtime outcomes over style. "
            "Use score and success_rate as primary signals, and failures for tie-breaking."
        )
        user_msg = (
            "Choose exactly one candidate from the pool below.\n"
            "Return ONLY JSON: "
            "{\"chosen_candidate\": <integer>, \"reason\": \"<brief>\", \"confidence\": <0_to_1>}.\n\n"
            f"Candidate pool: {[idx + 1 for idx in candidate_pool]}\n\n"
            + "\n\n".join(sections)
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
            response = litellm.completion(**kwargs)
            raw = response.choices[0].message.content or ""
            llm_info["llm_raw"] = raw
            json_match = re.search(r"\{.*\}", raw, re.DOTALL)
            if not json_match:
                llm_info["llm_reason"] = "Judge response did not contain JSON."
                return None, llm_info
            parsed = json.loads(json_match.group())
            chosen = int(parsed.get("chosen_candidate"))
            if chosen - 1 not in candidate_pool:
                llm_info["llm_reason"] = (
                    f"Judge selected candidate {chosen} outside pool "
                    f"{[idx + 1 for idx in candidate_pool]}."
                )
                return None, llm_info
            llm_info["llm_choice"] = chosen
            llm_info["llm_reason"] = str(parsed.get("reason", "")).strip()
            confidence = parsed.get("confidence")
            if isinstance(confidence, (int, float)):
                llm_info["llm_confidence"] = max(0.0, min(1.0, float(confidence)))
            return chosen - 1, llm_info
        except Exception as e:
            llm_info["llm_reason"] = f"Judge call failed: {e}"
            return None, llm_info

    def _generate_candidates(
        self,
        prompt_names: list[str],
        current_instructions: dict[str, str],
        trajectory_context: dict[str, str] | None = None,
    ) -> list[dict[str, str]]:
        """Generate self.n_candidates candidate dicts (prompt_name -> instruction)."""
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

        candidates = []
        for c_idx in range(self.n_candidates):
            candidate: dict[str, str] = {}
            for pname in prompt_names:
                variants = per_prompt[pname]
                candidate[pname] = (
                    variants[c_idx] if c_idx < len(variants) else current_instructions[pname]
                )
            candidates.append(candidate)
        return candidates

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
            "Return ONLY a JSON array of strings, e.g. [\"variant1\", \"variant2\"]. "
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
            response = litellm.completion(**kwargs)
            raw = response.choices[0].message.content or ""
            json_match = re.search(r"\[.*?\]", raw, re.DOTALL)
            if json_match:
                variants = json.loads(json_match.group())
                if isinstance(variants, list) and variants:
                    while len(variants) < n:
                        variants.append(current_instruction)
                    return [str(v) for v in variants[:n]]
        except Exception as e:
            logger.warning(
                f"[EvalExecute] Failed to generate variants for {prompt_name}: {e}"
            )

        # Fallback: repeat the current instruction
        return [current_instruction] * n

    def _build_recent_trajectory_context(
        self,
        work_dir: Path,
        iteration: int,
        prompt_names: list[str],
    ) -> dict[str, str]:
        """Build per-prompt trajectory context from recent experiment runs."""
        evidences = self._collect_recent_trajectory_evidence(work_dir, iteration)
        if not evidences:
            return {pname: "" for pname in prompt_names}

        run_summaries = self._rank_weighted_notes([
            (
                e.weight,
                f"{e.app_name} via {e.run_name}: status={e.status}, attempts={e.attempts}",
            )
            for e in evidences
        ])
        trajectory_notes = self._rank_weighted_notes([
            (e.weight, note) for e in evidences for note in e.trajectory_insights
        ])
        error_notes = self._rank_weighted_notes([
            (e.weight, note)
            for e in evidences
            for note in (e.error_insights + e.error_signals)
        ])
        script_notes = self._rank_weighted_notes([
            (e.weight, note) for e in evidences for note in e.script_insights
        ])
        repo_notes = self._rank_weighted_notes([
            (e.weight, note) for e in evidences for note in e.repo_insights
        ])

        context_by_prompt: dict[str, str] = {}
        for prompt_name in prompt_names:
            if prompt_name == "subagent_trajectory_analyst":
                context = self._format_context_sections([
                    ("Recent run outcomes", run_summaries),
                    ("Trajectory analyst findings", trajectory_notes),
                    ("Recurring error patterns", error_notes),
                ])
            elif prompt_name == "subagent_error_log_analyst":
                context = self._format_context_sections([
                    ("Recent run outcomes", run_summaries),
                    ("Error-log analyst findings", error_notes),
                ])
            elif prompt_name == "subagent_script_analyst":
                context = self._format_context_sections([
                    ("Recent run outcomes", run_summaries),
                    ("Script analyst findings", script_notes),
                    ("Recurring error patterns", error_notes),
                ])
            elif prompt_name == "subagent_repo_analyst":
                context = self._format_context_sections([
                    ("Recent run outcomes", run_summaries),
                    ("Repository analyst findings", repo_notes),
                    ("Recurring error patterns", error_notes),
                ])
            elif prompt_name == "subagent_root_synthesis":
                context = self._format_context_sections([
                    ("Recent run outcomes", run_summaries),
                    ("Trajectory findings", trajectory_notes),
                    ("Error findings", error_notes),
                    ("Script findings", script_notes),
                    ("Repository findings", repo_notes),
                ])
            else:
                context = self._format_context_sections([
                    ("Recent run outcomes", run_summaries),
                    ("Recurring error patterns", error_notes),
                    ("Script findings", script_notes),
                ])

            if context:
                logger.info(
                    "[EvalExecute] Using {} chars of trajectory context for {}",
                    len(context),
                    prompt_name,
                )
            context_by_prompt[prompt_name] = context

        return context_by_prompt

    def _collect_recent_trajectory_evidence(
        self,
        work_dir: Path,
        iteration: int,
        max_runs: int = 8,
    ) -> list[_TrajectoryEvidence]:
        """Collect compressed evidence from recent training-run trajectories.

        Validation runs are intentionally excluded to avoid leaking validation
        outcomes back into subsequent training-time prompt generation.
        """
        if not work_dir.exists():
            return []

        candidate_dirs: list[Path] = []
        for child in work_dir.iterdir():
            if not child.is_dir():
                continue
            if not _TRAIN_RUN_RE.match(child.name):
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
            trajectory = json.loads(traj_files[0].read_text())
        except Exception as e:
            logger.warning(
                f"[EvalExecute] Failed to parse trajectory {traj_files[0]}: {e}"
            )
            return None

        _iter, app_name = self._parse_run_dir_name(run_dir.name)
        deployment = trajectory.get("deployment", [])
        evidence = _TrajectoryEvidence(
            run_name=run_dir.name,
            app_name=app_name,
            status=str(trajectory.get("metadata", {}).get("status", "unknown")),
            attempts=len(deployment),
        )

        for convo in deployment:
            for msg in convo.get("messages", []):
                role = msg.get("role")
                content = str(msg.get("content") or "")

                if role == "assistant" and content:
                    lower = content.lower()
                    snippet = self._extract_analysis_snippet(content)
                    if not snippet:
                        continue

                    if (
                        "[subagent trajectory analyst]" in lower
                        or "[hybrid pre-analysis: trajectory]" in lower
                    ):
                        evidence.trajectory_insights.append(snippet)
                    elif (
                        "[subagent error_log analyst]" in lower
                        or "[hybrid pre-analysis: error_log]" in lower
                    ):
                        evidence.error_insights.append(snippet)
                    elif (
                        "[subagent script analyst]" in lower
                        or "[hybrid pre-analysis: script]" in lower
                        or "auto-validation warning" in lower
                    ):
                        evidence.script_insights.append(snippet)
                    elif (
                        "[subagent repo analyst]" in lower
                        or "[hybrid pre-analysis: repo]" in lower
                    ):
                        evidence.repo_insights.append(snippet)

                if role == "tool_call":
                    for field in ("stderr", "stdout"):
                        evidence.error_signals.extend(
                            self._extract_error_signal_lines(str(msg.get(field) or ""))
                        )

        return evidence

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
        max_chars: int = 2200,
    ) -> str:
        """Render compact context text from titled evidence sections."""
        lines: list[str] = []
        for title, items in sections:
            chosen = self._dedupe_limit(items)
            if not chosen:
                continue
            lines.append(f"{title}:")
            lines.extend(f"- {item}" for item in chosen)

        if not lines:
            return ""

        text = "\n".join(lines)
        if len(text) > max_chars:
            return text[: max_chars - 3].rstrip() + "..."
        return text

    def _write_candidate(self, candidate: dict[str, str], version: str) -> None:
        """Write .dspy.json files for all prompts in *candidate* to *version* dir."""
        version_dir = self.optimized_dir / version
        version_dir.mkdir(parents=True, exist_ok=True)
        for prompt_name, instruction in candidate.items():
            sig = get_signature(prompt_name)
            state = {
                "prompt_name": prompt_name,
                "signature": sig.__name__,
                "demos": [],
                "optimized_instruction": instruction,
            }
            (version_dir / f"{prompt_name}.dspy.json").write_text(
                json.dumps(state, indent=2)
            )

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
                    subprocess.run(
                        [
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
                        ],
                        capture_output=True,
                        text=True,
                    )

            container_ids: set[str] = set()
            for project_name in project_names:
                result = subprocess.run(
                    [
                        "docker", "ps", "-a", "-q",
                        "--filter", f"label=com.docker.compose.project={project_name}",
                    ],
                    capture_output=True,
                    text=True,
                )
                for container_id in result.stdout.strip().splitlines():
                    if container_id:
                        container_ids.add(container_id)

            if container_ids:
                logger.info(
                    f"[EvalExecute] Removing {len(container_ids)} leftover containers "
                    f"for projects {project_names}..."
                )
                subprocess.run(
                    ["docker", "rm", "-f"] + sorted(container_ids),
                    capture_output=True,
                    text=True,
                )
                logger.info(f"[EvalExecute] Cleaned up containers for {project_names}")
        except Exception as e:
            logger.warning(
                f"[EvalExecute] Could not clean up containers for {project_names}: {e}"
            )

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
            calls_score = min(1.0, total_calls / 10) if total_calls <= 10 else max(
                0.0, 1.0 - (total_calls - 10) / 10
            )

            # Depth score: shallow is better
            depth_score = 1.0 if max_depth <= 3 else max(0.0, 1.0 - (max_depth - 3) / 3)

            # Ratio score: prefer more code than recursive calls
            if recursive > 0:
                ratio_score = min(1.0, (code_execs / recursive) / 2.0)
            else:
                ratio_score = 1.0 if code_execs > 0 else 0.5

            return calls_score * 0.4 + depth_score * 0.3 + ratio_score * 0.3

        except Exception as e:
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

        # Override provider if requested
        if provider_override:
            content = re.sub(
                r'^(\s*provider\s*=\s*).*$',
                f'\\1"{provider_override}"',
                content,
                count=1,
                flags=re.MULTILINE,
            )

        # Override model if requested
        if model_override:
            content = re.sub(
                r'^(\s*model\s*=\s*).*$',
                f'\\1"{model_override}"',
                content,
                count=1,
                flags=re.MULTILINE,
            )

        dspy_section = (
            "\n[dspy]\n"
            "use_optimized = true\n"
            f'optimized_version = "{optimized_version}"\n'
        )
        sds_toml.write_text(content + dspy_section)
