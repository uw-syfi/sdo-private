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
            'PROJECT_NAME=$(basename "$APP_DIR").\n'
            "- Case-statement CLI parsing $1: start|stop|restart|status|logs|build|cleanup. "
            "No flags or getopts. No interactive prompts. Exit 0 on success, non-zero on failure.\n"
            '- Pass --project-name "$PROJECT_NAME" to every docker compose command.\n'
            '- start MUST use: docker compose --project-name "$PROJECT_NAME" up --build -d\n'
            "- Use 'docker compose' (v2, space-separated), never docker-compose (hyphen) or sudo.\n"
            "- Create one named Docker network shared by all services.\n"
            "- If no docker-compose.yml exists: embed a generate_compose() shell function that "
            "writes docker-compose.yml from Dockerfiles and code analysis; call it before "
            "docker compose up. Include healthchecks and depends_on where possible.\n"
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
            '- Path setup: APP_DIR=$(pwd); PROJECT_NAME=$(basename "$APP_DIR"). '
            "Do NOT cd to the script's own directory.\n"
            '- Pass --project-name "$PROJECT_NAME" to every docker compose command; '
            "never use plain docker ps, docker-compose (hyphen), kubectl, Helm, or sudo.\n"
            "- Check container status via 'docker compose ps'.\n"
            "- Check HTTP endpoints ONLY on ports explicitly exposed in docker-compose "
            "(host-mapped ports) using 'curl -sf -o /dev/null'. Do NOT grep response bodies "
            "or assume paths like /actuator/health unless code analysis confirms them.\n"
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

    Run the health check script, independently verify with platform commands
    (docker compose ps, logs, curl), fix the script if it is buggy, and
    validate that all services with Dockerfiles are built from source.
    """

    repo_path: str = dspy.InputField(desc="Absolute path to the repository root")
    deploy_output: str = dspy.InputField(desc="Stdout/stderr from deploy.sh start")
    platform: str = dspy.InputField(desc="Deployment platform: docker or k8s")

    healthy: bool = dspy.OutputField(desc="Whether the application is healthy")
    assessment: str = dspy.OutputField(desc="What the script reported vs what was independently observed")
    diagnosis: str = dspy.OutputField(desc="If unhealthy: symptoms and root causes. If healthy: empty string")
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
    programmatically. Read Dockerfiles, docker-compose files, package.json,
    requirements.txt, and source code to identify services, ports,
    dependencies, and potential deployment issues.
    """

    repo_path: str = dspy.InputField(desc="Absolute path to the repository to analyze")

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
# RLM — Repair
# ---------------------------------------------------------------------------


class RepairDeploymentErrorRLM(dspy.Signature):
    """Debug and fix a failed deployment by analyzing error output and editing scripts.

    You have the error_context variable containing the error output, file paths,
    fix history, and attempt info. Use the provided tools (read_file, write_file,
    run_shell) to read the current scripts, analyze what went wrong, and write
    fixes. Do NOT rewrite scripts from scratch — make targeted fixes based on
    evidence from the error output.
    """

    error_context: str = dspy.InputField(
        desc=(
            "Structured context containing: repo_path, deploy_path, health_path, "
            "error_output from the failed deploy/health check, fix_history of "
            "previous attempts, current attempt number, and max_attempts"
        )
    )

    fix_summary: str = dspy.OutputField(
        desc=(
            "Brief summary of what issue(s) were found and what fix(es) were applied. "
            "Workflow: read scripts, analyze error, make targeted fixes, verify."
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
