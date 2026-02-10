import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketState

from lego_agent.server import WebIO, app


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


@pytest.mark.asyncio
async def test_webio_send_event(mock_websocket, input_queue):
    io = WebIO(mock_websocket, input_queue)
    await io._send_event("test_type", {"key": "value"})

    assert len(mock_websocket.sent_messages) == 1
    assert mock_websocket.sent_messages[0] == {"type": "test_type", "key": "value"}


@pytest.mark.asyncio
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


@pytest.mark.asyncio
async def test_webio_ask_questions(mock_websocket, input_queue):
    io = WebIO(mock_websocket, input_queue)

    # Simulate user answering
    await input_queue.put({"answers": ["Ans1"]})

    answers = await io.ask_questions(["Q1"])

    assert answers == ["Ans1"]
    assert {"type": "question", "questions": ["Q1"]} in mock_websocket.sent_messages


@pytest.mark.asyncio
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


@pytest.mark.asyncio
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
