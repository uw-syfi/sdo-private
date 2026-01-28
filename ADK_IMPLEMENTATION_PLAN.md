# ADK Runtime Implementation Plan for SDS

## Goal
Add a third app operator runtime implementation using Google Agent Development Kit (ADK), parallel to `cli_agent` and `langgraph`. The ADK runtime should support the same lifecycle: code analysis, script generation, deployment with auto-fix retries, and health monitoring with summaries.

## ADK recap (for this plan)
- ADK provides Python agent primitives (`LlmAgent`) and workflow agents (e.g., `SequentialAgent`, `LoopAgent`) plus a `Runner` for execution. It supports function tools and plugins for tool/model callbacks.
- The ADK quickstart demonstrates agents using tools and being executed by a `Runner` with an in-memory session service.

## Design decisions (locked)
1. Runtime name: `adk`.
2. Orchestration: deterministic Python control flow like `cli_agent` (not LLM-driven graph), while using ADK `LlmAgent` for analysis, generation, fix, and health summary.
3. Tools: implement ADK function tools matching LangGraph tools (LS, Glob, Read, Grep, write_file, bash) with repository-root sandboxing.
4. Trajectory: integrate `TrajectoryRecorder` via an ADK plugin for model/tool callbacks, plus explicit user message logging per phase.
5. Model selection: require `agent.model` when `runtime.impl == "adk"`. Only support Gemini-family models via ADK (Gemini API or Vertex AI). Non-Gemini providers are not supported for ADK.

## Files to change/add

### 1) Config + CLI routing
- `app_operator/config.py`
  - Extend `RuntimeConfig.VALID_IMPLS` to include `"adk"`.
  - Update `Config.__post_init__` to require `agent.model` when `runtime.impl == "adk"`.
  - Add helper `validate_runtime_requirements(config: Config) -> None` to centralize langgraph/adk validation.
- `app_operator/commands/run.py`
  - Import `AdkOperator`.
  - Add branch for `config.runtime.impl == "adk"` that constructs `AdkOperator`.
- `sds.example.toml`
  - Allow `impl = "adk"` and document model requirement.
- `README.md`
  - Add ADK runtime section, requirements, and provider/model notes.

### 2) New ADK runtime package
Create `app_operator/adk/` with:

#### `app_operator/adk/__init__.py`
- Export `AdkOperator`.

#### `app_operator/adk/models.py`
- Function:
  ```python
  def build_adk_model(config: Config) -> object:
      """Return an ADK-compatible model handle for LlmAgent."""
  ```
- Logic:
  - Validate `config.agent.model`.
  - Normalize provider:
    - `gemini` or `vertex` -> return model string (e.g., "gemini-2.0-flash")
  - Raise `ValueError` for unsupported (non-Gemini) providers.

#### `app_operator/adk/tools.py`
- `ToolContext(repo_root: Path, filesystem: FileSystemInterface)` with `resolve_path()` to enforce repo-root containment.
- Tools (ADK function tools):
  ```python
  def ls(path: str = ".") -> str
  def glob(pattern: str) -> list[str]
  def read(path: str) -> str
  def grep(pattern: str, path: str = ".") -> list[str]
  def write_file(path: str, content: str) -> str
  def bash(command: str, timeout: int = 120) -> dict[str, object]
  ```
- `build_tools(repo_path: Path, filesystem: FileSystemInterface) -> list[Callable]` returns all functions bound to a `ToolContext`.

#### `app_operator/adk/trajectory_plugin.py`
- ADK plugin class:
  ```python
  class AdkTrajectoryPlugin(BasePlugin):
      def __init__(self, recorder: TrajectoryRecorderProtocol): ...
      def before_model_callback(self, context, request): ...
      def after_model_callback(self, context, response): ...
      def before_tool_callback(self, context, tool, args): ...
      def after_tool_callback(self, context, tool, result): ...
      def on_tool_error_callback(self, context, tool, error): ...
  ```
- Use recorder methods:
  - `add_assistant_message()` in `after_model_callback` (extract text from response)
  - `add_tool_call()` in tool callbacks with stdout/stderr/exit_code where available

#### `app_operator/adk/runner.py`
- Wrapper to run an agent once and return assistant text:
  ```python
  class AdkAgentRunner:
      def __init__(self, app_name: str, recorder: TrajectoryRecorderProtocol, repo_path: Path): ...
      def run_once(self, agent: LlmAgent, user_prompt: str) -> str: ...
  ```
- Implementation:
  - Create `InMemorySessionService()` per operator run.
  - Create `Runner(agent=agent, app_name=app_name, session_service=...)`.
  - Use `runner.run_async(user_id="sds", session_id=run_id, new_message=user_prompt)` and extract final model response text.
  - Install `AdkTrajectoryPlugin` via `plugins=[...]` or `Runner` config.

