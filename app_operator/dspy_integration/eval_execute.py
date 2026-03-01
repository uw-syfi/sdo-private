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
from pathlib import Path
from typing import Any, Dict, List, Optional

import litellm

from app_operator.dspy_integration.config import DSPyConfig
from app_operator.dspy_integration.signatures import SIGNATURES, get_signature
from app_operator.logger import logger
from app_operator.rate_limit_handler import run_subprocess_with_rate_limit_handling
from app_operator.rlm.metrics import extract_rlm_statistics_from_trajectory


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
        vertex_location: Optional[str] = None,
    ):
        self.config = config
        self.prompts_dir = Path(prompts_dir)
        self.optimized_dir = self.prompts_dir / "optimized"
        self.project_root = Path(project_root)
        self.n_candidates = n_candidates
        self.vertex_location = vertex_location

    def optimize(
        self,
        prompt_names: List[str],
        train_apps: List[Path],
        work_dir: Path,
        output_dir: Path,
        iteration: int,
        current_version: Optional[str],
        provider: str,
        max_retries: int = 3,
        rate_limit_backoff: int = 60,
        inter_run_delay: int = 30,
        provider_override: Optional[str] = None,
        model_override: Optional[str] = None,
        output_prefix: Optional[str] = None,
    ) -> Dict[str, Any]:
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
        current_instructions: Dict[str, str] = {}
        for pname in prompt_names:
            current_instructions[pname] = self._load_current_instruction(pname, current_version)
            logger.info(
                f"[EvalExecute] Current instruction for {pname}: "
                f"{current_instructions[pname][:120]}..."
            )

        # 2. Generate n_candidates instruction variants per prompt
        logger.info(
            f"[EvalExecute] Generating {self.n_candidates} candidate variants per prompt..."
        )
        candidates = self._generate_candidates(prompt_names, current_instructions)
        logger.info(f"[EvalExecute] Generated {len(candidates)} candidates")

        # 3. Evaluate each candidate by running the operator on all training apps
        scores: List[float] = []
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
            rlm_scores: List[float] = []
            for app_path in train_apps:
                app_name = app_path.name
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
            logger.info(
                f"[EvalExecute] Candidate {c_idx + 1} score: {score:.2f} "
                f"({successful_runs}/{total_runs} runs succeeded)"
            )

        if not scores:
            return {"success": False, "error": "No candidates were evaluated"}

        # 4. Pick the best candidate
        best_idx = max(range(len(scores)), key=lambda i: scores[i])
        best_score = scores[best_idx]
        best_candidate = candidates[best_idx]
        if output_prefix:
            best_version = f"{output_prefix}/eval_{iteration}_c{best_idx + 1}"
        else:
            best_version = f"eval_{iteration}_c{best_idx + 1}"

        logger.info(
            f"[EvalExecute] Best candidate: {best_idx + 1} with score {best_score:.2f}"
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
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_current_instruction(
        self, prompt_name: str, current_version: Optional[str]
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

    def _generate_candidates(
        self,
        prompt_names: List[str],
        current_instructions: Dict[str, str],
    ) -> List[Dict[str, str]]:
        """Generate self.n_candidates candidate dicts (prompt_name -> instruction)."""
        per_prompt: Dict[str, List[str]] = {}
        for pname in prompt_names:
            per_prompt[pname] = self._generate_instruction_variants(
                pname, current_instructions[pname], self.n_candidates
            )

        candidates = []
        for c_idx in range(self.n_candidates):
            candidate: Dict[str, str] = {}
            for pname in prompt_names:
                variants = per_prompt[pname]
                candidate[pname] = (
                    variants[c_idx] if c_idx < len(variants) else current_instructions[pname]
                )
            candidates.append(candidate)
        return candidates

    def _generate_instruction_variants(
        self, prompt_name: str, current_instruction: str, n: int
    ) -> List[str]:
        """Use the teacher LLM to produce *n* instruction variants."""
        model = self.config.optimization.teacher_model
        if "/" not in model:
            if "claude" in model.lower():
                model = f"anthropic/{model}"
            elif "gemini" in model.lower():
                model = f"gemini/{model}"
            elif "gpt" in model.lower() or "o1" in model.lower():
                model = f"openai/{model}"

        system_msg = (
            "You are a prompt optimization expert helping improve AI deployment agent "
            "instructions to increase deployment success rates."
        )
        user_msg = (
            f"Current instruction for the '{prompt_name}' step:\n"
            f"{current_instruction}\n\n"
            f"Generate exactly {n} improved variants. "
            "Return ONLY a JSON array of strings, e.g. [\"variant1\", \"variant2\"]. "
            "Each variant should be 1-3 sentences and focus on clearer guidance for "
            "fixing deployment errors."
        )

        kwargs: Dict[str, Any] = {
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

    def _write_candidate(self, candidate: Dict[str, str], version: str) -> None:
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
        project_name = exp_dir.name.lower()
        try:
            result = subprocess.run(
                [
                    "docker", "ps", "-a", "-q",
                    "--filter", f"label=com.docker.compose.project={project_name}",
                ],
                capture_output=True,
                text=True,
            )
            container_ids = [c for c in result.stdout.strip().splitlines() if c]
            if container_ids:
                logger.info(
                    f"[EvalExecute] Removing {len(container_ids)} leftover containers "
                    f"for project '{project_name}'..."
                )
                subprocess.run(
                    ["docker", "rm", "-f"] + container_ids,
                    capture_output=True,
                    text=True,
                )
                logger.info(f"[EvalExecute] Cleaned up containers for '{project_name}'")
        except Exception as e:
            logger.warning(
                f"[EvalExecute] Could not clean up containers for '{project_name}': {e}"
            )

    def _score_rlm_trajectory(self, exp_dir: Path) -> Optional[float]:
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
        provider_override: Optional[str] = None,
        model_override: Optional[str] = None,
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
            new_lines: List[str] = []
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
