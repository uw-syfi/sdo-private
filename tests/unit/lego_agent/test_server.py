import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect, WebSocketState

from lego_agent.server import WebIO, app, websocket_endpoint


class MockWebSocket:
    def __init__(self):
        self.client_state = WebSocketState.CONNECTED
        self.sent_messages = []
        self.accept = AsyncMock()
        # self.send_json = AsyncMock()  <-- This was overriding the method below
        self.receive_json = AsyncMock()
        self.close = AsyncMock()

    async def send_json(self, data):
        self.sent_messages.append(data)


@pytest.fixture
def mock_websocket():
    return MockWebSocket()


@pytest.fixture
def input_queue():
    return asyncio.Queue()


@pytest.mark.anyio
async def test_webio_send_event(mock_websocket, input_queue):
    io = WebIO(mock_websocket, input_queue)
    await io._send_event("test_type", {"key": "value"})

    assert len(mock_websocket.sent_messages) == 1
    assert mock_websocket.sent_messages[0] == {"type": "test_type", "key": "value"}


@pytest.mark.anyio
async def test_webio_flush_thinking(mock_websocket, input_queue):
    io = WebIO(mock_websocket, input_queue)
    io._thinking_buffer = "thinking..."

    await io._flush_thinking()

    assert len(mock_websocket.sent_messages) == 1
    assert mock_websocket.sent_messages[0] == {
        "type": "thinking",
        "text": "thinking...",
    }
    assert io._thinking_buffer == ""


@pytest.mark.anyio
async def test_webio_ask_questions(mock_websocket, input_queue):
    io = WebIO(mock_websocket, input_queue)

    # Simulate user answering
    await input_queue.put({"answers": ["Ans1"]})

    answers = await io.ask_questions(["Q1"])

    assert answers == ["Ans1"]
    assert {"type": "question", "questions": ["Q1"]} in mock_websocket.sent_messages


@pytest.mark.anyio
async def test_webio_prompt_int(mock_websocket, input_queue):
    io = WebIO(mock_websocket, input_queue)

    await input_queue.put({"answers": ["42"]})

    val = await io.prompt_int("Enter number")

    assert val == 42
    assert {
        "type": "question",
        "questions": ["Enter number (integer)"],
    } in mock_websocket.sent_messages


def test_websocket_connection():
    client = TestClient(app)
    # Note: TestClient with WebSocket requires httpx or similar, but FastAPI TestClient wraps Starlette's.
    # Starlette's TestClient supports websocket_connect.

    with client.websocket_connect("/ws"):
        # We can send data
        # But our server logic relies on `load_config` which might fail if not mocked or in wrong dir.
        pass


@pytest.mark.anyio
async def test_server_logic(tmp_path):
    # We can mock the dependencies of websocket_endpoint
    with (
        patch("lego_agent.server.load_config"),
        patch("lego_agent.server.get_loader"),
        patch("lego_agent.server.LegoAgentEngine") as mock_engine_cls,
    ):
        mock_engine = AsyncMock()
        mock_engine.run_async.return_value = MagicMock(script_path="/tmp/script.py")
        mock_engine_cls.return_value = mock_engine

        # We can't easily test the websocket_endpoint directly without a client or careful mocking of the websocket object lifecycle.
        # But we can test the `run_engine_and_script` function if we extract it or import it.
        from lego_agent.server import run_engine_and_script

        io = AsyncMock()
        config = MagicMock()
        config.operator.agent_timeout = 300

        # Mock subprocess
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            process = AsyncMock()
            process.wait.return_value = 0
            process.stdout.readline.side_effect = [b"line1\n", b""]
            process.stderr.readline.side_effect = [b""]
            mock_exec.return_value = process

            await run_engine_and_script(
                io,
                config,
                MagicMock(),
                tmp_path / "out",
                tmp_path / "work",
                tmp_path / "repo",
                "prompt",
            )

            # Verify engine run
            mock_engine.run_async.assert_called_once_with("prompt")

            # Verify events
            # Log success of generation
            io._send_event.assert_any_call(
                "log",
                {"message": "Script generated at: /tmp/script.py", "level": "success"},
            )
            # Log execution start
            io._send_event.assert_any_call(
                "log", {"message": "Executing generated script...", "level": "info"}
            )
            # Execution result
            io._send_event.assert_any_call("execution_result", {"exit_code": 0})


