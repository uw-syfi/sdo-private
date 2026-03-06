"""Tests for data loader integration with fault injection metadata."""

import json

import pytest

from app_operator.dspy_integration._data_loader import TrajectoryDataLoader


@pytest.fixture
def trajectories_with_faults(tmp_path):
    """Create trajectory files with fault injection metadata."""
    traj_dir = tmp_path / "trajectories"
    traj_dir.mkdir()

    # Trajectory with fault injection
    traj_with_faults = {
        "metadata": {
            "repo_path": str(tmp_path),
            "start_time": "2025-01-01 00:00:00",
            "end_time": "2025-01-01 01:00:00",
            "agent_name": "TestAgent",
            "status": "completed",
            "run_id": "20250101-000000",
            "fault_injection": {
                "enabled": True,
                "num_faults_injected": 2,
                "fault_ids": ["MISC-001", "SEC-001"],
                "categories": ["misconfiguration", "security"],
                "severities": ["low", "medium"],
                "faults": [
                    {"fault_id": "MISC-001", "target_service": "frontend"},
                    {"fault_id": "SEC-001", "target_service": "geo"},
                ],
            },
        },
        "deployment": [
            {
                "call_id": 1,
                "messages": [
                    {"role": "system", "content": "Deploy the app"},
                    {"role": "user", "content": "Fix deployment"},
                    {"role": "assistant", "content": "Fixed the port issue"},
                    {"role": "tool_call", "tool": "bash", "args": {"cmd": "deploy"}, "exit_code": 0},
                ],
            }
        ],
        "monitoring": [],
        "script_generation": [],
    }
    with open(traj_dir / "trajectory_20250101-000000.json", "w") as f:
        json.dump(traj_with_faults, f)

    # Trajectory without fault injection
    traj_clean = {
        "metadata": {
            "repo_path": str(tmp_path),
            "start_time": "2025-01-02 00:00:00",
            "end_time": "2025-01-02 01:00:00",
            "agent_name": "TestAgent",
            "status": "completed",
            "run_id": "20250102-000000",
        },
        "deployment": [
            {
                "call_id": 1,
                "messages": [
                    {"role": "system", "content": "Deploy the app"},
                    {"role": "user", "content": "Deploy clean"},
                    {"role": "assistant", "content": "Deployed successfully"},
                    {"role": "tool_call", "tool": "bash", "args": {"cmd": "deploy"}, "exit_code": 0},
                ],
            }
        ],
        "monitoring": [],
        "script_generation": [],
    }
    with open(traj_dir / "trajectory_20250102-000000.json", "w") as f:
        json.dump(traj_clean, f)

    return traj_dir


class TestDataLoaderFaultIntegration:
    def test_load_examples_with_fault_metadata(self, trajectories_with_faults):
        loader = TrajectoryDataLoader(trajectories_with_faults)
        examples = loader.load_examples(phase_filter="deployment")
        assert len(examples) == 2

        # Check fault-injected example
        faulted = [e for e in examples if e.fault_injected]
        assert len(faulted) == 1
        assert faulted[0].fault_ids == ["MISC-001", "SEC-001"]
        assert "misconfiguration" in faulted[0].fault_categories
        assert "security" in faulted[0].fault_categories
        assert "low" in faulted[0].fault_severities

        # Check clean example
        clean = [e for e in examples if not e.fault_injected]
        assert len(clean) == 1
        assert clean[0].fault_ids == []
        assert clean[0].fault_categories == []

    def test_load_examples_without_faults(self, tmp_path):
        traj_dir = tmp_path / "trajectories"
        traj_dir.mkdir()
        traj = {
            "metadata": {
                "repo_path": str(tmp_path),
                "status": "completed",
                "run_id": "test",
            },
            "deployment": [
                {
                    "call_id": 1,
                    "messages": [
                        {"role": "user", "content": "Deploy"},
                        {"role": "assistant", "content": "OK"},
                        {"role": "tool_call", "tool": "bash", "args": {}, "exit_code": 0},
                    ],
                }
            ],
            "monitoring": [],
            "script_generation": [],
        }
        with open(traj_dir / "trajectory_test.json", "w") as f:
            json.dump(traj, f)

        loader = TrajectoryDataLoader(traj_dir)
        examples = loader.load_examples(phase_filter="deployment")
        assert len(examples) == 1
        assert examples[0].fault_injected is False
        assert examples[0].fault_ids == []
