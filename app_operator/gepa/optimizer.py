"""Core GEPA optimization loop for SDS prompts."""

import json
import random
import time
import uuid
from pathlib import Path
from typing import IO, Any

from app_operator.config import GEPAConfig
from app_operator.gepa.adapter import SDSPromptAdapter
from app_operator.gepa.candidate import CandidatePool, PromptCandidate
from app_operator.gepa.evaluator import (
    EfficiencyMetrics,
    EvaluationExample,
    EvaluationResult,
    SDSEvaluator,
    extract_generated_scripts,
)
from app_operator.gepa.reflector import ExecutionTrace, PromptReflector
from app_operator.logger import logger


class GEPAOptimizer:
    """Main GEPA optimization loop for SDS prompts.

    Implements the full GEPA algorithm:
    1. Initialize candidate pool with current prompt
    2. Loop:
       a. Pareto-select a candidate
       b. Execute on minibatch
       c. Reflect and mutate (or crossover)
       d. Validate mutation preserves template structure
       e. Evaluate mutation on validation set
       f. Add to pool if improved
       g. Prune dominated candidates
    3. Return best candidate

    Use as a context manager to ensure the log file is closed::

        with GEPAOptimizer(config, adapter, reflector, evaluator) as opt:
            results = opt.optimize(template, train, val)
    """

    def __init__(
        self,
        config: GEPAConfig,
        adapter: SDSPromptAdapter,
        reflector: PromptReflector,
        evaluator: SDSEvaluator,
    ) -> None:
        self.config = config
        self.adapter = adapter
        self.reflector = reflector
        self.evaluator = evaluator
        self._rng = random.Random(config.seed)

        self.run_id = time.strftime("%Y%m%d-%H%M%S")
        self.output_dir = Path(config.output_dir) / self.run_id
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._log_file: IO[str] | None = None

    def _ensure_log_file(self) -> IO[str]:
        """Lazily open the optimization log file."""
        if self._log_file is None:
            self._log_file = (self.output_dir / "optimization.log").open("a")
        return self._log_file

    def close(self) -> None:
        """Close the optimization log file."""
        if self._log_file is not None:
            self._log_file.close()
            self._log_file = None

    def __enter__(self) -> "GEPAOptimizer":
        self._ensure_log_file()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def __del__(self) -> None:
        self.close()

    def optimize(
        self,
        template_name: str,
        train_examples: list[EvaluationExample],
        val_examples: list[EvaluationExample],
    ) -> dict[str, Any]:
        """Run the GEPA optimization loop for a single template."""
        initial_prompt = self.adapter.read_template(template_name)
        initial_candidate = self._make_candidate(
            template_name=template_name,
            prompt_text=initial_prompt,
            mutation_type="initial",
            generation=0,
        )

        pool = CandidatePool()
        pool.add(initial_candidate)

        initial_result = self.evaluator.evaluate(
            initial_prompt,
            template_name,
            val_examples,
            candidate_id=initial_candidate.id,
        )
        initial_candidate.validation_score = initial_result.overall_score
        initial_candidate.scores = initial_result.scores

        self._populate_traces_with_scores(initial_result)

        history = [self._history_entry(0, initial_candidate, "initial", pool)]
        self._log(f"Initial candidate score: {initial_candidate.validation_score:.3f}")
        self._log_metric_breakdown(initial_candidate)

        self._run_evolution_loop(
            pool,
            template_name,
            train_examples,
            val_examples,
            history,
            start_step=1,
        )

        best = pool.get_best()
        self._log(f"\nBest: {best.id} (score={best.validation_score or 0.0:.3f})")

        results = self._build_results(template_name, best, initial_candidate, pool, history)
        self._save_results(results, template_name)
        return results

    def optimize_all(
        self,
        agent_type: str,
        train_examples: list[EvaluationExample],
        val_examples: list[EvaluationExample],
    ) -> dict[str, Any]:
        """Optimize all templates for a given agent type."""
        templates = self.adapter.get_templates_for_agent(agent_type)
        results = {}
        for template_name in templates:
            self._log(f"\n{'=' * 60}\nOptimizing: {template_name}\n{'=' * 60}")
            results[template_name] = self.optimize(template_name, train_examples, val_examples)
        return results

    def resume(
        self,
        run_dir: str,
        train_examples: list[EvaluationExample],
        val_examples: list[EvaluationExample],
    ) -> dict[str, Any]:
        """Resume optimization from a checkpoint.

        Args:
            run_dir: Path to the previous run directory containing checkpoint files.
            train_examples: Training examples for minibatch evaluation.
            val_examples: Validation examples for candidate scoring.

        Returns:
            Dict with optimization results.
        """
        run_path = Path(run_dir)
        checkpoint_files = list(run_path.glob("checkpoint_*.json"))
        if not checkpoint_files:
            raise FileNotFoundError(f"No checkpoint files found in {run_dir}")

        checkpoint_file = max(checkpoint_files, key=lambda p: p.stat().st_mtime)
        with open(checkpoint_file) as f:
            checkpoint = json.load(f)

        template_name = checkpoint["template_name"]
        start_step = checkpoint["step"] + 1
        history = checkpoint.get("history", [])

        self._log(f"Resuming from step {start_step} for {template_name}")

        pool = CandidatePool()
        for cdata in checkpoint.get("pool", []):
            candidate = PromptCandidate(
                id=cdata["id"],
                template_name=template_name,
                prompt_text=cdata["prompt_text"],
                parent_id=cdata.get("parent_id"),
                generation=cdata.get("generation", 0),
                scores=cdata.get("scores", {}),
                validation_score=cdata.get("validation_score"),
                mutation_type=cdata.get("mutation_type"),
                mutation_rationale=cdata.get("mutation_rationale"),
            )
            pool.add(candidate)

        if not pool.candidates:
            raise ValueError("Checkpoint contains no candidates")

        initial_candidate = pool.candidates[0]

        self._run_evolution_loop(
            pool,
            template_name,
            train_examples,
            val_examples,
            history,
            start_step=start_step,
        )

        best = pool.get_best()
        self._log(f"\nBest: {best.id} (score={best.validation_score or 0.0:.3f})")

        results = self._build_results(template_name, best, initial_candidate, pool, history)
        self._save_results(results, template_name)
        return results

    def _run_evolution_loop(
        self,
        pool: CandidatePool,
        template_name: str,
        train_examples: list[EvaluationExample],
        val_examples: list[EvaluationExample],
        history: list[dict[str, Any]],
        start_step: int = 1,
    ) -> None:
        """Run the core evolution loop shared by optimize() and resume().

        Candidates are added to the pool only if they score strictly higher
        than their parent, or with probability ``diversity_probability`` to
        maintain population diversity.  Early stopping triggers when the
        *global* best score has not improved for ``patience`` consecutive
        steps (not just the parent comparison).
        """
        best_so_far = pool.get_best().validation_score or 0.0
        steps_without_improvement = 0

        for step in range(start_step, self.config.max_steps + 1):
            step_start = time.time()
            self._log(f"\n--- Step {step}/{self.config.max_steps} ---")

            selected = pool.pareto_select(rng=self._rng)
            sel_score = selected.validation_score or 0.0
            self._log(f"Selected: {selected.id} (score={sel_score:.3f})")
            self._log_metric_breakdown(selected)

            minibatch = self._sample_minibatch(train_examples)
            eval_result = self.evaluator.evaluate(
                selected.prompt_text,
                template_name,
                minibatch,
                candidate_id=selected.id,
            )

            self._populate_traces_with_scores(eval_result)

            try:
                mutated_text, rationale, mutation_type = self._do_mutation(
                    pool,
                    selected,
                    eval_result.traces,
                    template_name,
                    minibatch,
                )
            except (ValueError, RuntimeError, OSError, TimeoutError) as e:
                self._log(f"  Mutation failed: {e}, skipping step")
                continue

            if not self.adapter.validate_template(template_name, mutated_text):
                self._log("  Mutation invalid (broke template), skipping")
                continue

            new_candidate = self._make_candidate(
                template_name=template_name,
                prompt_text=mutated_text,
                parent_id=selected.id,
                mutation_type=mutation_type,
                mutation_rationale=rationale,
                generation=step,
            )

            val_result = self.evaluator.evaluate(
                mutated_text,
                template_name,
                val_examples,
                candidate_id=new_candidate.id,
            )
            new_candidate.validation_score = val_result.overall_score
            new_candidate.scores = val_result.scores

            new_score = new_candidate.validation_score or 0.0
            self._log(f"  {mutation_type}: {new_score:.3f} (parent: {sel_score:.3f})")
            self._log_metric_delta(selected, new_candidate)

            if new_score > sel_score:
                pool.add(new_candidate)
                delta = new_score - sel_score
                self._log(f"  Added to pool (improvement: +{delta:.3f})")
            elif self._rng.random() < self.config.diversity_probability:
                pool.add(new_candidate)
                self._log("  Added to pool (diversity)")

            pool.prune_dominated(self.config.num_candidates)

            step_duration = time.time() - step_start
            history.append(
                self._history_entry(
                    step,
                    new_candidate,
                    mutation_type,
                    pool,
                    rationale,
                    step_duration=step_duration,
                    efficiency=val_result.efficiency,
                )
            )

            if new_candidate.validation_score > best_so_far:
                steps_without_improvement = 0
                best_so_far = new_candidate.validation_score
            else:
                steps_without_improvement += 1

            if steps_without_improvement >= self.config.patience:
                self._log(f"Early stopping: no improvement for {self.config.patience} steps")
                break

            if step % self.config.checkpoint_interval == 0:
                self._save_checkpoint(pool, history, template_name, step)

    def _do_mutation(
        self,
        pool: CandidatePool,
        selected: PromptCandidate,
        traces: list[ExecutionTrace],
        template_name: str,
        minibatch: list[EvaluationExample],
    ) -> tuple[str, str, str]:
        """Perform mutation or crossover."""
        if self._rng.random() < self.config.mutation_probability or len(pool) < 2:
            mutated_text, rationale = self.reflector.mutate(selected.prompt_text, traces, template_name)
            return mutated_text, rationale, "mutate"

        other = pool.pareto_select(rng=self._rng)
        attempts = 0
        while other.id == selected.id and len(pool) > 1 and attempts < 10:
            other = pool.pareto_select(rng=self._rng)
            attempts += 1

        other_result = self.evaluator.evaluate(
            other.prompt_text,
            template_name,
            minibatch,
            candidate_id=other.id,
        )
        mutated_text, rationale = self.reflector.crossover(
            selected.prompt_text,
            other.prompt_text,
            traces,
            other_result.traces,
            template_name,
        )
        return mutated_text, rationale, "crossover"

    def _populate_traces_with_scores(self, eval_result: EvaluationResult) -> None:
        """Populate traces with metric scores and generated scripts."""
        for trace in eval_result.traces:
            trace.metric_scores = eval_result.scores
            scripts = extract_generated_scripts({trace.phase: [{"messages": trace.messages}]})
            if not scripts:
                scripts = extract_generated_scripts({"script_generation": [{"messages": trace.messages}]})
            trace.generated_scripts = scripts

    def _make_candidate(self, **kwargs) -> PromptCandidate:
        """Create a new PromptCandidate with a unique ID."""
        return PromptCandidate(id=str(uuid.uuid4())[:12], **kwargs)

    def _sample_minibatch(self, examples: list[EvaluationExample]) -> list[EvaluationExample]:
        """Sample a minibatch from training examples."""
        return self._rng.sample(examples, min(self.config.minibatch_size, len(examples)))

    def _history_entry(
        self,
        step: int,
        candidate: PromptCandidate,
        mutation_type: str,
        pool: CandidatePool,
        rationale: str = "",
        step_duration: float = 0.0,
        efficiency: EfficiencyMetrics | None = None,
    ) -> dict[str, Any]:
        """Build a history entry dict."""
        entry = {
            "step": step,
            "candidate_id": candidate.id,
            "score": candidate.validation_score,
            "scores": candidate.scores,
            "mutation_type": mutation_type,
            "rationale": rationale,
            "pool_size": len(pool),
            "best_score": pool.get_best().validation_score,
            "step_duration_seconds": step_duration,
        }
        if efficiency is not None:
            entry["efficiency"] = efficiency.to_dict()
        return entry

    def _build_results(
        self,
        template_name: str,
        best: PromptCandidate,
        initial: PromptCandidate,
        pool: CandidatePool,
        history: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Build the final results dict."""
        step_durations = [h.get("step_duration_seconds", 0) for h in history if h.get("step", 0) > 0]
        efficiency_entries = [h["efficiency"] for h in history if h.get("efficiency") is not None]

        efficiency_summary = {}
        if efficiency_entries:
            efficiency_summary = {
                "avg_estimated_tokens": sum(e["estimated_tokens"] for e in efficiency_entries)
                / len(efficiency_entries),
                "avg_turn_count": sum(e["turn_count"] for e in efficiency_entries) / len(efficiency_entries),
                "avg_tool_calls": sum(e["tool_call_count"] for e in efficiency_entries) / len(efficiency_entries),
                "total_wall_clock_seconds": sum(e["wall_clock_seconds"] for e in efficiency_entries),
            }

        return {
            "template_name": template_name,
            "best_prompt": best.prompt_text,
            "best_score": best.validation_score or 0.0,
            "best_scores": best.scores,
            "initial_score": initial.validation_score or 0.0,
            "improvement": ((best.validation_score or 0.0) - (initial.validation_score or 0.0)),
            "total_steps": self.config.max_steps,
            "final_pool_size": len(pool),
            "total_wall_clock_seconds": sum(step_durations),
            "efficiency_summary": efficiency_summary,
            "config": {
                "max_steps": self.config.max_steps,
                "num_candidates": self.config.num_candidates,
                "seed": self.config.seed,
                "mutation_probability": self.config.mutation_probability,
                "diversity_probability": self.config.diversity_probability,
                "patience": self.config.patience,
                "reflection_provider": self.config.reflection_provider,
                "reflection_model": self.config.reflection_model,
            },
            "history": history,
            "all_candidates": [
                {
                    "id": c.id,
                    "score": c.validation_score,
                    "scores": c.scores,
                    "generation": c.generation,
                    "mutation_type": c.mutation_type,
                    "parent_id": c.parent_id,
                }
                for c in pool.candidates
            ],
        }

    def _log(self, message: str) -> None:
        """Log a message to both loguru and the optimization log file."""
        logger.info(message)
        log_file = self._ensure_log_file()
        log_file.write(f"{time.strftime('%H:%M:%S')} {message}\n")
        log_file.flush()

    def _log_metric_breakdown(self, candidate: PromptCandidate) -> None:
        """Log per-metric breakdown for a candidate."""
        if not candidate.scores:
            return
        parts = [f"{k}={v:.2f}" for k, v in sorted(candidate.scores.items())]
        self._log(f"  Metric breakdown: {', '.join(parts)}")

    def _log_metric_delta(self, parent: PromptCandidate, child: PromptCandidate) -> None:
        """Log per-metric delta between parent and child."""
        if not parent.scores or not child.scores:
            return
        all_keys = sorted(set(parent.scores) | set(child.scores))
        parts = []
        for key in all_keys:
            old = parent.scores.get(key, 0.0)
            new = child.scores.get(key, 0.0)
            delta = new - old
            if delta == 0:
                delta_str = "="
            elif delta > 0:
                delta_str = f"+{delta:.2f}"
            else:
                delta_str = f"{delta:.2f}"
            parts.append(f"    {key}: {old:.2f}\u2192{new:.2f} ({delta_str})")
        self._log("\n".join(parts))

    def _save_checkpoint(
        self,
        pool: CandidatePool,
        history: list[dict[str, Any]],
        template_name: str,
        step: int | None = None,
    ) -> None:
        """Save full optimization checkpoint for resume capability."""
        if step is None:
            step = history[-1]["step"] if history else 0

        checkpoint = {
            "template_name": template_name,
            "step": step,
            "pool_size": len(pool),
            "best_score": pool.get_best().validation_score,
            "pool": [
                {
                    "id": c.id,
                    "prompt_text": c.prompt_text,
                    "validation_score": c.validation_score,
                    "scores": c.scores,
                    "generation": c.generation,
                    "parent_id": c.parent_id,
                    "mutation_type": c.mutation_type,
                    "mutation_rationale": c.mutation_rationale,
                }
                for c in pool.candidates
            ],
            "history": history,
        }
        safe_name = template_name.replace("/", "_")
        tmp_path = self.output_dir / f"checkpoint_{safe_name}.json.tmp"
        final_path = self.output_dir / f"checkpoint_{safe_name}.json"
        with open(tmp_path, "w") as f:
            json.dump(checkpoint, f, indent=2)
        tmp_path.rename(final_path)

    def _save_results(self, results: dict[str, Any], template_name: str) -> None:
        """Save final optimization results."""
        safe_name = template_name.replace("/", "_")
        path = self.output_dir / f"results_{safe_name}.json"
        with open(path, "w") as f:
            json.dump(results, f, indent=2)

        best_path = self.output_dir / f"best_{safe_name}"
        with open(best_path, "w") as f:
            f.write(results["best_prompt"])