#### `app_operator/adk/agent_factory.py`
- Build specialized ADK LLM agents with shared tools and model:
  ```python
  def build_adk_agent(name: str, instruction: str, model: object, tools: list[Callable]) -> LlmAgent
  ```
- Optionally include `description` for clarity.

#### `app_operator/adk/operator.py`
- Main operator class:
  ```python
  class AdkOperator:
      def __init__(
          self,
          repo_path: str,
          health_check_interval: int = 30,
          health_check_max_count: int | None = 5,
          max_deployment_attempts: int = 5,
          filesystem: FileSystemInterface | None = None,
          config: Config | None = None,
      ) -> None

      def run(self) -> int
  ```
- Internal methods:
  ```python
  def _persist_deployment_config(self) -> None
  def _run_analysis(self) -> None
  def _generate_scripts(self) -> None
  def _deploy_with_retries(self) -> bool
  def _run_health_check(self, attempt: int) -> dict[str, object]
  def _monitor(self) -> None
  def _run_agent_phase(self, phase: Phase, agent: LlmAgent, user_prompt: str, context: dict) -> str
  def _run_deploy_command(self, command: str, timeout: int) -> dict[str, object]
  def _cleanup(self) -> None
  ```
- Behavior:
  - Create `TrajectoryRecorder`, set agent name "ADK".
  - Build tools and model once, then create agents for each task using prompts:
    - Code Analyzer: `code_analyzer/system.jinja2` + `code_analyzer/user.jinja2`
    - Script Generator: `deployment_context.create_system_prompt()` + `create_generate_script_prompt()` for deploy/health
    - Error Fixer: `create_fix_prompt()`
    - Health Monitor: `monitor/analyze_health.jinja2`
  - On each LLM phase:
    - `recorder.start_phase()` with context (attempt number, etc.)
    - `recorder.add_user_message(user_prompt)`
    - Run ADK agent via `AdkAgentRunner.run_once`
    - `recorder.end_phase("success" | "failed" | "needs_retry")`
  - Use `run_script` logic mirroring `langgraph.utils.run_script` for deploy/health.
  - Deploy loop:
    - Ensure `.sds/deploy.sh` and `.sds/health_check.sh` exist (generate if missing).
    - Attempt `deploy.sh start` with logs: `.sds/logs/deploy_attempt_N.log`.
    - If deploy or health check fails, run Error Fixer (with log context) then retry until max attempts.
  - Monitoring:
    - Use `health_check.sh` on interval, log output, then call Health Monitor LLM to summarize into `.sds/logs/monitor/analysis_N.log`.

### 3) Tests
- `tests/unit/config/test_runtime_config.py`
  - Add `test_runtime_impl_adk_requires_model()`.
  - Add `test_runtime_impl_adk_accepts_model()`.
- `tests/unit/adk/test_tools.py`
  - Validate `resolve_path` prevents escaping repo root.
  - Validate `write_file` creates parent dirs and writes content in `InMemoryFilesystem`.
- `tests/unit/adk/test_operator_deploy_loop.py`
  - Use `InMemoryFilesystem` + stubbed `AdkAgentRunner` to simulate:
    - Successful deploy on first try.
    - Failure then fix then success (validate logs and `.sds` files created).
- `tests/unit/adk/test_runner.py`
  - Unit test that `AdkAgentRunner` calls `Runner.run_async` and returns final text (use a fake Runner in tests).

### 4) Dependencies and docs
- `pyproject.toml`
  - Add dependency: `google-adk>=0.1.0` (pin based on latest tested version).
- `README.md`
  - Add ADK setup instructions:
    - `pip install google-adk` via project deps.
    - `GOOGLE_API_KEY` or Vertex AI env variables as needed.
  - Explain `[runtime] impl = "adk"` and `agent.model` requirement.
- `sds.example.toml`
  - Add sample `adk` config block.

### 5) Formatting and lint
- Run `@scripts/format_code.sh` after code edits.
- Run `@scripts/check_errors.sh` (add `--fix` if needed).

## Acceptance criteria
1. `sds_operator run <repo>` works with `[runtime] impl = "adk"` and produces:
   - `.sds/deploy.sh`, `.sds/health_check.sh`
   - `.sds/logs/deploy_attempt_*.log`
   - `.sds/trajectories/trajectory_*.json` (with ADK tool/model calls recorded)
2. Config validation fails with clear error if `runtime.impl == "adk"` and `agent.model` is missing.
3. Unit tests for adk tools and runtime config pass.

## Rollout / compatibility
- Default runtime remains `cli_agent`.
- ADK is opt-in via config; no behavior change for existing users.
