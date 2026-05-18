import json
from pathlib import Path

import pytest

from app_operator.dspy_integration._enriched_data_loader import (
    EnrichedTrajectoryDataLoader,
    extract_prompt_kwargs_from_content,
    get_example_counts_by_prompt,
)
from app_operator.trajectory import Phase


@pytest.fixture
def enriched_trajectory_file(tmp_path: Path) -> Path:
    traj_dir = tmp_path / "traj1"
    traj_dir.mkdir()
    traj_file = traj_dir / "enriched_trajectory.json"
    traj_data = {
        "_enriched": True,
        "metadata": {"status": "completed"},
        "deployment": [
            {
                "call_id": 1,
                "prompt_kwargs": {"attempt": 1},
                "_sessions": [
                    {
                        "prompt_type": "deployer_fix_error",
                        "rendered_prompt": "Deployment failed. Attempt 1 of 5.",
                        "messages": [],
                        "session_file": "session1.json",
                        "session_start": "2024-01-01T00:00:00",
                        "num_messages": 1,
                    }
                ],
            }
        ],
    }
    traj_file.write_text(json.dumps(traj_data))
    return traj_dir


def test_load_examples(enriched_trajectory_file: Path):
    loader = EnrichedTrajectoryDataLoader(enriched_trajectory_file)
    examples = loader.load_examples()
    assert len(examples) == 1
    example = examples[0]
    assert example.phase == Phase.DEPLOYMENT
    assert example.prompt_name == "deployer_fix_error"
    assert example.prompt_kwargs is not None
    assert example.prompt_kwargs["attempt"] == 1


def test_extract_prompt_kwargs():
    prompt = "Deployment has failed on attempt: 2 of 10. Error: service not available."
    kwargs = extract_prompt_kwargs_from_content("deployer_fix_error", prompt, [])
    assert kwargs["attempt"] == 2
    assert kwargs["max_attempts"] == 10
    assert "service not available" in kwargs["error_context"]


def test_get_example_counts_by_prompt(enriched_trajectory_file: Path):
    loader = EnrichedTrajectoryDataLoader(enriched_trajectory_file)
    examples = loader.load_examples()
    counts = get_example_counts_by_prompt(examples)
    assert counts["deployer_fix_error"] == 1
