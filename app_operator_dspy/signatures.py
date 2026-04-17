"""DSPy signature definitions for the DSPy-native operator.

Each signature defines the typed input/output contract for an agent module.
DSPy auto-generates prompts from these signatures and can optimize them
with BootstrapFewShot, MIPROv2, GEPA, etc.
"""

import dspy

# ---------------------------------------------------------------------------
# Code Analyzer
# ---------------------------------------------------------------------------


class AnalyzeCodebase(dspy.Signature):
    """Analyze a microservice repository to produce a deployment analysis and issue list.

    Use read_file, list_files, and run_shell to explore the repository.
    Identify all services, their technology stacks, ports, databases,
    environment variables, health endpoints, and inter-service dependencies.
    """

    repo_path: str = dspy.InputField(desc="Path to the repository")

    analysis: str = dspy.OutputField(
        desc=(
            "Structured markdown analysis covering: executive summary, services inventory "
            "table (name/tech/port/database/dependencies), per-service details (port, health "
            "endpoint, env vars), database requirements, ASCII dependency graph, recommended "
            "startup order, and environment variables summary"
        )
    )
    issues: str = dspy.OutputField(
        desc=(
            "Markdown deployment issues document with: TODO checklist "
            "(``- [ ] #N — title`` format) and per-issue blocks each containing "
            "Severity (Critical/High/Medium/Low), Confidence (High/Medium/Low with "
            "justification), Category, Affected service/file, Description, Evidence, "
            "Impact, and Recommended Fix"
        )
    )


# ---------------------------------------------------------------------------
# Deployer — Script Generation
# ---------------------------------------------------------------------------


class GenerateDeployScript(dspy.Signature):
    """Generate a bash deploy.sh for a microservice repository."""

    repo_path: str = dspy.InputField(desc="Absolute path to the repository root")
    code_analysis: str = dspy.InputField(
        desc="Structured code analysis with services inventory, ports, "
        "dependencies, and raw file contents including docker-compose"
    )
    deployment_issues: str = dspy.InputField(desc="Known deployment issues with severity and confidence")

    deploy_script: str = dspy.OutputField(
        desc=(
            "Raw bash script — no markdown fences. Requirements:\n"
            "- Saved to <repo>/.sds/deploy.sh; invoked as 'deploy.sh <command>' from repo root.\n"
            '- Path setup: APP_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/..") to reach repo '
            'root (one level UP from .sds/); then cd "$APP_DIR"; '
            "PROJECT_NAME=$(basename \"$APP_DIR\" | tr '[:upper:]' '[:lower:]'). "
            "Docker Compose REJECTS uppercase in project names.\n"
            "- Case-statement CLI parsing $1: start|stop|restart|status|logs|build|cleanup. "
            "No flags or getopts. No interactive prompts. Exit 0 on success, non-zero on failure.\n"
            '- Pass --project-name "$PROJECT_NAME" to EVERY docker compose command.\n'
            '- start MUST use: docker compose --project-name "$PROJECT_NAME" up --build -d\n'
            "  (--build forces source compilation for every service with a Dockerfile).\n"
            "- Use 'docker compose' (v2, space-separated), NEVER docker-compose (hyphen) or sudo.\n"
            "- Create one named Docker network; reference it in every service's networks: key.\n"
            "- Infrastructure services (DB, cache, MQ): use pre-built images, NO exposed ports, "
            "communicate over Docker network only.\n"
            "- Application services with source code: MUST use build: context pointing to local "
            "Dockerfile. NEVER use pre-built images for services with code in the repo.\n"
            "- NEVER add healthcheck: blocks to docker-compose.yml — all health check logic "
            "goes in health_check.sh. Use depends_on: condition: service_started (not service_healthy).\n"
            "- NEVER run build tools on host — all compilation in Dockerfile multi-stage builds.\n"
            "- NEVER expose ports except the single user-facing entry point (frontend/gateway). "
            "All internal services communicate over the Docker network.\n"
            "- If no docker-compose.yml exists: embed a generate_compose() shell function that "
            "writes docker-compose.yml from Dockerfiles and code analysis; call it before "
            "docker compose up.\n"
            "- Reconcile code analysis against deployment config: verify services, ports, "
            "startup order; flag missing/phantom services or mismatches."
        )
    )


