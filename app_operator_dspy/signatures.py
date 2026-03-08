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
    """You are a Code Analyzer Agent specialized in understanding microservice
    applications. Analyze the repository and produce a comprehensive deployment
    analysis and a list of potential issues.

    Analysis process:
    1. Identify project type, layout, and all services
    2. For each service: technology stack, database requirements, environment
       variables, exposed ports, health endpoints, inter-service dependencies
    3. Analyze deployment config (Docker Compose, Kubernetes, Helm)
    4. Construct service dependency graph and recommended startup order
    5. Detect issues: config mismatches, port conflicts, missing components,
       hardcoded connection strings, missing health checks

    The analysis output MUST include:
    - Executive summary (2-3 paragraphs)
    - Services inventory table (Name, Technology, Port, Database, Dependencies)
    - Detailed per-service analysis (location, port, health endpoint, env vars)
    - Database requirements table
    - Service dependency graph (ASCII)
    - Recommended startup order
    - Environment variables summary

    The issues output MUST use this format:
    - TODO section: ``- [ ] #N — title`` checklist
    - Per-issue blocks with: Severity (Critical/High/Medium/Low),
      Confidence (High/Medium/Low with justification), Category,
      Affected service/file, Description, Evidence, Impact, Recommended Fix
    """

    repo_path: str = dspy.InputField(desc="Path to the repository")
    file_tree: str = dspy.InputField(
        desc="Repository file tree listing followed by raw file contents"
    )

    analysis: str = dspy.OutputField(
        desc="Structured markdown analysis with services inventory table, "
        "per-service details (port, health endpoint, database, env vars), "
        "dependency graph, and recommended startup order"
    )
    issues: str = dspy.OutputField(
        desc="Markdown deployment issues document with TODO checklist and "
        "per-issue blocks (Severity, Confidence, Category, Evidence, Fix)"
    )


# ---------------------------------------------------------------------------
# Deployer — Script Generation
# ---------------------------------------------------------------------------


class GenerateDeployScript(dspy.Signature):
    """Generate a deploy.sh bash script for a repository.

    The script is saved to ``<repo>/.sds/deploy.sh`` and invoked as
    ``deploy.sh <command>`` with working directory set to the repo root.

    CLI interface:
    - Invocation: ``deploy.sh <command>`` — parse $1 in a case statement.
      Do NOT use flags or getopts.
    - Required commands: start, stop, restart, status, logs, build, cleanup
    - Exit codes: 0 on success, non-zero on failure

    CRITICAL Docker Compose rules:
    - Set ``PROJECT_NAME=$(basename "$APP_DIR")`` near the top where APP_DIR
      is the working directory. Pass ``--project-name "$PROJECT_NAME"`` to
      EVERY ``docker compose`` command.
    - The ``start`` command MUST use ``docker compose --project-name
      "$PROJECT_NAME" up --build -d``. Without --build, Docker reuses
      cached images and never compiles current source.
    - NEVER use plain ``docker run`` — always use docker compose.
    - NEVER use the old ``docker-compose`` (hyphen form) — use
      ``docker compose`` (v2 plugin, space-separated).
    - NEVER use ``sudo``.
    - Create one named Docker network shared by all services.
    - If no docker-compose file exists, generate one from the Dockerfiles
      and application structure before creating deploy.sh.

    Architecture reconciliation:
    - Use the code analysis to verify services, ports, dependencies, and
      startup order match the deployment config
    - Check for missing services, phantom services, database mismatches,
      port mismatches, and startup order violations

    Output ONLY the raw script — no markdown formatting or code fences.
    """

    repo_path: str = dspy.InputField(desc="Path to the repository")
    code_analysis: str = dspy.InputField(
        desc="Structured code analysis with services inventory, ports, "
        "dependencies, and raw file contents including docker-compose"
    )
    deployment_issues: str = dspy.InputField(
        desc="Known deployment issues with severity and confidence"
    )

    deploy_script: str = dspy.OutputField(
        desc="Raw bash script (no markdown fences) with case-statement CLI, "
        "--project-name on every docker compose command, --build on start"
    )


class GenerateHealthCheckScript(dspy.Signature):
    """Generate a health_check.sh bash script for a deployed application.

    The script is saved to ``<repo>/.sds/health_check.sh`` and invoked
    with no arguments. Working directory is set to the repo root.

    CLI interface:
    - Invocation: ``health_check.sh`` (no arguments)
    - Runs readiness checks, prints summary report with health score
    - Exit codes: 0 if all checks pass, non-zero if any critical check fails

    CRITICAL Docker Compose rules:
    - Set ``PROJECT_NAME=$(basename "$APP_DIR")`` near the top where APP_DIR
      is the working directory. Pass ``--project-name "$PROJECT_NAME"`` to
      EVERY ``docker compose`` command.
    - Use ``docker compose --project-name "$PROJECT_NAME" ps`` to check
      container status. NEVER use plain ``docker ps``.
    - NEVER use the old ``docker-compose`` (hyphen form).
    - NEVER use kubectl or Helm — this is a local Docker Compose deployment.
    - NEVER use ``sudo``.

    Health check requirements:
    - Check container status via docker compose ps
    - Check HTTP endpoints ONLY on ports explicitly exposed in the
      docker-compose file (host-mapped ports). Use ``curl -sf -o /dev/null``
      to check HTTP status — do NOT grep response bodies or assume specific
      API paths like /eureka/apps or /actuator/health unless the code
      analysis confirms they exist.
    - Track total checks, passed, failed, warnings
    - Include a summary report with health score
    - Use retries with backoff for services that need startup time
    - Do NOT hardcode service counts — detect them dynamically from
      ``docker compose ps``

    Output ONLY the raw script — no markdown formatting or code fences.
    """

    repo_path: str = dspy.InputField(desc="Path to the repository")
    code_analysis: str = dspy.InputField(
        desc="Structured code analysis with services inventory, ports, "
        "health endpoints, and raw file contents including docker-compose"
    )
    deployment_issues: str = dspy.InputField(
        desc="Known deployment issues with severity and confidence"
    )

    health_check_script: str = dspy.OutputField(
        desc="Raw bash script (no markdown fences) that validates all "
        "services via docker compose ps and curl on exposed ports only"
    )


