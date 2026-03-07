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

    The script must support ``start`` and ``stop`` commands.
    The ``start`` command must use ``docker compose up --build -d``
    to ensure images are always rebuilt from source.
    """

    repo_path: str = dspy.InputField(desc="Path to the repository")
    code_analysis: str = dspy.InputField(desc="Code analysis summary")
    deployment_issues: str = dspy.InputField(desc="Identified deployment issues")

    deploy_script: str = dspy.OutputField(desc="Complete deploy.sh bash script content with start/stop commands")


class GenerateHealthCheckScript(dspy.Signature):
    """Generate a health_check.sh script for a deployed application.

    The script should verify that all services are running and responding
    correctly, exiting with code 0 on success and non-zero on failure.
    """

    repo_path: str = dspy.InputField(desc="Path to the repository")
    code_analysis: str = dspy.InputField(desc="Code analysis summary")
    deployment_issues: str = dspy.InputField(desc="Identified deployment issues")

    health_check_script: str = dspy.OutputField(desc="Complete health_check.sh bash script that validates all services")


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
