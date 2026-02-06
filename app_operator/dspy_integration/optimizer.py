"""DSPy prompt optimizer.

Orchestrates the prompt optimization process using DSPy.
"""

import json
from pathlib import Path
from typing import Optional, List, Dict, Any
import dspy

from app_operator.dspy_integration.config import DSPyConfig
from app_operator.dspy_integration.data_loader import TrajectoryDataLoader
from app_operator.dspy_integration.metrics import CompositeMetric, GroundTruthSimilarityMetric
from app_operator.dspy_integration.signatures import get_signature, SIGNATURES
from app_operator.dspy_integration.field_mappings import (
    map_kwargs_to_fields,
    get_output_field_name,
)


class _MetricCallTracker:
    """Wraps a metric to count successful invocations during compile.

    BootstrapFewShot calls the metric only when the teacher LM succeeds.
    If call_count is 0 after compile, every example failed before evaluation
    (e.g. due to auth errors) and no real bootstrapping occurred.
    """

    def __init__(self, metric: CompositeMetric):
        self.metric = metric
        self.call_count = 0

    def __call__(self, *args, **kwargs):
        result = self.metric(*args, **kwargs)
        self.call_count += 1
        return result


PROMPT_PHASE_MAP: Dict[str, str] = {
    "deployer_system": "deployment",
    "deployer_fix_error": "deployment",
    "deployer_summarize": "deployment",
    "deployer_generate_script": "script_generation",
    "code_analyzer_system": "exploration",
    "code_analyzer_user": "exploration",
    "monitor_analyze_health": "monitoring",
    "agentflow_system": "script_generation",
    "agentflow_user": "script_generation",
    "agentflow_repair": "script_generation",
}

