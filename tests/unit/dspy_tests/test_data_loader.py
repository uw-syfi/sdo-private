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

    def test_load_examples_with_prompt_kwargs(self, tmp_path):
        """prompt_kwargs recorded in trajectory are extracted into TrajectoryExample."""
        traj_dir = tmp_path / "trajectories"
        traj_dir.mkdir()

        trajectory = {
            "metadata": {"status": "completed", "run_id": "20260130-120000"},
            "calls": [{"call_id": 1, "phase": "deployment"}],
            "deployment": [
                {
                    "call_id": 1,
                    "prompt_kwargs": {
                        "repo_path": "/test/repo",
                        "error_context": "exit code 1",
                        "attempt": 2,
                    },
                    "messages": [
                        {"role": "user", "content": "Fix deployment"},
                        {"role": "assistant", "content": "Fixed"},
                        {"role": "tool_call", "tool": "bash", "exit_code": 0},
                    ],
                }
            ],
            "monitoring": [],
            "script_generation": [],
        }

        traj_file = traj_dir / "trajectory_20260130-120000.json"
        with open(traj_file, "w") as f:
            json.dump(trajectory, f)

        loader = TrajectoryDataLoader(traj_dir)
        examples = loader.load_examples(phase_filter="deployment")

        assert len(examples) == 1
        assert examples[0].prompt_kwargs == {
            "repo_path": "/test/repo",
            "error_context": "exit code 1",
            "attempt": 2,
        }

    def test_load_examples_with_rendered_prompt(self, tmp_path):
        """rendered_prompt recorded in trajectory is extracted into TrajectoryExample."""
        traj_dir = tmp_path / "trajectories"
        traj_dir.mkdir()

        trajectory = {
            "metadata": {"status": "completed", "run_id": "20260130-140000"},
            "calls": [{"call_id": 1, "phase": "deployment"}],
            "deployment": [
                {
                    "call_id": 1,
                    "prompt_kwargs": {"repo_path": "/test/repo"},
                    "rendered_prompt": "You are a DevOps agent. Fix the deployment.",
                    "messages": [
                        {"role": "user", "content": "Fix deployment"},
                        {"role": "assistant", "content": "Fixed"},
                        {"role": "tool_call", "tool": "bash", "exit_code": 0},
                    ],
                }
            ],
            "monitoring": [],
            "script_generation": [],
        }

        traj_file = traj_dir / "trajectory_20260130-140000.json"
        with open(traj_file, "w") as f:
            json.dump(trajectory, f)

        loader = TrajectoryDataLoader(traj_dir)
        examples = loader.load_examples(phase_filter="deployment")

        assert len(examples) == 1
        assert examples[0].rendered_prompt == "You are a DevOps agent. Fix the deployment."

    def test_load_examples_without_rendered_prompt(self, tmp_path):
        """Trajectories without rendered_prompt yield rendered_prompt=None."""
        traj_dir = tmp_path / "trajectories"
        traj_dir.mkdir()

        trajectory = {
            "metadata": {"status": "completed", "run_id": "20260130-150000"},
            "calls": [{"call_id": 1, "phase": "deployment"}],
            "deployment": [
                {
                    "call_id": 1,
                    "messages": [
                        {"role": "user", "content": "Deploy"},
                        {"role": "assistant", "content": "Done"},
                        {"role": "tool_call", "tool": "bash", "exit_code": 0},
                    ],
                }
            ],
            "monitoring": [],
            "script_generation": [],
        }

        traj_file = traj_dir / "trajectory_20260130-150000.json"
        with open(traj_file, "w") as f:
            json.dump(trajectory, f)

        loader = TrajectoryDataLoader(traj_dir)
        examples = loader.load_examples(phase_filter="deployment")

        assert len(examples) == 1
        assert examples[0].rendered_prompt is None

    def test_load_examples_without_prompt_kwargs(self, tmp_path):
        """Trajectories without prompt_kwargs yield prompt_kwargs=None."""
        traj_dir = tmp_path / "trajectories"
        traj_dir.mkdir()

        trajectory = {
            "metadata": {"status": "completed", "run_id": "20260130-130000"},
            "calls": [{"call_id": 1, "phase": "deployment"}],
            "deployment": [
                {
                    "call_id": 1,
                    "messages": [
                        {"role": "user", "content": "Deploy"},
                        {"role": "assistant", "content": "Done"},
                        {"role": "tool_call", "tool": "bash", "exit_code": 0},
                    ],
                }
            ],
            "monitoring": [],
            "script_generation": [],
        }

        traj_file = traj_dir / "trajectory_20260130-130000.json"
        with open(traj_file, "w") as f:
            json.dump(trajectory, f)

        loader = TrajectoryDataLoader(traj_dir)
        examples = loader.load_examples(phase_filter="deployment")

        assert len(examples) == 1
        assert examples[0].prompt_kwargs is None

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

    def test_determine_success_deployment_exit_minus_one_with_success_stdout(self):
        """exit_code=-1 with 'success' in stdout should count as successful."""
        loader = TrajectoryDataLoader(Path())
        messages = [
            {"role": "user", "content": "Deploy"},
            {"role": "tool_call", "tool": "bash", "exit_code": -1,
             "stdout": "Services started\nSUCCESS: System appears healthy.\n"},
        ]
        assert loader._determine_success(messages, "deployment", False) is True

    def test_determine_success_deployment_exit_minus_one_no_success(self):
        """exit_code=-1 without success keywords in stdout is a failure."""
        loader = TrajectoryDataLoader(Path())
        messages = [
            {"role": "user", "content": "Deploy"},
            {"role": "tool_call", "tool": "bash", "exit_code": -1,
             "stdout": "Container crashed during startup\n"},
        ]
        assert loader._determine_success(messages, "deployment", True) is False

    def test_determine_success_monitoring_exec_summary_healthy(self):
        """exec_summary stating system is healthy overrides error keywords in body."""
        loader = TrajectoryDataLoader(Path())
        messages = [
            {"role": "assistant", "content":
             "<exec_summary>The system is fully operational with all checks passing.</exec_summary>\n"
             "### Details\n"
             "Log Noise/Errors: frontend reports resolver errors (non-critical)."},
        ]
        # Would be False with naive keyword matching; True with exec_summary
        assert loader._determine_success(messages, "monitoring", False) is True

    def test_determine_success_monitoring_exec_summary_unhealthy(self):
        """exec_summary indicating critical failure marks monitoring as failed."""
        loader = TrajectoryDataLoader(Path())
        messages = [
            {"role": "assistant", "content":
             "<exec_summary>Critical failure: system down, database unreachable.</exec_summary>\n"
             "All endpoints returning 503."},
        ]
        assert loader._determine_success(messages, "monitoring", True) is False

    def test_determine_success_monitoring_no_exec_summary_falls_back(self):
        """Without exec_summary, monitoring falls back to overall trajectory status."""
        loader = TrajectoryDataLoader(Path())
        messages = [
            {"role": "assistant", "content": "Checked health endpoints."},
        ]
        assert loader._determine_success(messages, "monitoring", True) is True
        assert loader._determine_success(messages, "monitoring", False) is False

    def test_extract_token_usage_basic(self):
        """Token usage is estimated from message content lengths."""
        loader = TrajectoryDataLoader(Path())
        messages = [
            {"role": "system", "content": "A" * 400},   # 100 input tokens
            {"role": "user", "content": "B" * 200},      # 50 input tokens
            {"role": "assistant", "content": "C" * 120},  # 30 output tokens
            {"role": "tool_call", "tool": "bash",
             "content": "",
             # ~10 input tokens (str repr adds a few chars)
             "args": {"cmd": "D" * 40},
             "stdout": "E" * 80},                        # 20 input tokens
        ]
        usage = loader._extract_token_usage(messages)
        assert usage is not None
        assert usage["estimated"] is True
        # input: (400 + 200 + len(str({"cmd":"D"*40})) + 80) / 4
        # output: 120 / 4 = 30
        assert usage["output"] == 30
        assert usage["input"] > 150  # at least system + user + stdout

    def test_extract_token_usage_empty_messages(self):
        """Empty messages return None."""
        loader = TrajectoryDataLoader(Path())
        assert loader._extract_token_usage([]) is None

    def test_extract_token_usage_no_content(self):
        """Messages with no content at all return None."""
        loader = TrajectoryDataLoader(Path())
        messages = [
            {"role": "tool_call", "tool": "bash"},
        ]
        assert loader._extract_token_usage(messages) is None

    def test_iterations_reflects_phase_attempt_count(self, tmp_path):
        """iterations should reflect the 1-based index of each conversation in the phase."""
        traj_dir = tmp_path / "trajectories"
        traj_dir.mkdir()

        trajectory = {
            "metadata": {"status": "failed", "run_id": "20260130-160000"},
            "calls": [
                {"call_id": 1, "phase": "deployment"},
                {"call_id": 2, "phase": "deployment"},
                {"call_id": 3, "phase": "deployment"},
            ],
            "deployment": [
                {
                    "call_id": 1,
                    "messages": [
                        {"role": "user", "content": "Deploy"},
                        {"role": "assistant", "content": "Trying..."},
                        {"role": "assistant", "content": "Still trying..."},
                        {"role": "tool_call", "tool": "bash", "exit_code": 1},
                    ],
                },
                {
                    "call_id": 2,
                    "messages": [
                        {"role": "user", "content": "Fix and retry"},
                        {"role": "assistant", "content": "Retrying..."},
                        {"role": "tool_call", "tool": "bash", "exit_code": 1},
                    ],
                },
                {
                    "call_id": 3,
                    "messages": [
                        {"role": "user", "content": "Fix and retry again"},
                        {"role": "assistant", "content": "Retrying again..."},
                        {"role": "tool_call", "tool": "bash", "exit_code": 0},
                    ],
                },
            ],
            "monitoring": [],
            "script_generation": [],
        }

        traj_file = traj_dir / "trajectory_20260130-160000.json"
        with open(traj_file, "w") as f:
            json.dump(trajectory, f)

        loader = TrajectoryDataLoader(traj_dir)
        examples = loader.load_examples(phase_filter="deployment")

        assert len(examples) == 3
        # Each example gets a 1-based iteration index within the phase
        assert [ex.iterations for ex in examples] == [1, 2, 3]
