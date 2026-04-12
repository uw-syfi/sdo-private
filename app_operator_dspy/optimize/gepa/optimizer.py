"""GEPA optimizer adapted for DSPy signature instructions.

Implements the core GEPA loop: evaluate → reflect → mutate → select,
targeting DSPy signature instruction text as the optimization surface.
Supports optimizing multiple signatures jointly in a single run.
"""

import json
import os
import random
from dataclasses import dataclass, field

from app_operator_dspy.logger import get_logger
from app_operator_dspy.optimize.gepa.adapter import (
    apply_candidate,
    get_instruction,
    make_initial_candidate,
    set_instruction,
    validate_candidate,
)
from app_operator_dspy.optimize.gepa.candidate import CandidatePool, PromptCandidate
from app_operator_dspy.optimize.gepa.evaluator import SignatureEvaluator
from app_operator_dspy.optimize.gepa.reflector import ExecutionTrace, SignatureReflector

log = get_logger("gepa.optimizer")


@dataclass
class GEPAConfig:
    max_steps: int = 10
    max_pool_size: int = 20
    mutation_probability: float = 0.7
    diversity_probability: float = 0.1
    patience: int = 5
    minibatch_size: int = 3
    seed: int = 42
    output_dir: str = "results/gepa"
    use_seeds: bool = True


@dataclass
class GEPAResult:
    signature_names: list[str]
    best_instructions: dict[str, str]
    score_before: float
    score_after: float
    steps_taken: int
    output_path: str


# --- Seed instructions for pool diversity ---
# Each emphasizes a different failure mode observed in baseline runs.

_DEPLOY_SEEDS = [
    # Emphasis on lowercase project names and correct compose file discovery
    (
        "Generate a bash deploy.sh for a microservice repository. "
        "CRITICAL: Always lowercase the project name for docker compose. "
        "First check if docker-compose.yml or docker-compose.yaml already exists in the repo. "
        "If it does, use it directly — do NOT generate a new one. "
        "Only generate a compose file if none exists."
    ),
    # Emphasis on build contexts and file paths
    (
        "Generate a bash deploy.sh for a microservice repository. "
        "CRITICAL: Verify all build context paths exist before using them. "
        "Use 'ls' or 'find' to confirm directory structure. "
        "Never assume subdirectory names — discover them from the filesystem. "
        "All paths in docker-compose.yml must be relative to the repo root."
    ),
    # Emphasis on platform compatibility and port conflicts
    (
        "Generate a bash deploy.sh for a microservice repository. "
        "CRITICAL: Add 'platform: linux/amd64' for any pre-built images on ARM hosts. "
        "Check for port conflicts before binding. "
        "Use docker compose v2 syntax (space, not hyphen). "
        "Always pass --project-name to every docker compose command."
    ),
]

_REPAIR_SEEDS = [
    # Emphasis on reading before fixing
    (
        "Debug and fix a failed deployment by editing deploy.sh, health_check.sh, or docker-compose files. "
        "ALWAYS read the current file contents before making any edits. "
        "Never assume what a file contains — read it first. "
        "Make minimal, targeted fixes. Do not rewrite scripts from scratch."
    ),
    # Emphasis on correct app context
    (
        "Debug and fix a failed deployment by editing deploy.sh, health_check.sh, or docker-compose files. "
        "CRITICAL: Only reference services, paths, and ports that exist in THIS repository. "
        "Use 'ls', 'find', and 'docker compose ps' to verify the current state. "
        "Never carry over assumptions from other applications."
    ),
]

_SEED_INSTRUCTIONS: dict[str, list[str]] = {
    "GenerateDeployScript": _DEPLOY_SEEDS,
    "RepairDeploymentError": _REPAIR_SEEDS,
}