@pytest.mark.anyio
async def test_path_traversal_rejected(tmp_path):
    """Path traversal in work_dir is rejected with an error event."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    mock_config = MagicMock()
    mock_config.agent.model = "test-model"
    mock_config.agent.thinking_budget = 100

    ws = MockWebSocket()

    # Simulate: first call returns a "start" event with traversal path,
    # second call raises disconnect to end the loop.
    ws.receive_json = AsyncMock(
        side_effect=[
            {
                "type": "start",
                "prompt": "do something",
                "work_dir": str(repo_root / ".." / ".." / "etc"),
            },
            WebSocketDisconnect(),
        ]
    )

    with (
        patch("lego_agent.server.find_repo_root", return_value=repo_root),
        patch("lego_agent.server.load_config", return_value=mock_config),
        patch("lego_agent.server.get_loader"),
        patch("lego_agent.server.LegoAgentEngine") as mock_engine_cls,
    ):
        mock_engine = AsyncMock()
        mock_engine_cls.return_value = mock_engine

        await websocket_endpoint(ws)

        # Should have sent an error about invalid work_dir
        error_msgs = [
            m
            for m in ws.sent_messages
            if m.get("type") == "log" and m.get("level") == "error"
            and "Invalid work_dir" in m.get("message", "")
        ]
        assert len(error_msgs) == 1
        assert "must be within the repository root" in error_msgs[0]["message"]

        # Engine should NOT have been started
        mock_engine_cls.assert_not_called()


@pytest.mark.anyio
async def test_valid_work_dir_accepted(tmp_path):
    """A work_dir within repo root is accepted and the engine is started."""
    repo_root = tmp_path / "repo"
    subdir = repo_root / "subproject"
    subdir.mkdir(parents=True)

    mock_config = MagicMock()
    mock_config.agent.model = "test-model"
    mock_config.agent.thinking_budget = 100
    mock_config.operator.agent_timeout = 300

    ws = MockWebSocket()

    ws.receive_json = AsyncMock(
        side_effect=[
            {
                "type": "start",
                "prompt": "do something",
                "work_dir": str(subdir),
            },
            WebSocketDisconnect(),
        ]
    )

    with (
        patch("lego_agent.server.find_repo_root", return_value=repo_root),
        patch("lego_agent.server.load_config", return_value=mock_config),
        patch("lego_agent.server.get_loader"),
        patch(
            "lego_agent.server.run_engine_and_script", new_callable=AsyncMock
        ),
    ):
        await websocket_endpoint(ws)

        # Engine should have been started (task created)
        # No error about invalid work_dir
        error_msgs = [
            m
            for m in ws.sent_messages
            if m.get("type") == "log" and "Invalid work_dir" in m.get("message", "")
        ]
        assert len(error_msgs) == 0


@pytest.mark.anyio
async def test_non_dict_message_returns_error(tmp_path):
    """A WebSocket message that is not a JSON object gets an error response."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    mock_config = MagicMock()
    mock_config.agent.model = "test-model"
    mock_config.agent.thinking_budget = 100

    ws = MockWebSocket()
    ws.receive_json = AsyncMock(
        side_effect=[
            "not a dict",
            WebSocketDisconnect(),
        ]
    )

    with (
        patch("lego_agent.server.find_repo_root", return_value=repo_root),
        patch("lego_agent.server.load_config", return_value=mock_config),
        patch("lego_agent.server.get_loader"),
    ):
        await websocket_endpoint(ws)

    error_msgs = [
        m for m in ws.sent_messages
        if m.get("type") == "error"
    ]
    assert len(error_msgs) == 1
    assert "expected a JSON object" in error_msgs[0]["message"]


