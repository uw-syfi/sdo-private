import asyncio
import os
import sys
from pathlib import Path
from typing import List, Any, Dict, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from lego_agent.engine import LegoAgentEngine
from lego_agent.prompts import get_loader, PromptLoader
from lego_agent.utils import find_repo_root
from app_operator.config import load_config, Config
from app_operator.logger import logger

app = FastAPI()


class WebIO:
    """UserIO implementation for WebSocket-based Web UI."""

    def __init__(self, websocket: WebSocket, input_queue: asyncio.Queue):
        self.websocket = websocket
        self.input_queue = input_queue
        self._thinking_buffer = ""
        self._pending_tasks: set[asyncio.Task] = set()

    def _track_task(self, coro) -> asyncio.Task:
        """Create a tracked asyncio task that is removed from the set when done."""
        task = asyncio.create_task(coro)
        self._pending_tasks.add(task)
        task.add_done_callback(self._pending_tasks.discard)
        return task

    async def cleanup(self) -> None:
        """Await all pending tasks, suppressing exceptions."""
        if self._pending_tasks:
            await asyncio.gather(*self._pending_tasks, return_exceptions=True)

    async def _send_event(self, type: str, data: Dict[str, Any]):
        if self.websocket.client_state == WebSocketState.CONNECTED:
            await self.websocket.send_json({"type": type, **data})

    async def _flush_thinking(self) -> None:
        if self._thinking_buffer:
            await self._send_event("thinking", {"text": self._thinking_buffer})
            self._thinking_buffer = ""

    def read_prompt(self) -> str:
        # Not used in this flow, prompt is passed directly to engine
        return ""

    async def ask_questions(self, questions: List[str]) -> List[str]:
        await self._flush_thinking()
        await self._send_event("question", {"questions": questions})

        # Wait for answer from input queue
        answers_data = await self.input_queue.get()
        return answers_data.get("answers", [])

    async def prompt_int(self, label: str) -> int:
        await self._flush_thinking()
        # reusing question event for simplicity, though UI might need specific handling
        # For now, assuming this isn't heavily used or we can adapt
        await self._send_event("question", {"questions": [f"{label} (integer)"]})

        while True:
            answers_data = await self.input_queue.get()
            ans = answers_data.get("answers", [])[0]
            try:
                return int(ans)
            except ValueError:
                await self._send_event(
                    "log", {"message": "Invalid integer, try again", "level": "error"}
                )

    def info(self, message: str) -> None:
        # We can't await here easily if not async, but UserIO protocol is mixed.
        # engine.py calls io.info() synchronously.
        # We must fire and forget or use run_coroutine_threadsafe if we had a loop ref.
        # However, for simplicity in this architecture, we can use a helper or make it async compliant if possible.
        # The UserIO protocol defines info as synchronous `def info(self, message: str) -> None:`.
        # We will schedule the task on the current loop.
        self._track_task(self._send_info_async(message))

    async def _send_info_async(self, message: str):
        await self._flush_thinking()
        await self._send_event("log", {"message": message, "level": "info"})

    def print_stream(self, text: str) -> None:
        self._track_task(self._send_stream_async(text))

    async def _send_stream_async(self, text: str):
        await self._flush_thinking()
        await self._send_event("log", {"message": text, "level": "info"})

    def render_thinking_chunk(self, text: str) -> None:
        # For thinking, we might want to buffer or send immediately.
        # Sending immediately is fine for websockets.
        # But we need to handle the sync vs async nature.
        # The engine calls this synchronously.
        self._track_task(self._send_thinking_async(text))

    async def _send_thinking_async(self, text: str):
        await self._send_event("thinking", {"text": text})

    def render_tool_start(self, name: str, inputs: str) -> None:
        self._track_task(self._send_tool_start_async(name, inputs))

    async def _send_tool_start_async(self, name: str, inputs: str):
        await self._flush_thinking()
        await self._send_event("tool_start", {"name": name, "input": inputs})

    def render_tool_end(self, name: str, output: str, status: str) -> None:
        self._track_task(self._send_tool_end_async(name, output, status))

    async def _send_tool_end_async(self, name: str, output: str, status: str):
        await self._flush_thinking()
        await self._send_event(
            "tool_end", {"name": name, "output": output, "status": status}
        )

    def render_error(self, message: str) -> None:
        self._track_task(self._send_log_async(message, "error"))

    def render_success(self, message: str) -> None:
        self._track_task(self._send_log_async(message, "success"))

    def render_info(self, message: str) -> None:
        self._track_task(self._send_log_async(message, "info"))

    async def _send_log_async(self, message: str, level: str):
        await self._flush_thinking()
        await self._send_event("log", {"message": message, "level": level})

    def render_graph(self, config: Dict[str, Any]) -> None:
        self._track_task(self._send_graph_async(config))

    async def _send_graph_async(self, config: Dict[str, Any]):
        await self._flush_thinking()
        await self._send_event("graph", {"config": config})


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    input_queue = asyncio.Queue()
    io = WebIO(websocket, input_queue)

    repo_root = find_repo_root()

    try:
        config = load_config(str(repo_root), None)
    except Exception as e:
        await io._send_event(
            "log", {"message": f"Failed to load config: {e}", "level": "error"}
        )
        await websocket.close()
        return

    # Send init event with absolute CWD and config info
    await io._send_event(
        "init",
        {
            "cwd": str(repo_root),
            "model": config.agent.model,
            "thinking_budget": config.agent.thinking_budget,
        },
    )

    prompt_loader = get_loader()
    output_dir = repo_root / "lego_agent_runs"
    work_dir = repo_root  # Default to repo root for execution context

    try:
        current_task: Optional[asyncio.Task] = None

        while True:
            data = await websocket.receive_json()
            event_type = data.get("type")

            if event_type == "start":
                user_prompt = data.get("prompt")
                # Work dir is passed from frontend, defaulting to repo root if not provided (though UI should enforce)
                user_work_dir = data.get("work_dir")

                if not user_prompt:
                    continue

                # Cancel existing task if running
                if current_task and not current_task.done():
                    current_task.cancel()
                    try:
                        await current_task
                    except asyncio.CancelledError:
                        pass

                # Drain input queue to remove stale answers
                while not input_queue.empty():
                    input_queue.get_nowait()

                # Resolve work dir
                exec_work_dir = (
                    Path(user_work_dir).resolve() if user_work_dir else work_dir
                )

                # Run engine in background task so we can keep receiving messages (like answers)
                current_task = asyncio.create_task(
                    run_engine_and_script(
                        io,
                        config,
                        prompt_loader,
                        output_dir,
                        exec_work_dir,
                        repo_root,
                        user_prompt,
                    )
                )

            elif event_type == "stop":
                if current_task and not current_task.done():
                    current_task.cancel()
                    try:
                        await current_task
                    except asyncio.CancelledError:
                        pass
                    await io._send_event(
                        "log", {"message": "Agent stopped by user", "level": "error"}
                    )
                    await io._send_event("execution_result", {"exit_code": -1})

            elif event_type == "list_dirs":
                path_str = data.get("path", "")
                if not path_str:
                    path_str = str(repo_root)

                try:
                    suggestions = []
                    # Determine search directory and prefix
                    path_obj = Path(path_str)

                    if path_str.endswith(os.sep):
                        search_dir = path_obj
                        prefix = ""
                    else:
                        search_dir = path_obj.parent
                        prefix = path_obj.name

                    if search_dir.exists() and search_dir.is_dir():
                        for item in search_dir.iterdir():
                            if item.is_dir():
                                if prefix:
                                    if item.name.startswith(prefix):
                                        suggestions.append(str(item))
                                else:
                                    suggestions.append(str(item))

                    # Sort and limit
                    suggestions = sorted(suggestions)[:20]
                    await io._send_event("dir_options", {"options": suggestions})

                except Exception:
                    # Silently fail for list dirs (e.g. permission error)
                    await io._send_event("dir_options", {"options": []})

            elif event_type == "validate_path":
                path_str = data.get("path", "")
                valid = False
                try:
                    if path_str:
                        p = Path(path_str)
                        valid = p.exists() and p.is_dir()
                except Exception:
                    pass

                await io._send_event(
                    "path_validation", {"path": path_str, "valid": valid}
                )

            elif event_type == "answer":
                # Put answer into queue for the waiting engine
                await input_queue.put(data)

    except WebSocketDisconnect:
        logger.info("Client disconnected")
    except Exception as e:
        logger.error(f"WebSocket error: {e}", exc_info=True)
    finally:
        await io.cleanup()


