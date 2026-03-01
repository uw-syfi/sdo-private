# LegoAgent: Autonomous Script Generation

The **LegoAgent** module is an autonomous script generation system that uses AI agents to create orchestrated Python scripts for complex multi-agent workflows. It features an interactive TUI, clarification loops, and supports advanced orchestration patterns.

## Key Features

- **Web UI Mode**: Modern Next.js interface with real-time visualization of agent thinking and tool usage.
- **Interactive TUI Mode**: Textual-based rich terminal interface (legacy).
- **Clarification Loop**: Iteratively refines requirements through AI-powered questions before generating scripts.
- **Orchestration Patterns**: Built-in support for `fan_out`, `summarize`, and `judge_loop` patterns.
- **Automatic Repo Detection**: Finds project root by searching upward for `.git` or `sds.toml`.
- **Script Validation**: Validates syntax and required components before execution.
- **Environment Setup**: Configures `PYTHONPATH` and work directory automatically.

## Quick Start

**Web UI Mode (Recommended):**
```bash
./scripts/start_lego_ui.sh
```
This starts the backend on port 8000 and the frontend on port 3000. Open `http://localhost:3000` in your browser.

**Default TUI Mode:**
```bash
uv run -m lego_agent
```

**With Initial Prompt:**
```bash
uv run -m lego_agent --prompt "Scrape hacker news and summarize top 3 AI stories"
```

**CLI Mode (No TUI):**
```bash
uv run -m lego_agent --no-tui --prompt "Your task"
```

## CLI Options

- `--prompt`: Initial user prompt (interactive if omitted in TUI mode).
- `--loop-bound`: Maximum iterations for loops (default: 10).
- `--max-clarifications`: Maximum clarification rounds (default: 5).
- `--config`: Path to `sds.toml` (optional, auto-detects repo root).
- `--model`: Override agent model from configuration.
- `--output-dir`: Output directory (default: `lego_agent_runs`).
- `--work-dir`: Execution directory (default: current directory).
- `--no-run`: Generate script but do not execute it.
- `--no-tui`: Use CLI mode instead of TUI.

## Architecture

### Core Components

- **LegoAgentEngine (`engine.py`)**:
  - Manages the clarification loop (up to `max_clarifications` rounds).
  - Integrates with LangGraph for agent execution.
  - Streams thinking chunks, tool calls, and results.
  - Validates generated Python scripts before execution.
  - Handles response parsing and error repair.

- **Web UI Server (`server.py`)**:
  - FastAPI application serving a WebSocket endpoint (`/ws`).
  - **WebIO**: Adapts the `UserIO` protocol to WebSocket events.
  - Streams "thinking", "tool_start", "tool_end" events to the frontend.
  - Handles "start" and "answer" events from the frontend.

- **Web Frontend (`ui/`)**:
  - Next.js application using Tailwind CSS and React.
  - **TerminalLog**: Renders the agent's stream in a terminal-like view.
  - **InputArea**: Dynamic form for prompts and clarification answers.
  - Connects to the backend via WebSocket.

- **I/O Abstraction (`io.py`)**:
  - **UserIO Protocol**: Duck-typed interface for user interaction.
  - **WebIO**: WebSocket-based implementation for the Web UI.
  - **ConsoleIO**: ANSI-colored console output for CLI mode.
  - **TextualIO**: Rich Textual widgets for TUI mode.

- **LegoAgentTUI (`tui.py`)**:
  - Textual App implementation with `RichLog` widget.
  - Interactive input field for prompts and answers.
  - Work directory display in header.
  - Async script execution with live stdout/stderr streaming.
  - Uses `flexoki` theme for consistent styling.

- **LangGraphAgent (`runtime.py`)**:
  - Wraps LangGraph React agent for orchestration.
  - Implements streaming event handlers for thinking and tool use.
  - Provides `generate()` sync and `_generate_async()` async methods.
  - Supports parallelization through `fan_out()` for multi-task execution.

### Orchestration Patterns

1. **fan_out()**: Execute multiple independent prompts in parallel.
2. **summarize()**: Aggregate multiple responses into one.
3. **judge_loop()**: Iterative refinement with evaluation.
4. **Combined patterns**: Complex workflows combining multiple patterns.

### Storage & Models

- **LegoAgentStorage (`storage.py`)**: Manages script output with timestamped directories.
- **LegoAgentResponse/Result (`models.py`)**: Pydantic models for structured data.

### Prompt System

**Prompts** (`prompts/templates/lego_agent/`):
- **system.jinja2**: Comprehensive system instructions (338 lines) with:
  - Orchestration pattern documentation
  - Available runtime API (create_agent, fan_out, summarize, judge_loop)
  - Tool descriptions (read_file, write_file, list_files, find_files, search_content, run_command)
  - Pattern examples with code blocks
  - Best practices section
- **user.jinja2**: User request with clarification history and validation checklist.
- **repair.jinja2**: Error correction prompt for JSON parsing failures.

## Data Flow

### Web UI Flow

```
Browser (Next.js) <── WebSocket ──> FastAPI (server.py)
                                        ↓
                                LegoAgentEngine
                                        ↓
                                    Clarification Loop
                                        ↓
                                    Script Generation
                                        ↓
                                    Execution
```

### TUI/CLI Flow

```
User Input (TUI or CLI)
    ↓
Config loading (sds.toml)
    ↓
LegoAgentEngine.run_async()
    ├─→ Clarification Loop (rounds 0 to max_clarifications):
    │   ├─→ Render system/user prompts
    │   ├─→ Stream agent thinking (LangGraph events)
    │   ├─→ Stream tool executions
    │   └─→ Parse response (clarify/ready status)
    │
    ├─→ If status="clarify": ask_questions() → next round
    ├─→ If status="ready": validate_script() → write to storage
    │
LegoAgentStorage.write_script()
    ↓
Script Execution (in work_dir)
    ├─→ Set PYTHONPATH to repo root
    ├─→ Stream stdout/stderr with visual formatting
    └─→ Return exit code
```

## Script Validation

Generated scripts must satisfy:
1. Define `MAX_ITERATIONS = {loop_bound}` constant.
2. Import from `lego_agent.runtime` or related modules.
3. Include `if __name__ == "__main__":` block.
4. Be valid Python with proper syntax.

## Output Structure

Scripts are saved in `lego_agent_runs/<timestamp>/lego_agent.py` with:
- Timestamped directory for each run.
- Standalone execution (no external dependencies except `lego_agent.runtime`).
- `MAX_ITERATIONS` constant for loop bounding.
- Orchestration tools: `fan_out()`, `summarize()`, `judge_loop()`.

## Usage Tips

- When adding lego_agent features: Test both TUI and CLI modes. Verify the generated scripts are syntactically valid and include required components.
- When debugging lego_agent scripts: Check `lego_agent_runs/<timestamp>/lego_agent.py` and examine the clarification history.