class GenerateHealthCheckScript(dspy.Signature):
    """Generate a bash health_check.sh for a deployed application."""

    repo_path: str = dspy.InputField(desc="Absolute path to the repository root")
    code_analysis: str = dspy.InputField(
        desc="Structured code analysis with services inventory, ports, "
        "health endpoints, and raw file contents including docker-compose"
    )
    deployment_issues: str = dspy.InputField(desc="Known deployment issues with severity and confidence")

    health_check_script: str = dspy.OutputField(
        desc=(
            "Raw bash script — no markdown fences. Requirements:\n"
            "- Saved to <repo>/.sds/health_check.sh; invoked with no arguments from repo root.\n"
            "- Path setup: APP_DIR=$(pwd); "
            "PROJECT_NAME=$(basename \"$APP_DIR\" | tr '[:upper:]' '[:lower:]'). "
            "Docker Compose REJECTS uppercase in project names. "
            "Do NOT cd to the script's own directory.\n"
            '- Pass --project-name "$PROJECT_NAME" to EVERY docker compose command; '
            "NEVER use plain docker ps, docker-compose (hyphen), kubectl, Helm, or sudo.\n"
            "\n"
            "CRITICAL — Host vs Container networking:\n"
            "- NEVER curl Docker service names from the host (e.g. curl http://server:5000 "
            "— this ALWAYS FAILS because Docker service names only resolve inside the "
            "Docker network, not on the host).\n"
            "- Container status: docker compose --project-name $PROJECT_NAME ps\n"
            "- Host-port check: curl localhost:EXPOSED_PORT (ONLY for ports explicitly mapped "
            "to the host in docker-compose.yml ports: section).\n"
            "- Inter-service check: docker compose --project-name $PROJECT_NAME exec SERVICE "
            "curl http://OTHER_SERVICE:CONTAINER_PORT\n"
            "- Container logs: docker compose --project-name $PROJECT_NAME logs --tail=20 SERVICE "
            "to check for crash signals.\n"
            "\n"
            "- Do NOT grep response bodies or assume paths like /actuator/health unless "
            "code analysis confirms them.\n"
            "- Detect service count dynamically from docker compose ps; do not hardcode.\n"
            "- Track total/passed/failed/warnings; print summary with health score.\n"
            "- Use retries with backoff for slow-starting services.\n"
            "- Exit 0 if all checks pass, non-zero if any critical check fails."
        )
    )


# ---------------------------------------------------------------------------
# Deployer — Health Judge
# ---------------------------------------------------------------------------


class JudgeHealthCheck(dspy.Signature):
    """Assess whether a deployed application is healthy.

    Two-pronged verification:
    1. Run .sds/health_check.sh and observe exit code + output.
    2. Independently verify with platform commands — do NOT trust the script
       blindly. A passing script does NOT guarantee the app is healthy.

    Independent verification (Docker):
    - docker compose ps — check for Restarting, Exit, unhealthy
    - docker compose logs --tail=50 — check for errors, panics, crash loops
    - curl localhost:EXPOSED_PORT for host-mapped ports

    Source-build compliance:
    - Find all Dockerfiles in the repo. Each represents a service that must be
      built from source.
    - Verify deploy.sh contains a build step for each (--build flag or equivalent).
    - Exempt infrastructure (mongo, mysql, redis, rabbitmq, kafka, etc.).
    - If any non-exempt service has a Dockerfile but no build step, mark unhealthy.

    Signal recognition:
    - Restarting/CrashLoopBackOff → entrypoint failing
    - Exit 137 → OOMKill; Exit 139 → SIGSEGV
    - connection refused → wrong port or service not listening
    - no such host → DNS failure, wrong hostname
    - No logs after start → crash before logging initialized

    If the script disagrees with independent checks, fix the script first,
    re-run it, and repeat until script output matches reality.
    """

    repo_path: str = dspy.InputField(desc="Absolute path to the repository root")
    deploy_output: str = dspy.InputField(desc="Stdout/stderr from deploy.sh start")
    platform: str = dspy.InputField(desc="Deployment platform: docker or k8s")

    healthy: bool = dspy.OutputField(
        desc="Whether the application is healthy based on BOTH script and independent verification"
    )
    assessment: str = dspy.OutputField(
        desc="What the script reported vs what was independently observed, any discrepancies, what was fixed"
    )
    diagnosis: str = dspy.OutputField(
        desc="If unhealthy: concise list of which components are unhealthy and what symptoms "
        "(e.g. 'mongodb: CrashLoopBackOff exit 137; frontend: connection refused on 8080'). "
        "If healthy: empty string"
    )
    script_was_fixed: bool = dspy.OutputField(desc="Whether health_check.sh was modified")


