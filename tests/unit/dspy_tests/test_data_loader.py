"""Tests for TrajectoryDataLoader."""

import json
import pytest
from pathlib import Path
from app_operator.dspy_integration.data_loader import TrajectoryDataLoader, TrajectoryExample


@pytest.fixture
def sample_trajectory():
    """Sample trajectory data."""
    return {
        "metadata": {
            "repo_path": "/test/repo",
            "start_time": "2026-01-30 10:00:00",
            "end_time": "2026-01-30 10:10:00",
            "agent_name": "test-agent",
            "status": "completed",
            "run_id": "20260130-100000",
        },
        "calls": [
            {
                "call_id": 1,
                "phase": "deployment",
                "start_time": "2026-01-30 10:00:00",
                "end_time": "2026-01-30 10:05:00",
                "context": {},
            }
        ],
        "deployment": [
            {
                "call_id": 1,
                "messages": [
                    {
                        "role": "system",
                        "content": "You are a deployment agent",
                        "timestamp": "2026-01-30 10:00:00",
                    },
                    {
                        "role": "user",
                        "content": "Deploy the application",
                        "timestamp": "2026-01-30 10:00:01",
                    },
                    {
                        "role": "assistant",
                        "content": "Running deployment script",
                        "timestamp": "2026-01-30 10:00:02",
                        "duration_seconds": 1.5,
                    },
                    {
                        "role": "tool_call",
                        "tool": "bash",
                        "args": {"command": "./deploy.sh"},
                        "exit_code": 0,
                        "stdout": "Deployment successful",
                        "timestamp": "2026-01-30 10:05:00",
                        "duration_seconds": 290.0,
                    },
                ],
            }
        ],
        "monitoring": [],
        "script_generation": [],
        "gemini_sessions": [],
    }


@pytest.fixture
def trajectories_dir(tmp_path, sample_trajectory):
    """Create temp directory with trajectory files."""
    traj_dir = tmp_path / "trajectories"
    traj_dir.mkdir()

    # Write sample trajectory
    traj_file = traj_dir / "trajectory_20260130-100000.json"
    with open(traj_file, "w") as f:
        json.dump(sample_trajectory, f)

    return traj_dir


class TestTrajectoryDataLoader:
    """Tests for TrajectoryDataLoader class."""

    def test_init(self, tmp_path):
        """Test initialization."""
        loader = TrajectoryDataLoader(tmp_path)
        assert loader.trajectories_dir == tmp_path

    def test_load_trajectories_empty_dir(self, tmp_path):
        """Test loading from empty directory."""
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        loader = TrajectoryDataLoader(empty_dir)
        trajectories = loader.load_trajectories()
        assert trajectories == []

    def test_load_trajectories_nonexistent_dir(self, tmp_path):
        """Test loading from nonexistent directory."""
        loader = TrajectoryDataLoader(tmp_path / "nonexistent")
        trajectories = loader.load_trajectories()
        assert trajectories == []

    def test_load_trajectories_success(self, trajectories_dir):
        """Test successfully loading trajectories."""
        loader = TrajectoryDataLoader(trajectories_dir)
        trajectories = loader.load_trajectories()

        assert len(trajectories) == 1
        assert trajectories[0]["metadata"]["run_id"] == "20260130-100000"
        assert "_file_path" in trajectories[0]

    def test_load_examples_no_filter(self, trajectories_dir):
        """Test loading examples without phase filter."""
        loader = TrajectoryDataLoader(trajectories_dir)
        examples = loader.load_examples()

        assert len(examples) == 1
        example = examples[0]

        assert isinstance(example, TrajectoryExample)
        assert example.run_id == "20260130-100000"
        assert example.phase == "deployment"
        assert example.call_id == 1
        assert example.prompt == "Deploy the application"
        assert "deployment script" in example.response.lower()
        assert example.success is True
        assert example.iterations == 1
        assert len(example.tool_calls) == 1

    def test_load_examples_with_phase_filter(self, trajectories_dir):
        """Test loading examples with phase filter."""
        loader = TrajectoryDataLoader(trajectories_dir)

        # Filter by deployment (should match)
        deploy_examples = loader.load_examples(phase_filter="deployment")
        assert len(deploy_examples) == 1

        # Filter by monitoring (should not match)
        monitor_examples = loader.load_examples(phase_filter="monitoring")
        assert len(monitor_examples) == 0

    def test_load_examples_success_only(self, trajectories_dir):
        """Test loading only successful examples."""
        loader = TrajectoryDataLoader(trajectories_dir)
        examples = loader.load_examples(success_only=True)

        assert len(examples) == 1
        assert all(ex.success for ex in examples)

    def test_extract_prompt(self, sample_trajectory):
        """Test prompt extraction."""
        loader = TrajectoryDataLoader(Path())
        messages = sample_trajectory["deployment"][0]["messages"]
        prompt = loader._extract_prompt(messages)

        assert prompt == "Deploy the application"

    def test_extract_response(self, sample_trajectory):
        """Test response extraction."""
        loader = TrajectoryDataLoader(Path())
        messages = sample_trajectory["deployment"][0]["messages"]
        response = loader._extract_response(messages)

        assert "Running deployment script" in response

    def test_extract_tool_calls(self, sample_trajectory):
        """Test tool call extraction."""
        loader = TrajectoryDataLoader(Path())
        messages = sample_trajectory["deployment"][0]["messages"]
        tool_calls = loader._extract_tool_calls(messages)

        assert len(tool_calls) == 1
        assert tool_calls[0]["tool"] == "bash"
        assert tool_calls[0]["exit_code"] == 0
        assert "Deployment successful" in tool_calls[0]["stdout"]

    def test_determine_success_from_exit_code(self, sample_trajectory):
        """Test success determination from exit code."""
        loader = TrajectoryDataLoader(Path())
        messages = sample_trajectory["deployment"][0]["messages"]

        # Success case (exit_code=0)
        success = loader._determine_success(messages, "deployment", True)
        assert success is True

        # Failure case (exit_code=1)
        messages[-1]["exit_code"] = 1
        success = loader._determine_success(messages, "deployment", True)
        assert success is False

    def test_calculate_duration(self, sample_trajectory):
        """Test duration calculation."""
        loader = TrajectoryDataLoader(Path())
        messages = sample_trajectory["deployment"][0]["messages"]
        duration = loader._calculate_duration(messages)

        # 1.5 + 290.0 = 291.5
        assert duration == 291.5

    def test_load_examples_failed_trajectory(self, tmp_path):
        """Test loading from failed trajectory."""
        traj_dir = tmp_path / "trajectories"
        traj_dir.mkdir()

        failed_trajectory = {
            "metadata": {
                "status": "failed",  # Failed status
                "run_id": "20260130-110000",
            },
            "calls": [{"call_id": 1, "phase": "deployment"}],
            "deployment": [
                {
                    "call_id": 1,
                    "messages": [
                        {"role": "user", "content": "Deploy"},
                        {"role": "tool_call", "tool": "bash", "exit_code": 1},
                    ],
                }
            ],
            "monitoring": [],
            "script_generation": [],
        }

        traj_file = traj_dir / "trajectory_20260130-110000.json"
        with open(traj_file, "w") as f:
            json.dump(failed_trajectory, f)

        loader = TrajectoryDataLoader(traj_dir)

        # Without success_only filter
        all_examples = loader.load_examples()
        assert len(all_examples) == 1
        assert not all_examples[0].success

        # With success_only filter
        success_examples = loader.load_examples(success_only=True)
        assert len(success_examples) == 0