@pytest.mark.anyio
async def test_missing_type_field_returns_error(tmp_path):
    """A WebSocket message without a 'type' field gets an error response."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    mock_config = MagicMock()
    mock_config.agent.model = "test-model"
    mock_config.agent.thinking_budget = 100

    ws = MockWebSocket()
    ws.receive_json = AsyncMock(
        side_effect=[
            {"no_type_key": "value"},
            WebSocketDisconnect(),
        ]
    )

    with (
        patch("lego_agent.server.find_repo_root", return_value=repo_root),
        patch("lego_agent.server.load_config", return_value=mock_config),
        patch("lego_agent.server.get_loader"),
    ):
        await websocket_endpoint(ws)

    error_msgs = [
        m for m in ws.sent_messages
        if m.get("type") == "error"
    ]
    assert len(error_msgs) == 1
    assert "'type' field must be a string" in error_msgs[0]["message"]


@pytest.mark.anyio
async def test_non_string_type_field_returns_error(tmp_path):
    """A WebSocket message with a non-string 'type' field gets an error response."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    mock_config = MagicMock()
    mock_config.agent.model = "test-model"
    mock_config.agent.thinking_budget = 100

    ws = MockWebSocket()
    ws.receive_json = AsyncMock(
        side_effect=[
            {"type": 123},
            WebSocketDisconnect(),
        ]
    )

    with (
        patch("lego_agent.server.find_repo_root", return_value=repo_root),
        patch("lego_agent.server.load_config", return_value=mock_config),
        patch("lego_agent.server.get_loader"),
    ):
        await websocket_endpoint(ws)

    error_msgs = [
        m for m in ws.sent_messages
        if m.get("type") == "error"
    ]
    assert len(error_msgs) == 1
    assert "'type' field must be a string" in error_msgs[0]["message"]


@pytest.mark.anyio
async def test_track_task_adds_and_removes(mock_websocket, input_queue):
    """_track_task adds a task to _pending_tasks and removes it on completion."""
    io = WebIO(mock_websocket, input_queue)

    assert len(io._pending_tasks) == 0

    io.info("hello")
    assert len(io._pending_tasks) == 1

    # Let the event loop run so the task completes and done callback fires
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert len(io._pending_tasks) == 0
    assert any(
        m.get("type") == "log" and m.get("message") == "hello"
        for m in mock_websocket.sent_messages
    )


@pytest.mark.anyio
async def test_track_task_multiple_methods(mock_websocket, input_queue):
    """Multiple fire-and-forget methods are all tracked."""
    io = WebIO(mock_websocket, input_queue)

    io.info("msg1")
    io.render_error("err1")
    io.render_success("ok1")
    io.print_stream("stream1")
    io.render_thinking_chunk("think1")
    io.render_tool_start("tool", "input")
    io.render_tool_end("tool", "output", "ok")
    io.render_info("info1")
    io.render_graph({"key": "val"})

    assert len(io._pending_tasks) == 9

    await io.cleanup()

    assert len(io._pending_tasks) == 0
    assert len(mock_websocket.sent_messages) == 9


@pytest.mark.anyio
async def test_cleanup_suppresses_exceptions(mock_websocket, input_queue):
    """cleanup() does not raise even if a tracked task raises."""
    io = WebIO(mock_websocket, input_queue)

    async def failing_coro():
        raise RuntimeError("boom")

    io._track_task(failing_coro())

    # Should not raise
    await io.cleanup()
    assert len(io._pending_tasks) == 0


@pytest.mark.anyio
async def test_cleanup_on_empty(mock_websocket, input_queue):
    """cleanup() is a no-op when there are no pending tasks."""
    io = WebIO(mock_websocket, input_queue)
    await io.cleanup()
    assert len(io._pending_tasks) == 0