# ---------------------------------------------------------------------------
# Deployer — Fix Errors
# ---------------------------------------------------------------------------


class FixDeploymentError(dspy.Signature):
    """You are an expert DevOps engineer debugging deployment issues.

    Analyze the error output and fix the deployment and/or health check
    scripts. You receive the current scripts and must output corrected
    versions.

    CRITICAL platform awareness:
    - Read the deploy script to identify the platform (Docker Compose or K8s)
    - ALL fixes MUST use commands appropriate for the detected platform
    - NEVER switch between Docker Compose and Kubernetes
    - NEVER drop or change --project-name from docker compose commands
    - NEVER use sudo
    - NEVER expose new host ports to fix conflicts — use Docker networking

    Analysis methodology:
    1. Form a hypothesis about the root cause
    2. Verify against the error output and script content
    3. If not validated, form a new hypothesis
    4. Make targeted fixes — do not rewrite scripts from scratch

    Common error patterns:
    - Restarting/CrashLoopBackOff → entrypoint failing, check logs
    - address already in use → port conflict, remove host port mapping
    - connection refused → startup ordering race, add depends_on
    - DNS failure / no such host → wrong hostname or missing network
    - Health check wrong endpoint → check only ports in docker-compose
    - Health check grep mismatch → use curl status codes, not body parsing
    """

    deploy_script: str = dspy.InputField(
        desc="Current content of deploy.sh"
    )
    health_check_script: str = dspy.InputField(
        desc="Current content of health_check.sh"
    )
    error_output: str = dspy.InputField(
        desc="Truncated stdout/stderr from the failed deploy or health check"
    )
    fix_history: str = dspy.InputField(
        desc="History of previous fix attempts and their outcomes, "
        "grouped by failure pattern. Empty string on first attempt."
    )
    attempt: str = dspy.InputField(desc="Current attempt number")
    max_attempts: str = dspy.InputField(desc="Maximum allowed attempts")

    fixed_deploy_script: str = dspy.OutputField(
        desc="Corrected deploy.sh content (full script, no markdown fences). "
        "Return the original unchanged if the deploy script is not at fault."
    )
    fixed_health_check_script: str = dspy.OutputField(
        desc="Corrected health_check.sh content (full script, no markdown "
        "fences). Return the original unchanged if the health check is fine."
    )
    fix_summary: str = dspy.OutputField(
        desc="Brief summary: what issue(s) were found and what fix(es) applied"
    )


class ConsolidateFixSummary(dspy.Signature):
    """Consolidate deployment fix attempt summaries into a structured history.

    Group attempts by the underlying failure pattern they address.
    This summary helps the fix agent understand what has been tried
    and avoid repeating the same failed approaches.

    CRITICAL rules:
    - NEVER remove past failure patterns even if they appear resolved
    - NEVER delete or rewrite existing attempts
    - If a pattern already exists, add the new attempt under it
    - Always preserve prior content; only add or merge new information

    Output format:
    # Deployment Fix History

    ## Failure Pattern: [Name]
    *Description: [Brief description]*
    * **Attempt N**: [What was tried]. **Result**: [outcome].

    ## Chronological Timeline
    * **Attempt 1**: [summary]
    * **Attempt 2**: [summary]
    """

    existing_summary: str = dspy.InputField(
        desc="Current consolidated fix summary, or empty string if first"
    )
    new_attempts: str = dspy.InputField(
        desc="New fix attempt summaries to incorporate"
    )

    consolidated_summary: str = dspy.OutputField(
        desc="Updated fix history with new attempts merged in, grouped "
        "by failure pattern, with chronological timeline"
    )


# ---------------------------------------------------------------------------
# Monitor
# ---------------------------------------------------------------------------


class AnalyzeHealthCheck(dspy.Signature):
    """You are an expert SRE analyzing application health metrics.

    Analyze health check results and provide actionable insights.
    Your suggestions are informational only and will NOT be automatically
    applied.

    Analysis framework:
    1. Overall health status — is the system healthy? Any degraded services?
    2. Issues found — critical (immediate), warnings (soon), info (nice-to-have)
    3. Performance — response times, resource utilization, bottlenecks
    4. Recommendations — short-term fixes, long-term improvements

    Prioritize by severity. Distinguish temporary blips from real issues.
    Be specific about what to check or fix. Consider trends across checks.
    """

    health_output: str = dspy.InputField(
        desc="Output from the health check script including exit code"
    )
    check_number: str = dspy.InputField(
        desc="Current monitoring cycle number"
    )

    status: str = dspy.OutputField(
        desc="One of: healthy, degraded, unhealthy"
    )
    summary: str = dspy.OutputField(
        desc="Executive summary of health status (2 lines max)"
    )
    remediation: str = dspy.OutputField(
        desc="Prioritized remediation steps if unhealthy, empty if healthy"
    )
