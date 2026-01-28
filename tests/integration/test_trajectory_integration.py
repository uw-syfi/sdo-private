import json
import pytest
from app_operator.trajectory import (
    init_trajectory,
    finalize_trajectory,
    record_phase_start,
    record_phase_end,
    record_user_message,
    record_assistant_message,
    record_tool_call,
    get_current_call_id,
    get_run_id,
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

    # Check calls list (new sequential call ID tracking)
    assert "calls" in data
    calls = data["calls"]
    assert len(calls) == 2  # Two phases = two calls
    assert calls[0]["call_id"] == 1
    assert calls[0]["phase"] == "script_generation"
    assert calls[1]["call_id"] == 2
    assert calls[1]["phase"] == "deployment"

    # Check Script Generation Phase
    assert "script_generation" in data
    script_gen = data["script_generation"]
    assert len(script_gen) == 1  # One conversation/session in this phase

    conversation = script_gen[0]
    assert "call_id" in conversation
    assert conversation["call_id"] == 1
    assert "messages" in conversation

    messages = conversation["messages"]
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

    deploy_conversation = deployment[0]
    assert "call_id" in deploy_conversation
    assert deploy_conversation["call_id"] == 2
    assert "messages" in deploy_conversation

    deploy_messages = deploy_conversation["messages"]
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

    # Access messages through the new structure
    conversation = data["script_generation"][0]
    messages = conversation["messages"]
    tool_msg = messages[1]  # 0 is system, 1 is tool (no user msg here)
    assert tool_msg["role"] == "tool_call"

    # Check truncation happened (max is 10000 in implementation)
    assert len(tool_msg["stdout"]) < 15000
    assert "truncated" in tool_msg["stdout"]


def test_sequential_call_ids(temp_repo):
    """Test that call IDs are sequential and properly associated with phases."""
    init_trajectory(temp_repo)

    # Verify run_id is available
    run_id = get_run_id()
    assert run_id is not None
    assert len(run_id) > 0

    # Phase 1: Script Generation
    record_phase_start(Phase.SCRIPT_GENERATION)
    call_id_1 = get_current_call_id()
    assert call_id_1 == 1
    record_user_message("Generate scripts")
    record_assistant_message("Done")
    record_phase_end()

    # Phase 2: Deployment (first attempt)
    record_phase_start(Phase.DEPLOYMENT, context={"attempt": 1})
    call_id_2 = get_current_call_id()
    assert call_id_2 == 2
    record_user_message("Deploy app")
    record_assistant_message("Failed")
    record_phase_end()

    # Phase 3: Deployment (second attempt)
    record_phase_start(Phase.DEPLOYMENT, context={"attempt": 2})
    call_id_3 = get_current_call_id()
    assert call_id_3 == 3
    record_user_message("Fix and deploy")
    record_assistant_message("Success")
    record_phase_end()

    # Phase 4: Monitoring
    record_phase_start(Phase.MONITORING, context={"cycle": 1})
    call_id_4 = get_current_call_id()
    assert call_id_4 == 4
    record_user_message("Check health")
    record_assistant_message("Healthy")
    record_phase_end()

    # Finalize and verify
    final_path = finalize_trajectory()
    assert final_path is not None

    with open(final_path) as f:
        data = json.load(f)

    # Verify metadata contains run_id
    assert "run_id" in data["metadata"]
    assert data["metadata"]["run_id"] == run_id

    # Verify calls list has all 4 calls in sequential order
    assert "calls" in data
    calls = data["calls"]
    assert len(calls) == 4

    # Check each call
    assert calls[0]["call_id"] == 1
    assert calls[0]["phase"] == "script_generation"
    assert calls[0]["start_time"] is not None
    assert calls[0]["end_time"] is not None

    assert calls[1]["call_id"] == 2
    assert calls[1]["phase"] == "deployment"
    assert calls[1]["context"]["attempt"] == 1

    assert calls[2]["call_id"] == 3
    assert calls[2]["phase"] == "deployment"
    assert calls[2]["context"]["attempt"] == 2

    assert calls[3]["call_id"] == 4
    assert calls[3]["phase"] == "monitoring"
    assert calls[3]["context"]["cycle"] == 1

    # Verify each phase conversation has the correct call_id
    assert data["script_generation"][0]["call_id"] == 1
    assert data["deployment"][0]["call_id"] == 2
    assert data["deployment"][1]["call_id"] == 3
    assert data["monitoring"][0]["call_id"] == 4

    # Verify messages are properly nested
    for conversation in data["script_generation"]:
        assert "call_id" in conversation
        assert "messages" in conversation
        assert len(conversation["messages"]) > 0