async def run_engine_and_script(
    io: WebIO,
    config: Config,
    prompt_loader: PromptLoader,
    output_dir: Path,
    work_dir: Path,
    repo_root: Path,
    user_prompt: str,
) -> None:
    try:
        # 1. Run Engine
        engine = LegoAgentEngine(
            config=config,
            prompt_loader=prompt_loader,
            io=io,
            loop_bound=10,  # Default
            max_clarifications=5,  # Default
            agent_timeout=config.operator.agent_timeout,
            output_dir=output_dir,
            work_dir=work_dir,
        )

        result = await engine.run_async(user_prompt)
        await io._send_event(
            "log",
            {
                "message": f"Script generated at: {result.script_path}",
                "level": "success",
            },
        )

        # 2. Execute Script
        await io._send_event(
            "log", {"message": "Executing generated script...", "level": "info"}
        )

        env = os.environ.copy()
        env["PYTHONPATH"] = f"{str(repo_root)}:{env.get('PYTHONPATH', '')}"

        # Ensure work_dir exists
        if not work_dir.exists():
            work_dir.mkdir(parents=True, exist_ok=True)

        process = await asyncio.create_subprocess_exec(
            sys.executable,
            str(result.script_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=work_dir,
            env=env,
        )

        async def read_stream(stream, name):
            while True:
                line = await stream.readline()
                if not line:
                    break
                decoded = line.decode().rstrip()
                await io._send_event(
                    "script_execution", {"stream": name, "data": decoded}
                )

        await asyncio.gather(
            read_stream(process.stdout, "stdout"), read_stream(process.stderr, "stderr")
        )

        return_code = await process.wait()

        if return_code == 0:
            await io._send_event(
                "log",
                {
                    "message": f"Execution finished successfully (Exit Code: {return_code})",
                    "level": "success",
                },
            )
        else:
            await io._send_event(
                "log",
                {
                    "message": f"Execution failed (Exit Code: {return_code})",
                    "level": "error",
                },
            )

        await io._send_event("execution_result", {"exit_code": return_code})

    except Exception as e:
        logger.error(f"Execution failed: {e}", exc_info=True)
        await io._send_event("log", {"message": f"Error: {e}", "level": "error"})


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
