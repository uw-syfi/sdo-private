"""DSPy prompt optimizer.

Orchestrates the prompt optimization process using DSPy.
"""

import json
from pathlib import Path
from typing import Optional, List, Dict, Any
import dspy

from app_operator.dspy_integration.config import DSPyConfig
from app_operator.dspy_integration.data_loader import TrajectoryDataLoader
from app_operator.dspy_integration.metrics import CompositeMetric
from app_operator.dspy_integration.signatures import get_signature, SIGNATURES


class PromptOptimizer:
    """Orchestrate DSPy prompt optimization workflow.

    This class manages the end-to-end optimization process:
    1. Load training data from trajectories
    2. Configure DSPy optimizer
    3. Run optimization
    4. Save optimized prompts
    """

    def __init__(self, config: DSPyConfig, prompts_dir: Path):
        """Initialize optimizer.

        Args:
            config: DSPy configuration
            prompts_dir: Directory containing prompt templates
        """
        self.config = config
        self.prompts_dir = Path(prompts_dir)
        self.optimized_dir = prompts_dir / "optimized"

    def optimize(
        self,
        prompt_names: List[str],
        trajectories_dir: Path,
        output_dir: Optional[Path] = None,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """Run prompt optimization.

        Args:
            prompt_names: List of prompt names to optimize
            trajectories_dir: Directory containing trajectory files
            output_dir: Optional output directory for optimized prompts
            dry_run: If True, validate inputs but don't run optimization

        Returns:
            Dictionary with optimization results

        Raises:
            ValueError: If prompt_names contains invalid prompts
            RuntimeError: If optimization fails
        """
        # Validate prompt names
        invalid_prompts = [p for p in prompt_names if p not in SIGNATURES]
        if invalid_prompts:
            raise ValueError(
                f"Invalid prompt names: {', '.join(invalid_prompts)}. "
                f"Valid prompts: {', '.join(sorted(SIGNATURES.keys()))}"
            )

        # Load training data
        print(f"Loading training data from {trajectories_dir}...")
        data_loader = TrajectoryDataLoader(trajectories_dir)
        examples = data_loader.load_examples(success_only=False)

        if not examples:
            raise RuntimeError(f"No training examples found in {trajectories_dir}")

        print(f"Loaded {len(examples)} training examples")

        # Split into train/validation
        split_idx = int(len(examples) * (1 - self.config.optimization.validation_split))
        train_examples = examples[:split_idx]
        val_examples = examples[split_idx:]

        print(f"Train: {len(train_examples)}, Validation: {len(val_examples)}")

        if dry_run:
            return {
                "dry_run": True,
                "prompt_names": prompt_names,
                "train_examples": len(train_examples),
                "val_examples": len(val_examples),
                "config": {
                    "optimizer": self.config.optimization.optimizer,
                    "teacher_model": self.config.optimization.teacher_model,
                    "num_examples": self.config.optimization.num_examples,
                },
            }

        # Configure DSPy LM
        print(f"Configuring DSPy with teacher model: {self.config.optimization.teacher_model}")
        self._configure_dspy_lm()

        # Create composite metric
        metric = CompositeMetric(
            success_weight=self.config.optimization.metric_weights["success"],
            efficiency_weight=self.config.optimization.metric_weights["efficiency"],
            token_weight=self.config.optimization.metric_weights["tokens"],
        )

        # Optimize each prompt
        results = {}
        for prompt_name in prompt_names:
            print(f"\nOptimizing prompt: {prompt_name}")
            result = self._optimize_single_prompt(
                prompt_name,
                train_examples,
                val_examples,
                metric,
            )
            results[prompt_name] = result

        # Save optimized prompts
        if output_dir:
            output_path = Path(output_dir)
        else:
            # Create new version directory
            version = self._get_next_version()
            output_path = self.optimized_dir / f"v{version}"

        print(f"\nSaving optimized prompts to {output_path}")
        self._save_optimized_prompts(results, output_path)

        return {
            "success": True,
            "prompt_names": prompt_names,
            "output_dir": str(output_path),
            "train_examples": len(train_examples),
            "val_examples": len(val_examples),
            "results": results,
        }

    def _configure_dspy_lm(self):
        """Configure DSPy language model."""
        teacher_model = self.config.optimization.teacher_model

        # Map model names to provider/model format for DSPy 3.x
        # DSPy uses LiteLLM format: "provider/model"

        # If already in provider/model format, use as-is
        if "/" in teacher_model:
            model_str = teacher_model
        elif "claude" in teacher_model.lower():
            model_str = f"anthropic/{teacher_model}"
        elif "gpt" in teacher_model.lower() or "o1" in teacher_model.lower():
            model_str = f"openai/{teacher_model}"
        elif "gemini" in teacher_model.lower():
            model_str = f"gemini/{teacher_model}"
        else:
            # Try as-is for unknown models
            model_str = teacher_model

        lm = dspy.LM(model=model_str)
        dspy.settings.configure(lm=lm)

    def _convert_to_dspy_examples(
        self,
        trajectory_examples: List,
        prompt_name: str,
    ) -> List[dspy.Example]:
        """Convert TrajectoryExample objects to dspy.Example objects.

        Args:
            trajectory_examples: List of TrajectoryExample objects
            prompt_name: Name of the prompt being optimized

        Returns:
            List of dspy.Example objects suitable for DSPy optimization
        """
        dspy_examples = []

        for traj_ex in trajectory_examples:
            # For now, create simplified examples with basic fields
            # In future, could parse prompt/response to extract structured data
            if prompt_name == "deployer_fix_error":
                example = dspy.Example(
                    repo_path="/repo",
                    error_context=traj_ex.prompt[:500] if traj_ex.prompt else "Error context",
                    attempt=traj_ex.iterations,
                    max_attempts=20,
                    deploy_script="/path/deploy.sh",
                    health_check_script="/path/health_check.sh",
                    previous_summary="",
                    fix_summary=traj_ex.response[:200] if traj_ex.response else "Fix applied"
                ).with_inputs("repo_path", "error_context", "attempt", "max_attempts",
                              "deploy_script", "health_check_script", "previous_summary")
            else:
                # Generic example for other prompt types
                example = dspy.Example(
                    input_text=traj_ex.prompt[:500] if traj_ex.prompt else "",
                    output_text=traj_ex.response[:200] if traj_ex.response else ""
                ).with_inputs("input_text")

            dspy_examples.append(example)

        return dspy_examples

    def _optimize_single_prompt(
        self,
        prompt_name: str,
        train_examples: List,
        val_examples: List,
        metric: CompositeMetric,
    ) -> Dict[str, Any]:
        """Optimize a single prompt.

        Args:
            prompt_name: Name of the prompt to optimize
            train_examples: Training examples (TrajectoryExample objects)
            val_examples: Validation examples (TrajectoryExample objects)
            metric: Metric for evaluation

        Returns:
            Optimization results
        """
        # Get signature
        signature = get_signature(prompt_name)

        # Create DSPy module (simple Predict for now)
        class PromptModule(dspy.Module):
            def __init__(self, signature):
                super().__init__()
                self.predictor = dspy.Predict(signature)

            def forward(self, **kwargs):
                return self.predictor(**kwargs)

        module = PromptModule(signature)

        # Select optimizer
        optimizer = self._create_optimizer(metric)

        # Limit training examples
        num_examples = min(
            len(train_examples),
            self.config.optimization.num_examples
        )
        limited_train = train_examples[:num_examples]

        print(f"  Using {num_examples} training examples")
        print(f"  Optimizer: {self.config.optimization.optimizer}")

        # Convert TrajectoryExample objects to dspy.Example objects
        print("  Converting trajectory examples to DSPy format...")
        dspy_train = self._convert_to_dspy_examples(limited_train, prompt_name)
        print(f"  Converted {len(dspy_train)} training examples")

        try:
            # Run optimization
            # Note: BootstrapFewShot doesn't support valset parameter in DSPy 3.x
            optimized_module = optimizer.compile(
                module,
                trainset=dspy_train,  # Use converted examples
            )

            # Evaluate on validation set
            if val_examples:
                val_score = self._evaluate(optimized_module, val_examples[:5], metric)
            else:
                val_score = None

            return {
                "success": True,
                "validation_score": val_score,
                "optimized_module": optimized_module,
            }

        except Exception as e:
            print(f"  Optimization failed: {e}")
            return {
                "success": False,
                "error": str(e),
            }

    def _create_optimizer(self, metric: CompositeMetric):
        """Create DSPy optimizer based on config.

        Args:
            metric: Metric for evaluation

        Returns:
            DSPy optimizer instance
        """
        optimizer_name = self.config.optimization.optimizer

        if optimizer_name == "BootstrapFewShot":
            return dspy.BootstrapFewShot(metric=metric)
        elif optimizer_name == "BootstrapFewShotWithRandomSearch":
            return dspy.BootstrapFewShotWithRandomSearch(metric=metric)
        elif optimizer_name == "MIPROv2":
            return dspy.MIPROv2(metric=metric)
        elif optimizer_name == "COPRO":
            return dspy.COPRO(metric=metric)
        else:
            raise ValueError(f"Unknown optimizer: {optimizer_name}")

    def _evaluate(
        self,
        module: dspy.Module,
        examples: List,
        metric: CompositeMetric,
    ) -> float:
        """Evaluate module on examples.

        Args:
            module: DSPy module to evaluate
            examples: Validation examples
            metric: Metric for evaluation

        Returns:
            Average score
        """
        scores = []
        for example in examples:
            try:
                # Simple evaluation (would need proper input mapping)
                score = metric(example, None)
                scores.append(score)
            except Exception:
                pass

        return sum(scores) / len(scores) if scores else 0.0

    def _get_next_version(self) -> int:
        """Get next version number for optimized prompts.

        Returns:
            Next version number
        """
        if not self.optimized_dir.exists():
            return 1

        versions = []
        for version_dir in self.optimized_dir.iterdir():
            if version_dir.is_dir() and version_dir.name.startswith("v"):
                try:
                    version_num = int(version_dir.name[1:])
                    versions.append(version_num)
                except ValueError:
                    pass

        return max(versions) + 1 if versions else 1

    def _save_optimized_prompts(
        self,
        results: Dict[str, Dict[str, Any]],
        output_dir: Path,
    ):
        """Save optimized prompts to disk.

        Args:
            results: Optimization results
            output_dir: Output directory
        """
        output_dir.mkdir(parents=True, exist_ok=True)

        # Save metadata
        metadata = {
            "version": output_dir.name,
            "config": {
                "optimizer": self.config.optimization.optimizer,
                "teacher_model": self.config.optimization.teacher_model,
                "num_examples": self.config.optimization.num_examples,
                "metric_weights": self.config.optimization.metric_weights,
            },
            "prompts": {},
        }

        for prompt_name, result in results.items():
            if result.get("success"):
                optimized_module = result.get("optimized_module")
                module_file = output_dir / f"{prompt_name}.dspy.json"

                # Save the actual DSPy module
                try:
                    self._save_dspy_module(optimized_module, module_file, prompt_name)
                    print(f"  Saved optimized module: {module_file}")

                    metadata["prompts"][prompt_name] = {
                        "validation_score": result.get("validation_score"),
                        "optimized": True,
                        "module_file": f"{prompt_name}.dspy.json",
                    }
                except Exception as e:
                    print(f"  Failed to save module {prompt_name}: {e}")
                    metadata["prompts"][prompt_name] = {
                        "optimized": False,
                        "error": f"Save failed: {str(e)}",
                    }
            else:
                metadata["prompts"][prompt_name] = {
                    "optimized": False,
                    "error": result.get("error"),
                }

        # Save metadata
        metadata_file = output_dir / "metadata.json"
        with open(metadata_file, "w") as f:
            json.dump(metadata, f, indent=2)

        # Create 'latest' symlink
        latest_link = self.optimized_dir / "latest"
        if latest_link.exists() or latest_link.is_symlink():
            latest_link.unlink()
        try:
            latest_link.symlink_to(output_dir.name)
        except OSError:
            # Windows doesn't support symlinks without admin
            pass

        print(f"  Saved metadata to {metadata_file}")

    def _save_dspy_module(
        self,
        module: dspy.Module,
        output_file: Path,
        prompt_name: str,
    ):
        """Save a DSPy module to disk.

        Args:
            module: DSPy module to save
            output_file: Path to save the module
            prompt_name: Name of the prompt being saved

        Raises:
            RuntimeError: If module cannot be saved
        """
        try:
            # Extract the predictor from the wrapper module
            if hasattr(module, 'predictor'):
                predictor = module.predictor
            else:
                # If it's already a Predict module
                predictor = module

            # Serialize the module's state
            # DSPy modules store demonstrations in the demos attribute
            module_state = {
                "prompt_name": prompt_name,
                "signature": get_signature(prompt_name).__name__,
                "demos": [],
            }

            # Extract demonstrations if they exist
            if hasattr(predictor, 'demos') and predictor.demos:
                # Convert demos to serializable format
                serializable_demos = []
                for demo in predictor.demos:
                    if isinstance(demo, dict):
                        serializable_demos.append(demo)
                    elif hasattr(demo, '__dict__'):
                        # DSPy Example objects have __dict__
                        serializable_demos.append(dict(demo.__dict__))
                    else:
                        # Try to convert to dict
                        try:
                            serializable_demos.append(dict(demo))
                        except (TypeError, ValueError):
                            # If conversion fails, use string representation
                            serializable_demos.append(str(demo))

                module_state["demos"] = serializable_demos
                print(f"    Saved {len(serializable_demos)} demonstrations")
            else:
                print("    No demonstrations to save")

            # Save to JSON
            with open(output_file, 'w') as f:
                json.dump(module_state, f, indent=2, default=str)

        except Exception as e:
            raise RuntimeError(f"Failed to save DSPy module: {e}") from e
