"""Data loader for enriched trajectories that includes Gemini session data.

This loader extracts training examples from trajectories enriched with full
Gemini session conversations, providing richer training data than the standard
trajectory-only loader.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app_operator.trajectory import Phase
from app_operator.types import TokenUsage


@dataclass
class EnrichedTrajectoryExample:
    """Training example enriched with full Gemini session data.

    This extends the standard TrajectoryExample with:
    - Full conversation history from Gemini sessions
    - Complete user prompts (not just kwargs)
    - Tool call sequences
    - Token usage statistics
    """

    call_id: int
    phase: Phase
    prompt_name: str  # e.g., 'deployer_fix_error'
    rendered_prompt: str  # The full prompt sent to the agent
    prompt_kwargs: dict[str, Any] | None = None  # Original template kwargs

    # Enriched data from Gemini sessions
    full_conversation: list[dict[str, Any]] = field(default_factory=list)
    token_usage: TokenUsage = field(
        default_factory=lambda: TokenUsage(prompt_tokens=0, completion_tokens=0, total_tokens=0)
    )
    session_metadata: dict[str, Any] = field(default_factory=dict)

    # Success metrics (from trajectory)
    success: bool = False
    iteration_count: int = 0
    deployment_successful: bool = False


def extract_prompt_kwargs_from_content(
    prompt_type: str, rendered_prompt: str, conversation: list[dict]
) -> dict[str, Any]:
    """Extract prompt kwargs by reverse-engineering from rendered prompt.

    This attempts to extract the original template variables from the
    fully-rendered prompt text.
    """
    kwargs = {}

    if prompt_type == "deployer_fix_error":
        # Look for common patterns
        import re

        # Extract attempt number
        match = re.search(r"attempt[:\s]+(\d+)", rendered_prompt.lower())
        if match:
            kwargs["attempt"] = int(match.group(1))

        # Extract max_attempts
        match = re.search(r"of[:\s]+(\d+)", rendered_prompt.lower())
        if match:
            kwargs["max_attempts"] = int(match.group(1))

        # Look for error context (usually after "Error:")
        if "error" in rendered_prompt.lower():
            # Extract section after deployment failure mention
            parts = rendered_prompt.split("\n")
            error_lines = [line for line in parts if "error" in line.lower() or "failed" in line.lower()]
            if error_lines:
                kwargs["error_context"] = "\n".join(error_lines[:10])  # First 10 error lines

        # Look for file paths
        deploy_script_match = re.search(r"\.sds/deploy\.sh|deploy\.sh", rendered_prompt)
        if deploy_script_match:
            kwargs["deploy_script"] = ".sds/deploy.sh"

        health_check_match = re.search(r"\.sds/health_check\.sh|health_check\.sh", rendered_prompt)
        if health_check_match:
            kwargs["health_check_script"] = ".sds/health_check.sh"

    elif prompt_type == "deployer_summarize":
        # For summarize, the output_snippet should be extracted
        # Look for deployment output in the prompt
        lines = rendered_prompt.split("\n")
        # Usually the instruction is first, then the output to summarize
        if len(lines) > 2:
            kwargs["output_snippet"] = "\n".join(lines[2:])  # Skip instruction lines

    elif prompt_type == "code_analyzer_user":
        # Extract repo_path if mentioned
        import re

        match = re.search(r"repository[:\s]+(\S+)", rendered_prompt.lower())
        if match:
            kwargs["repo_path"] = match.group(1)

    return kwargs


class EnrichedTrajectoryDataLoader:
    """Load training examples from enriched trajectory files."""

    def __init__(self, enriched_dir: Path):
        """Initialize loader with enriched trajectories directory.

        Args:
            enriched_dir: Directory containing enriched_trajectory.json files
        """
        self.enriched_dir = Path(enriched_dir)

    def load_examples(self, success_only: bool = False, phase: Phase | None = None) -> list[EnrichedTrajectoryExample]:
        """Load training examples from all enriched trajectories.

        Args:
            success_only: Only load examples from successful runs
            phase: Optional phase filter (e.g., Phase.DEPLOYMENT)

        Returns:
            list of enriched training examples
        """
        examples = []

        # Find all enriched trajectory files
        traj_files = list(self.enriched_dir.glob("**/enriched_trajectory.json"))

        for traj_file in traj_files:
            try:
                examples.extend(self._load_from_file(traj_file, success_only, phase))
            except Exception as e:
                print(f"Warning: Failed to load {traj_file}: {e}")

        return examples

    def _load_from_file(
        self, traj_file: Path, success_only: bool, phase_filter: Phase | None
    ) -> list[EnrichedTrajectoryExample]:
        """Load examples from a single enriched trajectory file."""

        with open(traj_file) as f:
            traj = json.load(f)

        if not traj.get("_enriched"):
            raise ValueError(f"{traj_file} is not an enriched trajectory")

        examples = []
        metadata = traj["metadata"]
        overall_success = metadata.get("status") == "completed"

        if success_only and not overall_success:
            return examples

        # Process each phase
        for phase_key in ["exploration", "script_generation", "deployment", "monitoring"]:
            # Filter by phase if specified
            if phase_filter and phase_key != phase_filter.value:
                continue

            phase_entries = traj.get(phase_key, [])

            for entry in phase_entries:
                # Get sessions for this call
                sessions = entry.get("_sessions", [])

                if not sessions:
                    # No enriched data, skip
                    continue

                # Create an example for each session (each represents a different prompt)
                for session in sessions:
                    prompt_type = session["prompt_type"]

                    if prompt_type == "unknown":
                        continue  # Skip unidentified prompts

                    # Extract kwargs (use original if available, else reverse-engineer)
                    prompt_kwargs = entry.get("prompt_kwargs")
                    if not prompt_kwargs:
                        # Try to extract from session data
                        prompt_kwargs = session.get("prompt_kwargs", {})
                        if not prompt_kwargs.get("_rendered_prompt"):
                            # Reverse-engineer from rendered prompt
                            prompt_kwargs = extract_prompt_kwargs_from_content(
                                prompt_type, session["rendered_prompt"], session["messages"]
                            )

                    # Create enriched example
                    example = EnrichedTrajectoryExample(
                        call_id=entry["call_id"],
                        phase=Phase(phase_key),
                        prompt_name=prompt_type,
                        rendered_prompt=session["rendered_prompt"],
                        prompt_kwargs=prompt_kwargs,
                        full_conversation=session["messages"],
                        token_usage=prompt_kwargs.get("_token_usage", {}),
                        session_metadata={
                            "session_file": session["session_file"],
                            "session_start": session["session_start"],
                            "num_messages": session["num_messages"],
                        },
                        success=overall_success,
                        deployment_successful=overall_success,
                    )

                    examples.append(example)

        return examples


def get_example_counts_by_prompt(examples: list[EnrichedTrajectoryExample]) -> dict[str, int]:
    """Count examples by prompt type."""
    from collections import Counter

    return dict(Counter(ex.prompt_name for ex in examples))


if __name__ == "__main__":
    # Test the loader
    import sys
    from pathlib import Path

    enriched_dir = Path("enriched_trajectories/opt2")

    if not enriched_dir.exists():
        print(f"Error: {enriched_dir} not found. Run enrich_trajectories_from_sessions.py first.")
        sys.exit(1)

    loader = EnrichedTrajectoryDataLoader(enriched_dir)
    examples = loader.load_examples()

    print(f"\nLoaded {len(examples)} enriched examples from {enriched_dir}")
    print("\nBy prompt type:")
    for prompt_type, count in sorted(get_example_counts_by_prompt(examples).items()):
        print(f"  {prompt_type}: {count}")

    # Show sample
    if examples:
        print("\nSample example (deployer_fix_error):")
        fix_error_examples = [ex for ex in examples if ex.prompt_name == "deployer_fix_error"]
        if fix_error_examples:
            ex = fix_error_examples[0]
            print(f"  Prompt kwargs: {list(ex.prompt_kwargs.keys()) if ex.prompt_kwargs else []}")
            print(f"  Rendered prompt: {ex.rendered_prompt[:150]}...")
            print(f"  Full conversation: {len(ex.full_conversation)} messages")
            print(f"  Success: {ex.success}")