def run_gepa(
    signature_names: list[str],
    train_app_paths: list[str],
    eval_app_paths: list[str] | None = None,
    config: GEPAConfig | None = None,
) -> GEPAResult:
    """Run GEPA optimization on one or more DSPy signatures jointly.

    All signatures are optimized together — each candidate holds
    instructions for all target signatures, and evaluation runs the
    full pipeline exercising all of them.

    Args:
        signature_names: Signatures to optimize jointly.
        train_app_paths: App paths to train on.
        eval_app_paths: App paths for final evaluation (optional).
        config: GEPA configuration.

    Returns:
        GEPAResult with best instructions and scores.
    """
    config = config or GEPAConfig()
    rng = random.Random(config.seed)
    os.makedirs(config.output_dir, exist_ok=True)

    evaluator = SignatureEvaluator()
    reflector = SignatureReflector()
    pool = CandidatePool(max_size=config.max_pool_size)

    # Save original instructions for restoration on failure
    originals = {name: get_instruction(name) for name in signature_names}

    log.info("GEPA optimizing jointly: {}", signature_names)

    # --- Seed pool with diverse candidates ---
    # Initial candidate: current instructions
    initial = _make_joint_candidate(signature_names, generation=0, mutation_type="initial")
    log.info("evaluating baseline on {} apps...", len(train_app_paths))
    score_before, initial_traces = _evaluate_joint(evaluator, initial, signature_names, train_app_paths)
    initial.overall_score = score_before
    pool.add(initial)
    log.info("baseline score: {:.3f}", score_before)

    # Add seed candidates (evaluated on a small minibatch for speed)
    if not config.use_seeds:
        log.info("skipping seed candidates")
    seed_batch_size = min(2, len(train_app_paths))
    seed_batch = rng.sample(train_app_paths, seed_batch_size)
    for i, seed_instructions in enumerate(_generate_seed_combinations(signature_names) if config.use_seeds else []):
        seed = _make_joint_candidate(
            signature_names,
            overrides=seed_instructions,
            generation=0,
            mutation_type=f"seed_{i}",
        )
        seed_score, seed_traces = _evaluate_joint(evaluator, seed, signature_names, seed_batch)
        seed.overall_score = seed_score
        pool.add(seed)
        log.info("seed {} score: {:.3f}", i, seed_score)

    trace_cache: dict[str, list[ExecutionTrace]] = {initial.id: initial_traces}
    best_score = max(c.overall_score or 0 for c in pool.candidates)
    steps_without_improvement = 0
    step = 0

    for step in range(1, config.max_steps + 1):
        log.info("--- step {}/{} (best={:.3f}) ---", step, config.max_steps, best_score)

        parent = pool.pareto_select(rng)
        batch_size = min(config.minibatch_size, len(train_app_paths))
        minibatch = rng.sample(train_app_paths, batch_size)

        # Pick which signature to mutate this step (round-robin with randomness)
        target_sig = rng.choice(signature_names)

        parent_traces = trace_cache.get(parent.id, [])
        if rng.random() < config.mutation_probability or len(pool.candidates) < 2:
            # Mutate one signature
            child_instruction = reflector.mutate(
                PromptCandidate(
                    signature_name=target_sig,
                    instruction_text=parent.scores.get(f"instruction_{target_sig}", ""),
                ),
                parent_traces,
            )
            # Build joint candidate with the mutated signature
            child_overrides = {}
            for name in signature_names:
                if name == target_sig:
                    child_overrides[name] = child_instruction.instruction_text
                else:
                    child_overrides[name] = parent.scores.get(f"instruction_{name}", "")
            child = _make_joint_candidate(
                signature_names,
                overrides=child_overrides,
                generation=parent.generation + 1,
                mutation_type=f"mutate_{target_sig}",
                parent_id=parent.id,
                rationale=child_instruction.mutation_rationale,
            )
        else:
            other = pool.pareto_select(rng)
            other_traces = trace_cache.get(other.id, [])
            child_instruction = reflector.crossover(
                PromptCandidate(
                    signature_name=target_sig,
                    instruction_text=parent.scores.get(f"instruction_{target_sig}", ""),
                    overall_score=parent.overall_score or 0,
                ),
                PromptCandidate(
                    signature_name=target_sig,
                    instruction_text=other.scores.get(f"instruction_{target_sig}", ""),
                    overall_score=other.overall_score or 0,
                ),
                parent_traces,
                other_traces,
            )
            child_overrides = {}
            for name in signature_names:
                if name == target_sig:
                    child_overrides[name] = child_instruction.instruction_text
                else:
                    child_overrides[name] = parent.scores.get(f"instruction_{name}", "")
            child = _make_joint_candidate(
                signature_names,
                overrides=child_overrides,
                generation=max(parent.generation, other.generation) + 1,
                mutation_type=f"crossover_{target_sig}",
                parent_id=parent.id,
            )

        if not validate_candidate(child):
            log.info("  invalid mutation, skipping")
            continue

        child_score, child_traces = _evaluate_joint(evaluator, child, signature_names, minibatch)
        trace_cache[child.id] = child_traces
        log.info("  child {} score: {:.3f} (parent: {:.3f})",
                 child.id, child_score, parent.overall_score or 0)

        parent_score = parent.overall_score or 0
        if child_score > parent_score or rng.random() < config.diversity_probability:
            pool.add(child)
            pool.prune()

        if child_score > best_score:
            best_score = child_score
            steps_without_improvement = 0
            log.info("  new best! {:.3f}", best_score)
        else:
            steps_without_improvement += 1

        if steps_without_improvement >= config.patience:
            log.info("early stopping: no improvement for {} steps", config.patience)
            break

        _save_checkpoint(config.output_dir, signature_names, step, pool)

    # Get best and apply
    best = pool.get_best()
    if best is None:
        best = initial

    _apply_joint(best, signature_names)
    score_after = best.overall_score or score_before

    if eval_app_paths:
        log.info("evaluating best candidate on {} eval apps...", len(eval_app_paths))
        eval_score, _ = _evaluate_joint(evaluator, best, signature_names, eval_app_paths)
        log.info("eval score: {:.3f}", eval_score)
        score_after = eval_score

    # Save results
    best_instructions = {
        name: best.scores.get(f"instruction_{name}", originals[name])
        for name in signature_names
    }
    result_path = os.path.join(config.output_dir, "gepa_best.json")
    _save_result(result_path, signature_names, best, best_instructions, score_before, score_after, step, pool)

    log.info("GEPA done: {:.3f} → {:.3f}", score_before, score_after)

    return GEPAResult(
        signature_names=signature_names,
        best_instructions=best_instructions,
        score_before=score_before,
        score_after=score_after,
        steps_taken=step,
        output_path=result_path,
    )