# ---------------------------------------------------------------------------
# Deployer — Repair
# ---------------------------------------------------------------------------


class RepairDeploymentError(dspy.Signature):
    """Debug and fix a failed deployment by editing deploy.sh, health_check.sh, or docker-compose files."""

    repo_path: str = dspy.InputField(desc="Absolute path to the repository root")
    deploy_path: str = dspy.InputField(
        desc="Absolute path to deploy.sh (e.g. {repo_path}/.sds/deploy.sh). Read with read_file before editing."
    )
    health_path: str = dspy.InputField(
        desc="Absolute path to health_check.sh (e.g. {repo_path}/.sds/health_check.sh). "
        "Read with read_file before editing."
    )
    error_output: str = dspy.InputField(desc="Truncated stdout/stderr from the failed deploy or health check")
    fix_history: str = dspy.InputField(
        desc="History of previous fix attempts and their outcomes, "
        "grouped by failure pattern. Empty string on first attempt."
    )
    attempt: int = dspy.InputField(desc="Current attempt number")
    max_attempts: int = dspy.InputField(desc="Maximum allowed attempts")

    fix_summary: str = dspy.OutputField(
        desc=(
            "Brief summary of what issue(s) were found and what fix(es) were applied. "
            "Workflow: (1) read_file(deploy_path) and read_file(health_path) to load scripts; "
            "(2) analyze error_output and fix_history; "
            "(3) use write_file to persist fixes to deploy_path, health_path, or "
            "{repo_path}/docker-compose.override.yml for compose-level changes "
            "(port remapping, image tags, platform: linux/amd64); "
            "(4) never switch platforms, never drop --project-name, never use sudo; "
            "(5) form a hypothesis, verify against evidence, make targeted fixes — "
            "do not rewrite scripts from scratch; "
            "(6) use POSIX-compatible sed: [[:space:]] not \\s, sed -E for extended regex; "
            "APP_DIR must resolve to repo root not .sds/; no interactive prompts. "
            "Common patterns: Restarting→entrypoint failing; 'address already in use'→remap "
            "host port in override yml; image not found→try :latest or build from Dockerfile; "
            "connection refused→add depends_on; DNS failure→wrong hostname or missing network; "
            "no config file→embed generate_compose() in deploy.sh; arch crash→add "
            "platform: linux/amd64 in override yml."
        )
    )


class ConsolidateFixSummary(dspy.Signature):
    """Consolidate deployment fix attempt summaries into a structured history grouped by failure pattern."""

    existing_summary: str = dspy.InputField(
        desc=(
            "Current consolidated fix summary, or empty string if first attempt. "
            "Never remove or rewrite existing content — only add or merge."
        )
    )
    new_attempts: str = dspy.InputField(desc="New fix attempt summaries to incorporate")

    consolidated_summary: str = dspy.OutputField(
        desc=(
            "Updated fix history with new attempts merged in. "
            "Format: '# Deployment Fix History' header, then "
            "'## Failure Pattern: [Name]' sections each with "
            "'*Description: [brief]*' and '* **Attempt N**: [tried]. **Result**: [outcome].' bullets, "
            "then '## Chronological Timeline' with one bullet per attempt. "
            "Preserve all prior failure patterns even if apparently resolved."
        )
    )


