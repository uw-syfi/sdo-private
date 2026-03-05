"""CLI for running GEPA optimization on SDS prompts.

Usage:
    uv run python -m app_operator.gepa --agent-type deployer \
        --test-repos apps/deathstarbench/hotelReservation
    uv run python -m app_operator.gepa --template deployer/system.jinja2 \
        --test-repos apps/deathstarbench/hotelReservation
    uv run python -m app_operator.gepa --apply-best gepa_runs/YYYYMMDD-HHMMSS
    uv run python -m app_operator.gepa --resume gepa_runs/YYYYMMDD-HHMMSS \
        --test-repos apps/deathstarbench/hotelReservation
    uv run python -m app_operator.gepa --dry-run --agent-type deployer \
        --test-repos apps/deathstarbench/hotelReservation
"""

import argparse
import json
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path

from app_operator.config import GEPAConfig, load_config
from app_operator.gepa.adapter import SDSPromptAdapter
from app_operator.gepa.evaluator import (
    METRICS_REGISTRY,
    EvaluationExample,
    SDSEvaluator,
)
from app_operator.gepa.optimizer import GEPAOptimizer
from app_operator.gepa.reflector import PromptReflector
from app_operator.logger import logger


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for the GEPA CLI."""
    parser = argparse.ArgumentParser(description="GEPA Prompt Optimization for SDS")
    parser.add_argument(
        "--agent-type",
        choices=["deployer", "monitor", "code_analyzer"],
        help="Optimize all templates for this agent type",
    )
    parser.add_argument(
        "--template",
        help="Optimize a specific template (e.g. deployer/system.jinja2)",
    )
    parser.add_argument("--max-steps", type=int, default=None, help="Evolution steps")
    parser.add_argument(
        "--num-candidates",
        type=int,
        default=None,
        help="Max candidate pool size",
    )
    parser.add_argument(
        "--minibatch-size",
        type=int,
        default=None,
        help="Training minibatch size per step",
    )
    parser.add_argument(
        "--validation-size",
        type=int,
        default=None,
        help="Number of validation examples",
    )
    parser.add_argument(
        "--mutation-probability",
        type=float,
        default=None,
        help="Probability of mutation vs crossover",
    )
    parser.add_argument(
        "--patience",
        type=int,
        default=None,
        help="Early stopping patience (steps without improvement)",
    )
    parser.add_argument(
        "--checkpoint-interval",
        type=int,
        default=None,
        help="Save checkpoint every N steps",
    )
    parser.add_argument(
        "--diversity",
        type=float,
        default=None,
        help="Probability of accepting non-improving mutations for diversity",
    )
    parser.add_argument(
        "--reflection-provider",
        default=None,
        help="LLM provider for reflection",
    )
    parser.add_argument(
        "--reflection-model",
        default=None,
        help="Model for reflection LM",
    )
    parser.add_argument("--output-dir", default=None, help="Output directory")
    parser.add_argument(
        "--test-repos",
        nargs="+",
        help="Paths to test repositories (used for both train and val)",
    )
    parser.add_argument(
        "--train-repos",
        nargs="+",
        help="Paths to training repositories (overrides --test-repos)",
    )
    parser.add_argument(
        "--val-repos",
        nargs="+",
        help="Paths to validation repositories (overrides --test-repos)",
    )
    parser.add_argument(
        "--apply-best",
        metavar="RUN_DIR",
        help="Apply best prompts from a previous run to templates",
    )
    parser.add_argument(
        "--resume",
        metavar="RUN_DIR",
        help="Resume optimization from a checkpoint in the given run directory",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Random seed for reproducibility",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be optimized without running",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Main entry point for the GEPA CLI."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.apply_best:
        return _apply_best(args.apply_best)

    if args.resume:
        return _handle_resume(args, parser)

    if not args.agent_type and not args.template:
        parser.error("Either --agent-type or --template is required")

    train_repos, val_repos = _resolve_repos(args, parser)

    adapter = SDSPromptAdapter()

    agent_type = args.agent_type
    if not agent_type and args.template:
        info = adapter.get_template_info(args.template)
        if not info:
            logger.error(f"Unknown template: {args.template}")
            return 1
        agent_type = info.agent_type

    train_examples = _build_examples(train_repos, agent_type)
    val_examples = _build_examples(val_repos, agent_type)

    if args.dry_run:
        _print_dry_run(adapter, agent_type, args.template, train_examples)
        return 0

    with _build_optimizer(args, adapter) as optimizer:
        if args.template:
            optimizer.optimize(args.template, train_examples, val_examples)
        else:
            optimizer.optimize_all(agent_type, train_examples, val_examples)

    return 0


def _handle_resume(args, parser) -> int:
    """Handle --resume flag."""
    train_repos, val_repos = _resolve_repos(args, parser, context="--resume")

    run_path = Path(args.resume)
    if not run_path.exists():
        logger.error(f"Run directory not found: {args.resume}")
        return 1

    checkpoint_files = list(run_path.glob("checkpoint_*.json"))
    if not checkpoint_files:
        logger.error(f"No checkpoint files found in {args.resume}")
        return 1

    latest_checkpoint = max(checkpoint_files, key=lambda p: p.stat().st_mtime)
    with open(latest_checkpoint) as f:
        checkpoint = json.load(f)
    template_name = checkpoint.get("template_name", "")

    adapter = SDSPromptAdapter()
    info = adapter.get_template_info(template_name)
    agent_type = info.agent_type if info else args.agent_type or "deployer"

    train_examples = _build_examples(train_repos, agent_type)
    val_examples = _build_examples(val_repos, agent_type)

    with _build_optimizer(args, adapter) as optimizer:
        optimizer.resume(args.resume, train_examples, val_examples)
    return 0


def _resolve_repos(args, parser, context: str = "") -> tuple[list[str], list[str]]:
    """Resolve train and validation repo lists from CLI args.

    Returns:
        Tuple of (train_repos, val_repos).
    """
    train_repos = args.train_repos or args.test_repos
    val_repos = args.val_repos or args.test_repos
    suffix = f" for {context}" if context else ""

    if not train_repos:
        parser.error(f"--test-repos or --train-repos is required{suffix}")
    if not val_repos:
        parser.error(f"--test-repos or --val-repos is required{suffix}")

    return train_repos, val_repos


def _build_config(args) -> GEPAConfig:
    """Build a GEPAConfig from sds.toml defaults, overridden by CLI args.

    Layering: dataclass defaults → sds.toml [gepa] → CLI args.
    Only CLI args explicitly provided by the user override sds.toml values.
    """
    base = load_config(".").gepa

    cli_overrides = {
        "max_steps": args.max_steps,
        "num_candidates": args.num_candidates,
        "minibatch_size": args.minibatch_size,
        "validation_size": args.validation_size,
        "mutation_probability": args.mutation_probability,
        "diversity_probability": args.diversity,
        "patience": args.patience,
        "checkpoint_interval": args.checkpoint_interval,
        "reflection_provider": args.reflection_provider,
        "reflection_model": args.reflection_model,
        "output_dir": args.output_dir,
        "seed": args.seed,
    }
    overrides = {k: v for k, v in cli_overrides.items() if v is not None}

    merged = {**asdict(base), **overrides}
    return GEPAConfig(**merged)


def _build_optimizer(args, adapter: SDSPromptAdapter) -> GEPAOptimizer:
    """Build a fully assembled GEPAOptimizer from CLI args and adapter."""
    config = _build_config(args)
    agent_type = args.agent_type or "deployer"
    return GEPAOptimizer(
        config=config,
        adapter=adapter,
        reflector=PromptReflector(config),
        evaluator=SDSEvaluator(
            metrics=_get_metrics_for_agent(agent_type),
            agent_factory=_create_agent_factory(),
            templates_dir=adapter.templates_dir,
        ),
    )


def _get_metrics_for_agent(
    agent_type: str,
) -> dict[str, Callable]:
    """Return appropriate metrics for the agent type from the registry."""
    return METRICS_REGISTRY.get(agent_type, METRICS_REGISTRY["deployer"])


def _build_examples(repo_paths: list[str], agent_type: str) -> list[EvaluationExample]:
    """Build EvaluationExamples from test repo paths."""
    return [
        EvaluationExample(
            repo_path=Path(p),
            expected_outcome={},
            agent_type=agent_type,
            description=f"Test repo: {p}",
        )
        for p in repo_paths
    ]


def _create_agent_factory() -> Callable:
    """Create a factory for CodingAgent instances."""
    from app_operator.cli_agent.factory import create_agent_from_config

    def factory():
        config = load_config(".")
        return create_agent_from_config(".", config=config)

    return factory


def _apply_best(run_dir: str) -> int:
    """Apply best prompts from a completed run to templates."""
    run_path = Path(run_dir)
    if not run_path.exists():
        logger.error(f"Run directory not found: {run_dir}")
        return 1

    adapter = SDSPromptAdapter()
    applied = 0

    for results_file in run_path.glob("results_*.json"):
        with open(results_file) as f:
            results = json.load(f)

        template_name = results.get("template_name")
        best_prompt = results.get("best_prompt")

        if not template_name or not best_prompt:
            continue

        if adapter.validate_template(template_name, best_prompt):
            adapter.write_template(template_name, best_prompt)
            logger.info(f"Applied best prompt for {template_name} (score: {results.get('best_score', 'N/A')})")
            applied += 1
        else:
            logger.warning(f"Skipping {template_name}: optimized prompt failed validation")

    logger.info(f"Applied {applied} optimized prompt(s)")
    return 0


def _print_dry_run(
    adapter: SDSPromptAdapter,
    agent_type: str,
    template_name: str | None,
    examples: list[EvaluationExample],
) -> None:
    """Print what would be optimized."""
    if template_name:
        templates = [template_name]
    else:
        templates = adapter.get_templates_for_agent(agent_type)

    logger.info(f"Would optimize {len(templates)} template(s):")
    for t in templates:
        info = adapter.get_template_info(t)
        desc = info.description if info else "unknown"
        logger.info(f"  {t} - {desc}")

    metrics = _get_metrics_for_agent(agent_type)
    logger.info(f"Metrics: {', '.join(metrics.keys())}")
    logger.info(f"Using {len(examples)} test repository(ies)")
