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
    """Analyze a repository's structure and identify deployment requirements.

    Examine the file tree, Dockerfiles, docker-compose files, and README to
    produce a deployment-oriented analysis and a list of potential issues.
    """

    repo_path: str = dspy.InputField(desc="Path to the repository")
    file_tree: str = dspy.InputField(desc="Repository file tree listing")

    analysis: str = dspy.OutputField(
        desc="Markdown analysis of the codebase structure, services, dependencies, and deployment requirements"
    )
    issues: str = dspy.OutputField(desc="Markdown list of potential deployment issues and risks")


# ---------------------------------------------------------------------------
# Deployer — Script Generation
# ---------------------------------------------------------------------------


class GenerateDeployScript(dspy.Signature):
    """Generate a deploy.sh script for a repository.

    The script will be saved to ``<repo>/.sds/deploy.sh`` and invoked as
    ``<repo>/.sds/deploy.sh start`` with working directory set to the
    repository root. It MUST NOT cd to its own directory — docker compose
    files live in the repo root.

    The ``start`` command must use ``docker compose up --build -d``
    to ensure images are always rebuilt from source.
    The ``stop`` command must use ``docker compose down``.
    Output ONLY the raw script — no markdown formatting or code fences.
    """

    repo_path: str = dspy.InputField(desc="Path to the repository")
    code_analysis: str = dspy.InputField(desc="Code analysis summary")
    deployment_issues: str = dspy.InputField(desc="Identified deployment issues")

    deploy_script: str = dspy.OutputField(
        desc="Raw bash script content (no markdown fences) with start/stop commands using docker compose"
    )


class GenerateHealthCheckScript(dspy.Signature):
    """Generate a health_check.sh script for a Docker Compose deployed application.

    The script will be saved to ``<repo>/.sds/health_check.sh`` and invoked
    with working directory set to the repository root.

    Services are deployed with ``docker compose``, so use ``docker compose ps``,
    ``curl --fail``, or ``nc`` to verify containers are running and endpoints respond.
    Do NOT use kubectl or Helm — this is a local Docker Compose deployment.
    Use ``curl --fail`` to check HTTP status codes — do NOT grep for specific
    HTML content (pages may use ``<!doctype html>`` or other markup).
    Exit with code 0 on success and non-zero on failure.
    Output ONLY the raw script — no markdown formatting or code fences.
    """

    repo_path: str = dspy.InputField(desc="Path to the repository")
    code_analysis: str = dspy.InputField(desc="Code analysis summary")
    deployment_issues: str = dspy.InputField(desc="Identified deployment issues")

    health_check_script: str = dspy.OutputField(
        desc="Raw bash script (no markdown fences) that validates all Docker Compose services using curl/docker"
    )


# ---------------------------------------------------------------------------
# Deployer — Fix Errors
# ---------------------------------------------------------------------------


class DiagnoseDeploymentFailure(dspy.Signature):
    """Diagnose a deployment failure and produce a fix plan.

    Given the error output from a failed deployment or health check,
    determine the root cause and describe the fix to apply.
    """

    repo_path: str = dspy.InputField(desc="Path to the repository")
    error_output: str = dspy.InputField(desc="Combined stdout/stderr from the failed deploy and/or health check")
    attempt: str = dspy.InputField(desc="Current attempt number")
    max_attempts: str = dspy.InputField(desc="Maximum allowed attempts")

    diagnosis: str = dspy.OutputField(desc="Root cause analysis of the failure")
    fix_plan: str = dspy.OutputField(desc="Step-by-step plan describing which files to modify and how")


# ---------------------------------------------------------------------------
# Monitor
# ---------------------------------------------------------------------------


class AnalyzeHealthCheck(dspy.Signature):
    """Analyze health check results and provide an operational summary.

    Determine whether the application is healthy, identify any degradation
    or failures, and suggest remediation if needed.
    """

    health_output: str = dspy.InputField(desc="Output from the health check script including exit code")
    check_number: str = dspy.InputField(desc="Current monitoring cycle number")

    status: str = dspy.OutputField(desc="One of: healthy, degraded, unhealthy")
    summary: str = dspy.OutputField(desc="Brief operational summary of the health check results")
    remediation: str = dspy.OutputField(desc="Suggested remediation steps if unhealthy, empty string if healthy")
