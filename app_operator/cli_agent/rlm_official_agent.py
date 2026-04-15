"""Official RLM library-based coding agent for the SDS operator.

Wraps the ``rlm`` package (``pip install rlms``) to provide depth-2 recursive
language model completion with a Python REPL.  The root LM can delegate
sub-problems to child RLMs that each have their own exploratory REPL.

Custom tools give the REPL access to SDS filesystem/shell utilities so the
model can read files, list directories, run shell commands, and write
deployment artifacts.

Register with ``provider = "rlm-official"`` in ``sds.toml``.
"""

from __future__ import annotations

import os
import re
import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

from libs.agent_cli.base import CodingAgent, register_provider
from libs.agent_cli.trajectory import NullTrajectoryRecorder, TrajectoryRecorderProtocol

if TYPE_CHECKING:
    from libs.agent_cli.events import AgentEventHandler


def _build_custom_tools(repo_path: Path) -> dict[str, Any]:
    """Build the custom_tools dict injected into the RLM REPL namespace.

    All tools are sandboxed to *repo_path* — file and shell operations refuse
    to escape the repository root.
    """
    root = str(repo_path.resolve())

    def _resolve_safe(path: str) -> str:
        """Resolve *path* relative to repo root and reject escapes."""
        resolved = os.path.realpath(os.path.join(root, path))
        if not resolved.startswith(root + os.sep) and resolved != root:
            raise PermissionError(f"Access denied: {path!r} resolves outside the repo")
        return resolved

    def read_file(path: str) -> str:
        """Read a file from the repository.  *path* is relative to repo root."""
        full = _resolve_safe(path)
        try:
            result = Path(full).read_text()
            print(result)
            return result
        except OSError as exc:
            msg = f"Error reading {path}: {exc}"
            print(msg)
            return msg

    def write_file(path: str, content: str) -> str:
        """Write *content* to a file inside the repository (overwrites)."""
        full = _resolve_safe(path)
        Path(full).parent.mkdir(parents=True, exist_ok=True)
        Path(full).write_text(content)
        msg = f"Wrote {len(content)} bytes to {path}"
        print(msg)
        return msg

    def append_file(path: str, content: str) -> str:
        """Append *content* to a file (creates if missing). Use this to build long files incrementally."""
        full = _resolve_safe(path)
        Path(full).parent.mkdir(parents=True, exist_ok=True)
        with open(full, "a") as f:
            f.write(content)
        msg = f"Appended {len(content)} bytes to {path}"
        print(msg)
        return msg

    def list_files(path: str = ".", recursive: bool = False) -> list[str]:
        """List files/directories under *path* (relative to repo root).

        If *recursive* is True, walk the tree and return all file paths.
        """
        full = _resolve_safe(path)
        try:
            if recursive:
                result: list[str] = []
                for dirpath, _dirs, files in os.walk(full):
                    for f in files:
                        rel = os.path.relpath(os.path.join(dirpath, f), full)
                        result.append(rel)
                entries = sorted(result)
            else:
                entries = sorted(os.listdir(full))
            print("\n".join(entries))
            return entries
        except OSError as exc:
            msg = f"Error listing {path}: {exc}"
            print(msg)
            return [msg]

    def run_shell(cmd: str, timeout: int = 60) -> str:
        """Run a diagnostic shell command inside the repository root.
        Cannot run deployment commands (deploy.sh start, docker compose up).

        Returns combined stdout+stderr, truncated to 20 000 chars.
        """
        # Block deployment commands — the pipeline handles deployment
        _blocked = (
            "deploy.sh start",
            "deploy.sh restart",
            "docker compose up",
            "docker compose start",
            "docker-compose up",
            "docker-compose start",
        )
        if any(b in cmd for b in _blocked):
            msg = (
                "ERROR: Cannot run deployment commands from REPL. "
                "The pipeline re-deploys automatically after your fixes."
            )
            print(msg)
            return msg
        try:
            result = subprocess.run(  # noqa: S602
                cmd,
                shell=True,
                cwd=root,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            output = result.stdout + result.stderr
            if len(output) > 20_000:
                output = output[:20_000] + "\n... [truncated]"
            print(output)
            return output
        except subprocess.TimeoutExpired:
            msg = f"Command timed out after {timeout}s: {cmd}"
            print(msg)
            return msg
        except OSError as exc:
            msg = f"Error running command: {exc}"
            print(msg)
            return msg

    def validate_compose(compose_path: str = "docker-compose.yml") -> str:
        """Validate a docker-compose file. Returns errors or 'Valid'."""
        full = _resolve_safe(compose_path)
        if not Path(full).exists():
            msg = f"File not found: {compose_path}"
            print(msg)
            return msg
        try:
            result = subprocess.run(
                ["docker", "compose", "-f", full, "config", "--quiet"],
                capture_output=True,
                text=True,
                timeout=30,
                cwd=root,
            )
            if result.returncode == 0:
                print("Valid")
                return "Valid"
            msg = f"Invalid: {result.stderr}"
            print(msg)
            return msg
        except (subprocess.TimeoutExpired, OSError) as exc:
            msg = f"Validation error: {exc}"
            print(msg)
            return msg

    def check_build_paths(compose_path: str = "docker-compose.yml") -> str:
        """Check if all build context paths in docker-compose.yml exist."""
        full = _resolve_safe(compose_path)
        if not Path(full).exists():
            msg = f"File not found: {compose_path}"
            print(msg)
            return msg

        try:
            content = Path(full).read_text()
        except OSError as exc:
            msg = f"Error reading {compose_path}: {exc}"
            print(msg)
            return msg

        # Extract build context paths from YAML using regex
        context_pattern = re.compile(r"^\s*context:\s*(.+)$", re.MULTILINE)
        matches = context_pattern.findall(content)
        if not matches:
            msg = "No build context paths found"
            print(msg)
            return msg

        issues: list[str] = []
        for ctx in matches:
            ctx = ctx.strip().strip("'\"")
            ctx_full = os.path.realpath(os.path.join(root, ctx))
            if not os.path.exists(ctx_full):
                issues.append(f"Missing: {ctx}")

        if issues:
            msg = "Build path issues:\n" + "\n".join(issues)
        else:
            msg = f"All {len(matches)} build context paths exist"
        print(msg)
        return msg

    return {
        "read_file": {
            "tool": read_file,
            "description": "Read a file from the repo (path relative to repo root)",
        },
        "write_file": {
            "tool": write_file,
            "description": "Write content to a file in the repo (overwrites)",
        },
        "append_file": {
            "tool": append_file,
            "description": "Append content to a file (creates if missing). Use for building long files incrementally.",
        },
        "list_files": {
            "tool": list_files,
            "description": "List files/directories (path relative to repo root). Pass recursive=True for full tree.",
        },
        "run_shell": {
            "tool": run_shell,
            "description": "Run a shell command in the repo root",
        },
        "validate_compose": {
            "tool": validate_compose,
            "description": "Validate a docker-compose file syntax. Returns 'Valid' or error details.",
        },
        "check_build_paths": {
            "tool": check_build_paths,
            "description": "Check if all build context paths in docker-compose.yml exist.",
        },
        "REPO_PATH": {
            "tool": root,
            "description": "Absolute path to the target repository",
        },
    }


def _build_context_text(repo_path: Path) -> str:
    """Build a context string from available SDS artifacts for the RLM prompt."""
    sds = repo_path / ".sds"

    def _read(p: Path) -> str:
        try:
            return p.read_text() if p.exists() else ""
        except OSError:
            return ""

    parts: list[str] = []
    parts.append(f"Repository: {repo_path}")

    # Code analysis
    analysis = _read(sds / "code_analysis.md")
    if analysis:
        parts.append(f"\n=== Code Analysis ===\n{analysis}")

    # Deploy script
    deploy_sh = _read(sds / "deploy.sh")
    if deploy_sh:
        parts.append(f"\n=== deploy.sh ===\n{deploy_sh}")

    # Health check script
    health_sh = _read(sds / "health_check.sh")
    if health_sh:
        parts.append(f"\n=== health_check.sh ===\n{health_sh}")

    # Latest deploy log
    logs = sds / "logs"
    deploy_log = _find_latest_log(logs, "deploy_attempt_", "deploy.log")
    if deploy_log:
        parts.append(f"\n=== Latest Deploy Log ===\n{deploy_log}")

    # Latest health check log
    health_log = _find_latest_log(logs, "health_check_attempt_", "health_check.log")
    if health_log:
        parts.append(f"\n=== Latest Health Check Log ===\n{health_log}")

    # Dockerfile
    dockerfile = _read(repo_path / "Dockerfile")
    if dockerfile:
        parts.append(f"\n=== Dockerfile ===\n{dockerfile}")

    # docker-compose
    compose = _read(repo_path / "docker-compose.yml") or _read(repo_path / "docker-compose.yaml")
    if compose:
        parts.append(f"\n=== docker-compose.yml ===\n{compose}")

    # README
    readme = _read(repo_path / "README.md") or _read(repo_path / "README.rst")
    if readme:
        parts.append(f"\n=== README ===\n{readme}")

    return "\n".join(parts)


def _find_latest_log(logs_dir: Path, prefix: str, fallback_name: str) -> str:
    """Find the latest numbered log file or fall back to a default name."""
    if not logs_dir.exists():
        return ""

    numbered: list[tuple[int, Path]] = []
    for p in logs_dir.iterdir():
        m = re.match(rf"^{re.escape(prefix)}(\d+)\.log$", p.name)
        if m:
            numbered.append((int(m.group(1)), p))
    numbered.sort(key=lambda t: t[0])

    if numbered:
        try:
            return numbered[-1][1].read_text()
        except OSError:
            return ""

    fallback = logs_dir / fallback_name
    try:
        return fallback.read_text() if fallback.exists() else ""
    except OSError:
        return ""


_SDS_ROOT_PROMPT_PREFIX = """\
You are an SDS deployment operator agent. Your task is to deploy, diagnose,
and repair application deployments.

Use the provided tools (read_file, write_file, append_file, list_files,
run_shell, validate_compose, check_build_paths) and the REPO_PATH variable
to explore the repository.

IMPORTANT: When writing long files (markdown reports, scripts), build them
incrementally using append_file() in multiple code blocks. Do NOT try to define
very long strings in a single code block — this causes SyntaxErrors from
unterminated triple-quoted strings. Instead:
  write_file('path', '')  # clear the file
  append_file('path', 'section 1 content\\n')
  append_file('path', 'section 2 content\\n')

===============================================================================
INVARIANTS — apply in EVERY session, no exceptions
===============================================================================

1. NO HOST ARTIFACTS: Never run build tools (compilers, package managers,
   bundlers) on the host machine. All compilation must happen inside
   Dockerfiles using multi-stage builds. If a Dockerfile lacks a build
   stage, add one.

2. NO WHOLESALE REWRITES: NEVER rewrite deploy.sh, health_check.sh,
   docker-compose.yml, or any config file from scratch. Wholesale rewrites
   discard working sections and introduce new errors. Read the file fully,
   find the broken line, fix THAT specific line. If you need to regenerate,
   write to a NEW file and compare before replacing.

3. READ BEFORE EDIT: Before editing any file, read its full content with
   read_file(). Never edit a file you have only partially read.

===============================================================================
CODE ANALYSIS TASKS
===============================================================================

When analyzing code, follow these phases:

Phase 1 — Repository structure discovery:
  - Identify project type (monorepo vs multi-repo) and build system
  - Use list_files(recursive=True) to enumerate service directories

Phase 2 — Service enumeration and deep analysis:
  For EACH service discovered:
  - Find its Dockerfile. Check COPY/ADD paths, CMD/ENTRYPOINT, exposed ports.
  - If using a SHARED Dockerfile (multi-service pattern with build args),
    flag that each service needs a `command:` override in docker-compose.yml.
  - Check for explicit CMD/ENTRYPOINT — if missing, the container will exit
    immediately.
  - Identify technology stack, database requirements, environment variables,
    exposed ports, inter-service dependencies.

Phase 3 — Dependency conflict detection:
  - Check requirements.txt / package.json for known incompatible packages.
  - For Python: check if opentelemetry-exporter-jaeger is used with
    opentelemetry-sdk — these have version compatibility requirements.
  - Check for conflicting database driver versions.

Phase 4 — Dependency graph:
  - Build a service dependency graph with startup order.
  - Identify circular dependencies.
  - Map database ownership.

Phase 5 — Issue detection:
  - Configuration mismatches, resource conflicts, missing components.
  - Phantom services (in compose but no code), missing services.
  - Port conflicts, credential mismatches, hardcoded connection strings.

Output: `.sds/code_analysis.md` and `.sds/deployment_issues.md`

===============================================================================
SCRIPT GENERATION TASKS (deploy.sh / health_check.sh / docker-compose.yml)
===============================================================================

Docker Compose rules:
- ALWAYS pass `--project-name "$PROJECT_NAME"` to every `docker compose` command.
- PROJECT_NAME MUST be lowercased:
    PROJECT_NAME=$(basename "$APP_DIR" | tr '[:upper:]' '[:lower:]')
- ALWAYS use `--build` when starting services (docker compose up --build -d).
- Create ONE named Docker network shared by all services.
- NEVER add `healthcheck:` blocks to compose files — all health check logic
  belongs in `.sds/health_check.sh`.
- Use `depends_on: condition: service_started` (never `service_healthy`).

Build context rules:
- Build context must point to the directory containing the Dockerfile.
- Verify all build context paths exist with list_files() before using them.
- Application services with source code MUST use `build:` with local source.
  NEVER use pre-built images from Docker Hub for app services.
- Infrastructure services (DB, cache, MQ) use pre-built images and MUST NOT
  have exposed host ports.

Host port rules:
- NEVER expose ports for infrastructure services (databases, caches, MQ,
  tracing, service discovery). They communicate over the Docker network.
- Only the single user-facing entry point (frontend / API gateway) gets a
  `ports:` mapping.

Health check script rules — CRITICAL:
- Health checks run on the HOST, not inside Docker. Docker service names
  do NOT resolve from the host.
- To check container status: `docker compose ps`
- To check the entry point: `curl localhost:<EXPOSED-HOST-PORT>` (only ports
  mapped in the compose file's `ports:` section)
- To check inter-service connectivity: `docker compose exec <service> curl http://<internal-service>:<container-port>`
- NEVER run `curl http://<docker-service-name>:<port>` from the host — this
  ALWAYS fails because Docker DNS only resolves inside the Docker network.

===============================================================================
REPAIR / FIX-ERROR TASKS
===============================================================================

STEP 0 — Read deployment progress:
  Read `.sds/deployment_progress.md` with read_file() to see what was already
  tried. Do NOT re-try any approach previously marked "refuted" or "partial".

STEP 1 — Write your hypothesis BEFORE fixing:
  Append to `.sds/deployment_progress.md`:
  - Attempt number
  - Hypothesis: your root cause diagnosis
  - Fix planned: specific changes you will make
  - Success criteria: observable outcome confirming your hypothesis
  - Disproved if: what outcome would show this hypothesis was wrong

STEP 2 — Read deploy attempt logs:
  Read `.sds/logs/deploy_attempt_N.log` with read_file() to see the actual
  error output from the last deployment. Then read `docker compose logs`
  output via run_shell(). Use BOTH to diagnose.

STEP 3 — Read the deployment scripts:
  Read `.sds/deploy.sh` and `.sds/health_check.sh` to understand the current
  deployment setup and identify the platform (Docker Compose vs Kubernetes).

STEP 4 — Diagnose using error signal patterns:
  - Restarting / CrashLoopBackOff → entrypoint failing
  - Exit 137 → OOMKill
  - Exit 139 → SIGSEGV
  - "connection refused" → service not listening, wrong port, or startup race
  - DNS failure / "no such host" → service not on shared network, or hostname
    typo, or health check curling Docker service name from host
  - "address already in use" → port conflict
  - No logs after start → crash before logging initialized
  - panic:/fatal: → application bug or missing dependency

STEP 5 — Make TARGETED fixes:
  - Read the file, find the broken line, fix that specific line.
  - Do NOT rewrite files from scratch.
  - The outer pipeline will re-run deployment after your fix — do NOT run
    deploy.sh yourself.

STEP 6 — Validate after editing:
  - Run validate_compose() after editing docker-compose.yml.
  - Run check_build_paths() to verify build contexts.
  - Check for regressions: exposed ports still correct, no healthcheck blocks
    added, --project-name still present.

STEP 7 — Record outcome:
  After fixing, update `.sds/deployment_progress.md` with what you changed.

Common SDS failure patterns:
  - Phantom services: compose references a service with no code/Dockerfile
  - Build context mismatch: context path doesn't contain a Dockerfile
  - Missing CMD: shared Dockerfile without per-service command overrides
  - Health check DNS failure: health_check.sh curls Docker service names
    from the host instead of using localhost with exposed ports

When you see a rate limit or timeout error in the output, note it in your
fix_summary but focus on fixing the actual deployment issue, not the rate limit.

"""


@register_provider("rlm-official")
class RLMOfficialAgent(CodingAgent):
    """Coding agent wrapping the official ``rlm`` library (``pip install rlms``).

    Uses depth-2 RLM recursion: the root LM can delegate sub-problems to
    child RLMs that each get their own Python REPL for iterative exploration.

    For file-generation tasks (code analysis, deploy.sh creation) the agent
    falls back to a single litellm call, matching the existing RLM agent
    behaviour.
    """

    def __init__(
        self,
        model: str | None = None,
        recorder: TrajectoryRecorderProtocol | None = None,
        event_handler: AgentEventHandler | None = None,
        location: str | None = None,
        dspy_config: object | None = None,
        max_depth: int = 2,
        max_iterations: int = 30,
    ):
        self.model = model or "gemini-2.5-pro"
        self.recorder: TrajectoryRecorderProtocol = recorder or NullTrajectoryRecorder()
        self.event_handler = event_handler
        self.location = location
        self.dspy_config = dspy_config
        self.max_depth = max_depth
        self.max_iterations = max_iterations

    def _resolve_backend(self) -> tuple[str, dict[str, Any]]:
        """Pick the RLM backend based on available credentials.

        Uses the native ``gemini`` backend when ``GEMINI_API_KEY`` is set,
        otherwise falls back to ``litellm`` which supports Vertex AI via
        Application Default Credentials.
        """
        model_name = self.model
        if "/" in model_name:
            model_name = model_name.split("/", 1)[1]

        if os.environ.get("GEMINI_API_KEY"):
            logger.info("[RLM-Official] Using gemini backend (GEMINI_API_KEY)")
            return "gemini", {"model_name": model_name}

        # Fall back to litellm which handles Vertex AI auth via ADC
        litellm_model = f"vertex_ai/{model_name}"
        logger.info("[RLM-Official] Using litellm backend (Vertex AI): {}", litellm_model)
        return "litellm", {"model_name": litellm_model}

    def generate(
        self,
        prompt: str,
        cwd: str | None = None,
        timeout: int = 300,
        silent: bool = False,
    ) -> str:
        repo_path = Path(cwd) if cwd else Path.cwd()

        # Route ALL tasks through RLM so the agent can explore the repo
        # via REPL tools. The direct LLM call path is kept only as a
        # fallback if RLM import fails.
        return self._run_rlm(prompt, repo_path, timeout)

    # ------------------------------------------------------------------
    # Retry helpers
    # ------------------------------------------------------------------

    _RATE_LIMIT_BACKOFF = (30, 60, 120)  # seconds between retries

    @staticmethod
    def _is_rate_limit_error(exc: Exception) -> bool:
        """Return True if *exc* looks like a rate-limit / quota error."""
        msg = str(exc).lower()
        indicators = ("429", "resource_exhausted", "rate limit", "ratelimit", "quota")
        return any(ind in msg for ind in indicators)

    # ------------------------------------------------------------------
    # RLM completion (depth-2 recursion with REPL)
    # ------------------------------------------------------------------

    def _run_rlm(self, prompt: str, repo_path: Path, timeout: int) -> str:
        """Run the official RLM library for fix/analysis tasks."""
        try:
            from rlm import RLM  # type: ignore[import-not-found]
            from rlm.logger import RLMLogger  # type: ignore[import-not-found]
        except ImportError as exc:
            logger.error(f"[RLM-Official] rlm library not installed: {exc}")
            return f"rlm library not available: {exc}"

        custom_tools = _build_custom_tools(repo_path)
        context_text = _build_context_text(repo_path)

        log_dir = str(repo_path / ".sds" / "rlm_logs")
        os.makedirs(log_dir, exist_ok=True)
        rlm_logger = RLMLogger(log_dir=log_dir)

        backend, backend_kwargs = self._resolve_backend()

        rlm = RLM(
            backend=backend,  # type: ignore[arg-type]
            backend_kwargs=backend_kwargs,
            other_backends=[backend],  # type: ignore[list-item]
            other_backend_kwargs=[backend_kwargs],
            environment="local",
            max_depth=self.max_depth,
            max_iterations=self.max_iterations,
            max_timeout=max(float(timeout), 300.0) if timeout else None,
            custom_tools=custom_tools,
            logger=rlm_logger,
            verbose=True,
        )

        root_prompt = _SDS_ROOT_PROMPT_PREFIX + prompt
        last_exc: Exception | None = None
        try:
            for attempt in range(1 + len(self._RATE_LIMIT_BACKOFF)):
                try:
                    result = rlm.completion(prompt=context_text, root_prompt=root_prompt)
                    break
                except KeyboardInterrupt:
                    raise
                except Exception as exc:
                    last_exc = exc
                    if attempt < len(self._RATE_LIMIT_BACKOFF) and self._is_rate_limit_error(exc):
                        delay = self._RATE_LIMIT_BACKOFF[attempt]
                        logger.warning(
                            f"[RLM-Official] Rate limit hit (attempt {attempt + 1}), retrying in {delay}s: {exc}"
                        )
                        time.sleep(delay)
                        continue
                    logger.error(f"[RLM-Official] RLM completion failed: {exc}")
                    return f"RLM completion failed: {exc}"
            else:
                logger.error(f"[RLM-Official] All retries exhausted: {last_exc}")
                return f"RLM completion failed after retries: {last_exc}"

            # Record usage in trajectory
            self._record_usage(result)

            answer = result.response
            logger.info(f"[RLM-Official] Completed in {result.execution_time:.1f}s")

            # Post-process any scripts the RLM may have written via tools
            self._post_process_scripts(repo_path)

            return answer

        except KeyboardInterrupt:
            logger.warning("[RLM-Official] Interrupted by user")
            raise
        finally:
            rlm.close()

    # ------------------------------------------------------------------
    # Post-processing: deterministic script fixes
    # ------------------------------------------------------------------

    _PROJECT_NAME_RE = re.compile(r"^(\s*PROJECT_NAME)=.*$", re.MULTILINE)
    _PROJECT_NAME_FIX = "$(basename \"$APP_DIR\" | tr '[:upper:]' '[:lower:]')"
    _DOCKER_COMPOSE_HYPHEN_RE = re.compile(r"\bdocker-compose\b")

    def _post_process_scripts(self, repo_path: Path) -> None:
        """Apply deterministic fixes to deploy.sh and health_check.sh."""
        sds = repo_path / ".sds"
        for name in ("deploy.sh", "health_check.sh"):
            script = sds / name
            if script.exists():
                self._post_process_script(script)

    def _post_process_script(self, path: Path) -> None:
        """Apply deterministic fixes to a single script file.

        - Enforce lowercased PROJECT_NAME via ``tr``
        - Replace ``docker-compose`` (hyphen) with ``docker compose`` (space)
        """
        try:
            content = path.read_text()
        except OSError:
            return

        original = content

        # Fix PROJECT_NAME to use lowercase transform
        def _replace_project_name(m: re.Match[str]) -> str:
            return f"{m.group(1)}={self._PROJECT_NAME_FIX}"

        content = self._PROJECT_NAME_RE.sub(_replace_project_name, content)

        # Replace docker-compose (hyphenated) with docker compose (space)
        content = self._DOCKER_COMPOSE_HYPHEN_RE.sub("docker compose", content)

        if content != original:
            path.write_text(content)
            logger.info(f"[RLM-Official] Post-processed {path.name}")

    def _record_usage(self, result: Any) -> None:
        """Record RLM token usage into the SDS trajectory recorder."""
        if not hasattr(result, "usage_summary") or result.usage_summary is None:
            return

        try:
            usage = result.usage_summary
            total_input = 0
            total_output = 0
            for _model, model_usage in usage.model_usage_summaries.items():
                total_input += model_usage.total_input_tokens
                total_output += model_usage.total_output_tokens

            token_data = {
                "prompt_tokens": total_input,
                "completion_tokens": total_output,
                "total_tokens": total_input + total_output,
            }
            if hasattr(self.recorder, "record_token_usage"):
                self.recorder.record_token_usage(token_data)  # type: ignore[arg-type]

            logger.info(
                f"[RLM-Official] Tokens: {total_input} in, {total_output} out, {total_input + total_output} total"
            )
        except (AttributeError, TypeError) as exc:
            logger.warning(f"[RLM-Official] Failed to record token usage: {exc}")