# Reverse map: prompt name → Jinja2 template path.
# Used to re-render ground-truth prompts from stored prompt_kwargs when
# rendered_prompt was not recorded in the trajectory.
PROMPT_TO_TEMPLATE: Dict[str, str] = {
    "deployer_system": "deployer/system.jinja2",
    "deployer_fix_error": "deployer/fix_error.jinja2",
    "deployer_summarize": "deployer/summarize.jinja2",
    "deployer_generate_script": "deployer/generate_script.jinja2",
    "code_analyzer_system": "code_analyzer/system.jinja2",
    "code_analyzer_user": "code_analyzer/user.jinja2",
    "monitor_analyze_health": "monitor/analyze_health.jinja2",
    "agentflow_system": "agentflow/system.jinja2",
    "agentflow_user": "agentflow/user.jinja2",
    "agentflow_repair": "agentflow/repair.jinja2",
}


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
        trajectories_dirs: List[Path],
        output_dir: Optional[Path] = None,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """Run prompt optimization.

        Args:
            prompt_names: List of prompt names to optimize
            trajectories_dirs: Directories containing trajectory files;
                examples from all directories are merged before optimization.
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

        # Load training data from all provided directories
        print(f"Loading training data from {trajectories_dirs}...")
        examples = []
        for tdir in trajectories_dirs:
            data_loader = TrajectoryDataLoader(tdir)
            examples.extend(data_loader.load_examples(success_only=False))

        if not examples:
            raise RuntimeError(f"No training examples found in {trajectories_dirs}")

        print(f"Loaded {len(examples)} training examples")

        if dry_run:
            split_idx = int(len(examples) * (1 - self.config.optimization.validation_split))
            return {
                "dry_run": True,
                "prompt_names": prompt_names,
                "train_examples": split_idx,
                "val_examples": len(examples) - split_idx,
                "config": {
                    "optimizer": self.config.optimization.optimizer,
                    "teacher_model": self.config.optimization.teacher_model,
                    "num_examples": self.config.optimization.num_examples,
                },
            }

        # Configure DSPy LM
        print(f"Configuring DSPy with teacher model: {self.config.optimization.teacher_model}")
        self._configure_dspy_lm()

        # Use GroundTruthSimilarityMetric for the prediction-quality slot:
        # instant, deterministic, no LM call, and immune to the
        # self-evaluation bias that plagues LLM judges when the same model
        # generates both the prediction and the score.
        metric_weights = self.config.optimization.metric_weights

        # Support both legacy (3 weights) and new (4 weights with health_check) format
        if "health_check" in metric_weights:
            metric = CompositeMetric(
                success_weight=metric_weights["success"],
                efficiency_weight=metric_weights["efficiency"],
                token_weight=metric_weights["tokens"],
                health_check_weight=metric_weights["health_check"],
                prediction_metric=GroundTruthSimilarityMetric(),
                include_health_check_quality=True,
            )
        else:
            # Legacy format without health check quality (backward compatibility)
            metric = CompositeMetric(
                success_weight=metric_weights["success"],
                efficiency_weight=metric_weights["efficiency"],
                token_weight=metric_weights["tokens"],
                prediction_metric=GroundTruthSimilarityMetric(),
                include_health_check_quality=False,
            )

        # Optimize each prompt
        results = {}
        for prompt_name in prompt_names:
            # Filter examples to the relevant phase for this prompt, then
            # split into train/validation.  The split must happen *after*
            # phase filtering so that small phases don't end up with zero
            # validation examples while unrelated phases consume the budget.
            target_phase = PROMPT_PHASE_MAP.get(prompt_name)
            if target_phase:
                phase_examples = [e for e in examples if e.phase == target_phase]
            else:
                phase_examples = examples

            if not phase_examples:
                print(f"\n  Skipping {prompt_name}: no training examples in phase '{target_phase}'")
                results[prompt_name] = {"success": False,
                                        "error": f"No training examples for phase '{target_phase}'"}
                continue

            # Guarantee at least one training example.  Validation is skipped
            # when the phase is too small to split meaningfully.
            split_idx = max(1, int(len(phase_examples) *
                            (1 - self.config.optimization.validation_split)))
            prompt_train = phase_examples[:split_idx]
            prompt_val = phase_examples[split_idx:]

            print(
                f"\nOptimizing prompt: {prompt_name} (phase={target_phase}, train={
                    len(prompt_train)}, val={
                    len(prompt_val)})")
            result = self._optimize_single_prompt(
                prompt_name,
                prompt_train,
                prompt_val,
                metric,
            )
            results[prompt_name] = result

        # Bail out if every prompt failed — nothing worth saving
        if not any(r.get("success") for r in results.values()):
            errors = {name: r.get("error", "unknown error")
                      for name, r in results.items()}
            raise RuntimeError(
                f"All prompts failed optimization: {errors}"
            )

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
            "train_examples": len(examples),
            "val_examples": 0,  # split is per-prompt after phase filtering
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

        # Disable LiteLLM's request-level cache so that COPRO candidates
        # with different instructions are not served stale responses.
        lm = dspy.LM(model=model_str, cache=False)
        dspy.settings.configure(lm=lm)

    def _convert_to_dspy_examples(
        self,
        trajectory_examples: List,
        prompt_name: str,
    ) -> List[dspy.Example]:
        """Convert TrajectoryExample objects to dspy.Example objects.

        Uses signature-driven generic conversion. Input fields are populated
        from prompt_kwargs via map_kwargs_to_fields. The output field is
        populated from rendered_prompt (the ground-truth instruction prompt
        recorded at render time), falling back to an empty string when not
        available. Examples without prompt_kwargs are skipped.

        Args:
            trajectory_examples: List of TrajectoryExample objects
            prompt_name: Name of the prompt being optimized

        Returns:
            List of dspy.Example objects suitable for DSPy optimization
        """
        signature = get_signature(prompt_name)
        input_field_names = list(signature.input_fields.keys())
        output_field_name = get_output_field_name(prompt_name)

        dspy_examples = []
        skipped_no_kwargs = 0
        skipped_wrong_prompt = 0
        for traj_ex in trajectory_examples:
            if traj_ex.prompt_kwargs is None:
                skipped_no_kwargs += 1
                continue

            # Map recorded kwargs through explicit + auto mappings
            mapped = map_kwargs_to_fields(prompt_name, traj_ex.prompt_kwargs)
            # Filter to only fields declared in the signature
            fields = {k: mapped[k] for k in input_field_names if k in mapped}

            # If none of the recorded kwargs match any input field this
            # conversation belongs to a *different* prompt that shares the
            # same phase (e.g. fix_error examples in the deployment phase
            # when optimising deployer_summarize).  Using them would inject
            # garbage into training — skip.
            if not fields:
                skipped_wrong_prompt += 1
                continue

            # Fill any missing input fields with empty string
            for name in input_field_names:
                if name not in fields:
                    fields[name] = ""

            # Set the output field: prefer recorded rendered_prompt; fall back
            # to re-rendering the Jinja2 template from stored prompt_kwargs.
            if traj_ex.rendered_prompt:
                fields[output_field_name] = traj_ex.rendered_prompt
            else:
                fields[output_field_name] = self._rerender_from_kwargs(
                    prompt_name, traj_ex.prompt_kwargs
                )

            example = dspy.Example(**fields).with_inputs(*input_field_names)
            dspy_examples.append(example)

        if skipped_no_kwargs:
            print(
                f"  Skipped {skipped_no_kwargs} example(s) missing prompt_kwargs")
        if skipped_wrong_prompt:
            print(
                f"  Skipped {skipped_wrong_prompt} example(s) from a different prompt in the same phase")

        return dspy_examples

    def _rerender_from_kwargs(self, prompt_name: str, prompt_kwargs: Dict[str, Any]) -> str:
        """Re-render a Jinja2 template from stored prompt_kwargs.

        Used as fallback when rendered_prompt was not recorded in the
        trajectory (i.e. trajectories produced before that recording was
        added).  Silently returns empty string on any rendering error.

        Args:
            prompt_name: Name of the prompt
            prompt_kwargs: Stored kwargs from the trajectory

        Returns:
            Rendered prompt string, or empty string on failure
        """
        template_name = PROMPT_TO_TEMPLATE.get(prompt_name)
        if not template_name:
            return ""

        try:
            from app_operator.prompts import PromptLoader

            loader = PromptLoader(templates_dir=self.prompts_dir / "templates")
            return loader._render_jinja2(template_name, prompt_kwargs)
        except Exception:
            return ""

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

        if not dspy_train:
            print(f"  No usable training examples after conversion for {prompt_name}")
            return {
                "success": False,
                "error": f"No examples with matching kwargs for '{prompt_name}'. "
                "The trajectory may not contain any conversations that used this prompt.",
            }

        # Wrap metric to detect whether the teacher LM ran at all
        tracking_metric = _MetricCallTracker(metric)

        # Select optimizer — pass len(dspy_train) so COPRO can set depth
        optimizer = self._create_optimizer(tracking_metric, num_train_examples=len(dspy_train))

        try:
            # Convert validation examples for optimizers that need valset
            dspy_val = self._convert_to_dspy_examples(val_examples, prompt_name)

            # Dispatch compile args per optimizer type (DSPy 3.1.2 signatures)
            optimizer_name = self.config.optimization.optimizer
            if optimizer_name == "BootstrapFewShot":
                optimized_module = optimizer.compile(module, trainset=dspy_train)
            elif optimizer_name == "BootstrapFewShotWithRandomSearch":
                optimized_module = optimizer.compile(
                    module, trainset=dspy_train, valset=dspy_val
                )
            elif optimizer_name == "MIPROv2":
                optimized_module = optimizer.compile(
                    module, trainset=dspy_train, valset=dspy_val
                )
            elif optimizer_name == "COPRO":
                optimized_module = optimizer.compile(
                    module, trainset=dspy_train, eval_kwargs={"num_threads": 4}
                )
            else:
                optimized_module = optimizer.compile(module, trainset=dspy_train)

            # BootstrapFewShot silently swallows per-example failures and
            # populates demos from the training set even when the teacher LM
            # never ran successfully.  The metric wrapper is the reliable
            # signal: if it was never called, every example failed before
            # evaluation (e.g. auth error, bad provider string).
            if tracking_metric.call_count == 0:
                print("  Optimization produced 0 successful traces — teacher model likely failed")
                return {
                    "success": False,
                    "error": "Bootstrapping produced 0 successful traces. "
                             "Check teacher model auth and training data.",
                }

            # Evaluate on validation set
            if val_examples:
                val_score = self._evaluate(
                    optimized_module, val_examples[:5], metric, prompt_name
                )
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

    def _create_optimizer(self, metric: CompositeMetric, num_train_examples: int = 30):
        """Create DSPy optimizer based on config.

        Args:
            metric: Metric for evaluation
            num_train_examples: Number of converted training examples (used to
                tune COPRO depth — shallow depth avoids wasting LM calls when
                the training set is small).

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
            # Vertex AI caps candidateCount at 8; COPRO passes n=breadth
            # at depth > 0, so breadth must stay <= 8.
            # Use shallow depth when the training set is small to avoid
            # burning LM calls on refinement rounds that have no signal.
            depth = 2 if num_train_examples <= 10 else 3
            return dspy.COPRO(metric=metric, breadth=8, depth=depth)
        else:
            raise ValueError(f"Unknown optimizer: {optimizer_name}")

    def _evaluate(
        self,
        module: dspy.Module,
        examples: List,
        metric: CompositeMetric,
        prompt_name: str,
    ) -> Optional[float]:
        """Evaluate module on validation examples by invoking it.

        Each example is converted individually so that examples which lack
        prompt_kwargs (and therefore cannot be converted) are skipped without
        shifting the alignment between trajectory and DSPy examples.

        Args:
            module: DSPy module to evaluate
            examples: Validation examples (TrajectoryExample objects)
            metric: Metric for evaluation
            prompt_name: Name of the prompt (used to convert examples)

        Returns:
            Average score, or None when no examples convert to DSPy format.
        """
        scores = []
        for traj_ex in examples:
            converted = self._convert_to_dspy_examples([traj_ex], prompt_name)
            if not converted:
                continue
            dspy_ex = converted[0]
            try:
                prediction = module(**dict(dspy_ex.inputs()))
                score = metric(traj_ex, prediction)
                scores.append(score)
            except Exception:
                pass

        return sum(scores) / len(scores) if scores else None

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

            # Extract demonstrations if they exist (BootstrapFewShot)
            if hasattr(predictor, 'demos') and predictor.demos:
                serializable_demos = []
                for demo in predictor.demos:
                    if isinstance(demo, dict):
                        serializable_demos.append(demo)
                    elif hasattr(demo, '__dict__'):
                        serializable_demos.append(dict(demo.__dict__))
                    else:
                        try:
                            serializable_demos.append(dict(demo))
                        except (TypeError, ValueError):
                            serializable_demos.append(str(demo))

                module_state["demos"] = serializable_demos
                print(f"    Saved {len(serializable_demos)} demonstrations")

            # Extract the optimized instruction if present (COPRO / MIPROv2
            # rewrite predictor.signature.instructions; BootstrapFewShot does
            # not touch it).
            if hasattr(predictor, 'signature') and hasattr(
                predictor.signature, 'instructions'
            ):
                module_state["optimized_instruction"] = (
                    predictor.signature.instructions
                )
                print(
                    f"    Saved optimized instruction "
                    f"({len(predictor.signature.instructions)} chars)"
                )

            if not module_state["demos"] and "optimized_instruction" not in module_state:
                print("    No optimization artifacts to save")

            # Save to JSON
            with open(output_file, 'w') as f:
                json.dump(module_state, f, indent=2, default=str)

        except Exception as e:
            raise RuntimeError(f"Failed to save DSPy module: {e}") from e