# --- Helpers ---


def _make_joint_candidate(
    signature_names: list[str],
    overrides: dict[str, str] | None = None,
    generation: int = 0,
    mutation_type: str | None = None,
    parent_id: str | None = None,
    rationale: str | None = None,
) -> PromptCandidate:
    """Create a candidate that holds instructions for all target signatures."""
    instructions = {}
    for name in signature_names:
        if overrides and name in overrides:
            instructions[name] = overrides[name]
        else:
            instructions[name] = get_instruction(name)

    # Use first signature name as the candidate's nominal signature
    combined_text = "\n---\n".join(
        f"[{name}]\n{text}" for name, text in instructions.items()
    )
    candidate = PromptCandidate(
        signature_name="+".join(signature_names),
        instruction_text=combined_text,
        generation=generation,
        mutation_type=mutation_type,
        parent_id=parent_id,
        mutation_rationale=rationale,
    )
    # Store individual instructions in scores dict for retrieval
    candidate.scores = {f"instruction_{name}": text for name, text in instructions.items()}
    return candidate


def _apply_joint(candidate: PromptCandidate, signature_names: list[str]) -> None:
    """Apply a joint candidate's instructions to all target signatures."""
    for name in signature_names:
        instruction = candidate.scores.get(f"instruction_{name}")
        if instruction:
            set_instruction(name, instruction)


def _evaluate_joint(
    evaluator: SignatureEvaluator,
    candidate: PromptCandidate,
    signature_names: list[str],
    app_paths: list[str],
) -> tuple[float, list[ExecutionTrace]]:
    """Apply joint instructions and evaluate."""
    _apply_joint(candidate, signature_names)
    # Use a temporary proxy candidate for the evaluator API (joint name
    # isn't a real signature, so evaluate() will skip apply_candidate).
    proxy = PromptCandidate(
        signature_name=candidate.signature_name,
        instruction_text="joint",
        id=candidate.id,
    )
    overall, traces = evaluator.evaluate(proxy, app_paths)
    # Copy eval results back to the real candidate
    candidate.overall_score = overall
    candidate.scores.update(proxy.scores)
    return overall, traces


def _generate_seed_combinations(signature_names: list[str]) -> list[dict[str, str]]:
    """Generate diverse seed instruction combinations."""
    combinations = []
    for name in signature_names:
        seeds = _SEED_INSTRUCTIONS.get(name, [])
        for seed_text in seeds:
            combo = {}
            for n in signature_names:
                if n == name:
                    combo[n] = seed_text
                else:
                    combo[n] = get_instruction(n)
            combinations.append(combo)
    return combinations


def _save_checkpoint(
    output_dir: str,
    signature_names: list[str],
    step: int,
    pool: CandidatePool,
) -> None:
    path = os.path.join(output_dir, "gepa_checkpoint.json")
    best = pool.get_best()
    data = {
        "signatures": signature_names,
        "step": step,
        "pool_size": len(pool.candidates),
        "best_score": best.overall_score if best else None,
    }
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def _save_result(
    path: str,
    signature_names: list[str],
    best: PromptCandidate,
    best_instructions: dict[str, str],
    score_before: float,
    score_after: float,
    steps: int,
    pool: CandidatePool,
) -> None:
    data = {
        "signature_names": signature_names,
        "score_before": round(score_before, 4),
        "score_after": round(score_after, 4),
        "improvement": round(score_after - score_before, 4),
        "steps_taken": steps,
        "best_instructions": best_instructions,
        "best_candidate": {
            "id": best.id,
            "overall_score": best.overall_score,
            "mutation_type": best.mutation_type,
            "mutation_rationale": best.mutation_rationale,
            "generation": best.generation,
        },
        "pool_size": len(pool.candidates),
    }
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