# ---------------------------------------------------------------------------
# Monitor
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# RLM — Code Analyzer
# ---------------------------------------------------------------------------


class AnalyzeCodebaseRLM(dspy.Signature):
    """Analyze a microservice repository to produce a deployment analysis and issue list.

    You have the repo_path variable available. Use the provided tools
    (read_file, list_files, run_shell) to explore the repository
    programmatically.

    Follow this phased analysis process:

    Phase 1: Repository Structure Discovery
    - Identify project type (monorepo vs multi-repo) and build system
    - Enumerate all services via build markers (**/pom.xml, **/package.json,
      **/go.mod, **/Cargo.toml, **/Dockerfile)

    Phase 2: Deep Service Analysis
    For EACH service discovered:
    - Find its Dockerfile; check COPY/ADD source paths relative to build context
    - Check CMD/ENTRYPOINT — if missing, the service needs a command: override
    - Identify technology stack, framework, exposed ports, env vars
    - Check dependency files (requirements.txt, package.json, pom.xml) for
      known version conflicts
    - Find health check endpoints in source code

    Phase 3: Infrastructure Configuration
    - Analyze docker-compose.yml: compare declared services vs discovered services
    - Check for port conflicts, env var completeness, volume mounts, networks
    - Analyze any Kubernetes manifests or Helm charts if present

    Phase 4: Dependency Graph Construction
    - Map service dependencies and startup order
    - Identify database ownership per service

    Phase 5: Issue Detection
    CRITICAL CHECKS:
    - If a single Dockerfile is shared across multiple services (build args
      pattern), flag that each service needs explicit command: in docker-compose.yml
    - Check requirements.txt/package.json for known incompatible versions
    - Verify COPY/ADD source paths exist relative to expected build context
    - Identify health check endpoints for each service
    - Detect phantom services (in compose but no code/Dockerfile)
    - Detect missing services (in code but not in compose)
    - Check for hardcoded connection strings and IPs
    """

    repo_path: str = dspy.InputField(desc="Absolute path to the repository to analyze")

    analysis: str = dspy.OutputField(
        desc=(
            "Structured markdown analysis covering: executive summary, services inventory "
            "table (name/tech/port/database/dependencies), per-service details (port, health "
            "endpoint, env vars, Dockerfile location, CMD/ENTRYPOINT), database requirements, "
            "ASCII dependency graph, recommended startup order, and environment variables summary"
        )
    )
    issues: str = dspy.OutputField(
        desc=(
            "Markdown deployment issues document with: TODO checklist "
            "(``- [ ] #N — title`` format) and per-issue blocks each containing "
            "Severity (Critical/High/Medium/Low), Confidence (High/Medium/Low with "
            "justification), Category, Affected service/file, Description, Evidence, "
            "Impact, and Recommended Fix"
        )
    )


# ---------------------------------------------------------------------------
# RLM — Repair
# ---------------------------------------------------------------------------


