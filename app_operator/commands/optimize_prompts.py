"""Optimize prompts using DSPy.

This command runs offline prompt optimization using trajectory data.
"""

import copy
import sys
from pathlib import Path

from app_operator.config import load_config
from app_operator.dspy_integration.optimizer import PromptOptimizer
from app_operator.dspy_integration.signatures import SIGNATURES


def add_arguments(parser):
    """Add arguments for optimize-prompts command."""
    parser.add_argument(
        "--prompts",
        type=str,
        nargs="+",
        help=f"Prompt names to optimize. Available: {', '.join(sorted(SIGNATURES.keys()))}",
    )
    parser.add_argument(
        "--trajectories-dir",
        type=Path,
        nargs="+",
        help="One or more directories containing trajectory files "
        "(default: .sds/trajectories in current dir). "
        "Examples from all directories are merged before optimization. "
        "Use shell glob expansion to select multiple baseline runs: "
        "exp/hotelReservation/baseline-*/.sds/trajectories. "
        "WARNING: Do not mix baseline and optimizee trajectories in training data.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Output directory for optimized prompts (default: auto-versioned)",
    )
    parser.add_argument(
        "--config",
        type=Path,
        help="Path to sds.toml configuration file",
    )
    parser.add_argument(
        "--optimizer",
        choices=["BootstrapFewShot", "BootstrapFewShotWithRandomSearch", "MIPROv2", "COPRO"],
        help="DSPy optimizer to use (default: from config or BootstrapFewShot)",
    )
    parser.add_argument(
        "--num-examples",
        type=int,
        help="Number of training examples to use (default: from config or 30)",
    )
    parser.add_argument(
        "--teacher-model",
        type=str,
        help="Teacher model for optimization (default: from config or claude-sonnet-4-5)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate inputs without running optimization",
    )
    parser.add_argument(
        "--list-prompts",
        action="store_true",
        help="List available prompts and exit",
    )
    parser.add_argument(
        "--use-seeds",
        action="store_true",
        help="Use minimal GEPA-style seed prompts instead of baseline templates "
        "(start small and let optimizer discover effective patterns)",
    )


def run_command(args) -> int:
    """Execute optimize-prompts command.

    Args:
        args: Parsed command-line arguments

    Returns:
        Exit code (0 for success, non-zero for failure)
    """
    # List prompts mode
    if args.list_prompts:
        print("\nAvailable prompts for optimization:")
        print("=" * 50)
        for prompt_name in sorted(SIGNATURES.keys()):
            signature = SIGNATURES[prompt_name]
            print(f"\n{prompt_name}")
            if signature.__doc__:
                doc_lines = signature.__doc__.strip().split("\n")
                print(f"  {doc_lines[0]}")
        print()
        return 0

    # Validate required arguments
    if not args.prompts:
        print("Error: --prompts is required", file=sys.stderr)
        print("Use --list-prompts to see available prompts", file=sys.stderr)
        return 1

    # Determine trajectories directories
    trajectories_dirs = args.trajectories_dir
    if trajectories_dirs is None:
        trajectories_dirs = [Path.cwd() / ".sds" / "trajectories"]

    missing = [d for d in trajectories_dirs if not d.exists()]
    if missing:
        for d in missing:
            print(f"Error: Trajectories directory not found: {d}", file=sys.stderr)
        print("Run the operator first to generate trajectory data.", file=sys.stderr)
        return 1

    # Load configuration
    try:
        if args.config:
            config = load_config(str(args.config.parent), str(args.config))
        else:
            config = load_config(str(Path.cwd()))

        dspy_config = copy.deepcopy(config.dspy)
    except Exception as e:
        print(f"Error loading config: {e}", file=sys.stderr)
        return 1

    # Override config with command-line arguments
    if args.optimizer:
        dspy_config.optimization.optimizer = args.optimizer
    if args.num_examples:
        dspy_config.optimization.num_examples = args.num_examples
    if args.teacher_model:
        dspy_config.optimization.teacher_model = args.teacher_model

    # Determine prompts directory
    prompts_dir = Path(__file__).parent.parent / "prompts"

    # Create optimizer
    optimizer = PromptOptimizer(dspy_config, prompts_dir, use_seeds=args.use_seeds)

    # Run optimization
    try:
        print("\n" + "=" * 60)
        print("DSPy Prompt Optimization")
        print("=" * 60 + "\n")

        print(f"Prompts to optimize: {', '.join(args.prompts)}")
        print(f"Trajectories directories: {trajectories_dirs}")
        print(f"Starting from: {'GEPA-style seeds' if args.use_seeds else 'Baseline templates'}")
        print(f"Optimizer: {dspy_config.optimization.optimizer}")
        print(f"Teacher model: {dspy_config.optimization.teacher_model}")
        print(f"Max training examples: {dspy_config.optimization.num_examples}")
        print(f"Validation split: {dspy_config.optimization.validation_split}")
        print(f"Metric weights: {dspy_config.optimization.metric_weights}")

        if args.dry_run:
            print("\n[DRY RUN MODE - No actual optimization will be performed]\n")

        result = optimizer.optimize(
            prompt_names=args.prompts,
            trajectories_dirs=trajectories_dirs,
            output_dir=args.output_dir,
            dry_run=args.dry_run,
        )

        # Print results
        print("\n" + "=" * 60)
        print("Optimization Results")
        print("=" * 60 + "\n")

        if result.get("dry_run"):
            print("Dry run completed successfully!")
            print(f"  Would optimize {len(args.prompts)} prompts")
            print(f"  Training examples: {result['train_examples']}")
            print(f"  Validation examples: {result['val_examples']}")
        else:
            print("✓ Optimization completed successfully!")
            print(f"  Output directory: {result['output_dir']}")
            print(f"  Training examples: {result['train_examples']}")
            print(f"  Validation examples: {result['val_examples']}")

            print("\nPer-prompt results:")
            for prompt_name, prompt_result in result.get("results", {}).items():
                if prompt_result.get("success"):
                    val_score = prompt_result.get("validation_score")
                    score_str = f"{val_score:.3f}" if val_score is not None else "N/A"
                    print(f"  ✓ {prompt_name}: validation score = {score_str}")
                else:
                    error = prompt_result.get("error", "Unknown error")
                    print(f"  ✗ {prompt_name}: {error}")

            print(f"\nOptimized prompts saved to: {result['output_dir']}")
            print("To use optimized prompts, update sds.toml:")
            print("  [dspy]")
            print("  use_optimized = true")
            print(f'  optimized_version = "{Path(result["output_dir"]).name}"')

        return 0

    except ValueError as e:
        print(f"\nError: {e}", file=sys.stderr)
        return 1
    except RuntimeError as e:
        print(f"\nError: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"\nUnexpected error: {e}", file=sys.stderr)
        import traceback

        traceback.print_exc()
        return 1
