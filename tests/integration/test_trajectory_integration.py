import json
import pytest
from tools.trajectory import (
    init_trajectory,
    finalize_trajectory,
    record_phase_start,
    record_phase_end,
    record_user_message,
    record_assistant_message,
    record_tool_call,
    Phase,
    TrajectoryRecorder,
)


@pytest.fixture
def temp_repo(tmp_path):
    """Create a temporary repository structure."""
    repo = tmp_path / "test_repo"
    repo.mkdir()
    return repo


@pytest.fixture(autouse=True)
def reset_trajectory():
    """Reset the trajectory recorder before and after each test."""
    TrajectoryRecorder.reset()
    yield
    TrajectoryRecorder.reset()


def test_trajectory_lifecycle_integration(temp_repo):
    """
    Test the full lifecycle of trajectory recording from an external perspective.

    This test verifies that:
    1. The trajectory file is created in the correct location.
    2. The structure of the JSON file matches expectations.
    3. Content (phases, messages, tool calls) is correctly recorded.
    4. Metadata is updated.

    It avoids testing internal state of the TrajectoryRecorder class,
    focusing instead on the persisted artifact (trajectory.json).
    """
    # 1. Initialize
    init_trajectory(temp_repo)

    # Verify file creation (trajectory.json symlink and actual file)
    sds_dir = temp_repo / ".sds"
    traj_link = sds_dir / "trajectory.json"

    assert sds_dir.exists()
    assert traj_link.exists()
    assert traj_link.is_symlink() or traj_link.is_file()  # Windows fallback copys file

    # 2. Simulate Script Generation Phase
    record_phase_start(Phase.SCRIPT_GENERATION)
    record_user_message("Generate scripts for this repo")
    record_tool_call(
        tool="ls", args={"path": "."}, stdout="file1.txt\nfile2.txt", duration=0.1
    )
    record_assistant_message("I see the files.")
    record_phase_end(status="success")

    # 3. Simulate Deployment Phase
    record_phase_start(Phase.DEPLOYMENT, context={"attempt": 1, "max_attempts": 3})
    record_user_message("Deploy the app")
    # Simulate a failed tool call
    record_tool_call(
        tool="bash",
        args={"command": "deploy.sh"},
        stdout="",
        stderr="Error: failed",
        exit_code=1,
        duration=1.5,
    )
    record_assistant_message("Deployment failed, retrying...")
    record_phase_end(status="failed")

    # 4. Finalize
    final_path = finalize_trajectory(status="completed")

    assert final_path is not None
    assert final_path.exists()

    # 5. Load and Verify Content
    with open(final_path) as f:
        data = json.load(f)

    # Check Metadata
    assert "metadata" in data
    assert data["metadata"]["repo_path"] == str(temp_repo)
    assert data["metadata"]["status"] == "completed"
    assert "start_time" in data["metadata"]
    assert "end_time" in data["metadata"]

    # Check Script Generation Phase
    assert "script_generation" in data
    script_gen = data["script_generation"]
    assert len(script_gen) == 1  # One conversation/session in this phase

    messages = script_gen[0]
    # Expect: System, User, Tool, Assistant
    # Note: Phase start adds a system message automatically
    assert len(messages) >= 4
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    assert messages[1]["content"] == "Generate scripts for this repo"

    # Find tool call
    tool_msg = next((m for m in messages if m["role"] == "tool_call"), None)
    assert tool_msg is not None
    assert tool_msg["tool"] == "ls"
    assert tool_msg["stdout"] == "file1.txt\nfile2.txt"

    # Check Deployment Phase
    assert "deployment" in data
    deployment = data["deployment"]
    assert len(deployment) == 1

    deploy_messages = deployment[0]
    # Check context in system prompt
    system_msg = deploy_messages[0]
    assert "role" in system_msg and system_msg["role"] == "system"
    assert "attempt 1 of 3" in system_msg["content"]

    # Check error capture
    err_tool = next(
        (
            m
            for m in deploy_messages
            if m["role"] == "tool_call" and m["tool"] == "bash"
        ),
        None,
    )
    assert err_tool is not None
    assert err_tool["stderr"] == "Error: failed"
    assert err_tool["exit_code"] == 1


def test_trajectory_robustness_large_output(temp_repo):
    """Test robustness against large tool outputs."""
    init_trajectory(temp_repo)
    record_phase_start(Phase.SCRIPT_GENERATION)

    # Create a large output
    large_output = "a" * 15000

    record_tool_call("cat", {"file": "large.txt"}, stdout=large_output)
    record_phase_end()

    final_path = finalize_trajectory()
    assert final_path is not None

    with open(final_path) as f:
        data = json.load(f)

    tool_msg = data["script_generation"][0][
        1
    ]  # 0 is system, 1 is tool (no user msg here)
    assert tool_msg["role"] == "tool_call"

    # Check truncation happened (max is 10000 in implementation)
    assert len(tool_msg["stdout"]) < 15000
    assert "truncated" in tool_msg["stdout"]