class RepairDeploymentErrorRLM(dspy.Signature):
    """Debug and fix a failed deployment by analyzing error output and editing scripts.

    The error_context contains key-value pairs at the top (repo_path, deploy_path,
    health_path, deploy_log_path, deployment_progress_path, attempt, max_attempts)
    followed by ERROR_OUTPUT between ---ERROR_OUTPUT_START--- and ---ERROR_OUTPUT_END---
    markers, and FIX_HISTORY between ---FIX_HISTORY_START--- and ---FIX_HISTORY_END---
    markers.

    WORKFLOW:
    1. Read the deploy log file (deploy_log_path) FIRST for full error output
    2. Read deployment_progress.md to check previous hypotheses — do NOT re-try
       any approach marked "refuted" or "partial"
    3. Write your hypothesis to deployment_progress.md BEFORE applying fixes
    4. Read deploy.sh and health_check.sh COMPLETELY before editing
    5. Make TARGETED fixes based on evidence — form hypothesis, verify, fix
    6. After fixing, validate with: docker compose config --quiet (if available)

    INVARIANTS (apply in every repair session):
    1. NEVER run build tools on host — all compilation in Dockerfile
    2. NEVER rewrite deploy.sh, health_check.sh, or docker-compose.yml from scratch
       — make targeted edits only (read the file, find the broken line, fix only that line)
    3. Read files COMPLETELY before editing — verify full content
    4. NEVER drop or change --project-name from docker compose commands
    5. NEVER add healthcheck: blocks to docker-compose.yml
    6. NEVER use sudo, docker-compose (hyphen form), or pre-built images for app services
    7. NEVER expose ports except the single user-facing entry point
    8. Use POSIX-compatible sed: [[:space:]] not \\s, sed -E for extended regex

    SIGNAL RECOGNITION:
    - Restarting/CrashLoopBackOff → entrypoint failure (check CMD/command)
    - Exit 137 → OOMKill (increase memory limit)
    - Exit 139 → SIGSEGV (binary crash)
    - "connection refused" → wrong port or service not listening
    - "no such host" → DNS failure, wrong hostname in config
    - "Dockerfile not found" → wrong build context path
    - "COPY failed: file not found" → build context doesn't contain referenced files
    - "address already in use" → port conflict, remove ports: mapping

    COMMON PATTERNS:
    - Phantom services: service in compose has no code/Dockerfile → remove it
    - Missing CMD: shared Dockerfile needs per-service command: override in compose
    - Build context mismatch: context should be parent of Dockerfile, containing all
      COPY sources
    - DNS failure: service not on shared Docker network, or hostname typo

    CRITICAL: run_shell is NOT available. Do NOT run deploy.sh, docker compose up,
    or any deployment commands. The outer pipeline re-runs deployment automatically
    after your fix. Only read files, analyze errors, and write targeted fixes.
    """

    error_context: str = dspy.InputField(
        desc=(
            "Structured context with key-value pairs (repo_path, deploy_path, "
            "health_path, deploy_log_path, deployment_progress_path, attempt, "
            "max_attempts) followed by error output between "
            "---ERROR_OUTPUT_START/END--- markers and fix history between "
            "---FIX_HISTORY_START/END--- markers"
        )
    )

    fix_summary: str = dspy.OutputField(
        desc=(
            "Brief summary of what issue(s) were found and what fix(es) were applied. "
            "Workflow: (1) read deploy log and deployment_progress.md; "
            "(2) form hypothesis and write to deployment_progress.md; "
            "(3) read scripts completely; (4) make targeted fixes; "
            "(5) never rewrite files from scratch."
        )
    )


# ---------------------------------------------------------------------------
# Monitor
# ---------------------------------------------------------------------------


class AnalyzeHealthCheck(dspy.Signature):
    """Analyze health check output and classify application health status."""

    health_output: str = dspy.InputField(desc="Output from the health check script including exit code")
    check_number: int = dspy.InputField(desc="Current monitoring cycle number")

    status: str = dspy.OutputField(
        desc=(
            "Overall health classification — exactly one of: healthy, degraded, unhealthy. "
            "healthy = all checks pass; degraded = partial failures, system functional; "
            "unhealthy = critical failures, system not functional."
        )
    )
    summary: str = dspy.OutputField(
        desc=(
            "Executive summary of health status (2 lines max). "
            "Prioritize by severity; distinguish temporary blips from real issues."
        )
    )
    remediation: str = dspy.OutputField(
        desc=(
            "Prioritized remediation steps if status is degraded or unhealthy, empty string if healthy. "
            "Include short-term fixes and long-term improvements. Be specific."
        )
    )
