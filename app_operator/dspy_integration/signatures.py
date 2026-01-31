"""DSPy signature definitions for SDS prompts.

Signatures define the input/output interface for each prompt template,
enabling DSPy to optimize them.
"""

import dspy


class DeployerSystemSignature(dspy.Signature):
    """System prompt for deployment agent.

    Provides overall context and instructions for the deployment agent.
    """

    repo_path = dspy.InputField(desc="Path to the repository being deployed")
    agent_name = dspy.InputField(desc="Name of the deployment agent")

    system_prompt = dspy.OutputField(desc="Comprehensive system instructions for deployment")


class DeployerGenerateScriptSignature(dspy.Signature):
    """Generate deployment and health check scripts.

    Analyzes the codebase and generates appropriate deployment scripts.
    """

    repo_path = dspy.InputField(desc="Path to the repository")
    code_analysis = dspy.InputField(desc="Code analysis summary")
    deployment_issues = dspy.InputField(desc="Identified deployment issues")

    deployment_script = dspy.OutputField(desc="Generated deploy.sh script")
    health_check_script = dspy.OutputField(desc="Generated health_check.sh script")


class DeployerFixErrorSignature(dspy.Signature):
    """Fix deployment errors.

    Analyzes deployment failures and fixes the deployment scripts.
    """

    repo_path = dspy.InputField(desc="Path to the repository")
    error_context = dspy.InputField(desc="Error messages and logs from failed deployment")
    attempt = dspy.InputField(desc="Current attempt number")
    max_attempts = dspy.InputField(desc="Maximum number of attempts")
    deploy_script = dspy.InputField(desc="Path to deploy.sh")
    health_check_script = dspy.InputField(desc="Path to health_check.sh")
    previous_summary = dspy.InputField(desc="Summary of previous fix attempt", default="")

    fix_summary = dspy.OutputField(desc="Summary of issues found and fixes applied")


class DeployerSummarizeSignature(dspy.Signature):
    """Summarize deployment results.

    Creates a concise summary of the deployment outcome.
    """

    deployment_log = dspy.InputField(desc="Full deployment log output")
    health_check_result = dspy.InputField(desc="Health check results")
    success = dspy.InputField(desc="Whether deployment succeeded")

    summary = dspy.OutputField(desc="Concise deployment summary")


class CodeAnalyzerSystemSignature(dspy.Signature):
    """System prompt for code analyzer agent.

    Provides instructions for analyzing codebases before deployment.
    """

    repo_path = dspy.InputField(desc="Path to the repository")
    agent_name = dspy.InputField(desc="Name of the code analyzer agent")

    system_prompt = dspy.OutputField(desc="System instructions for code analysis")


class CodeAnalyzerUserSignature(dspy.Signature):
    """Analyze codebase for deployment.

    Analyzes repository structure and identifies deployment approach.
    """

    repo_path = dspy.InputField(desc="Path to the repository")
    file_tree = dspy.InputField(desc="Repository file tree structure")

    code_analysis = dspy.OutputField(
        desc="Analysis of codebase structure and deployment requirements")
    deployment_issues = dspy.OutputField(desc="Potential deployment issues identified")


class MonitorAnalyzeHealthSignature(dspy.Signature):
    """Analyze application health.

    Analyzes health check results and determines if application is healthy.
    """

    health_check_output = dspy.InputField(desc="Output from health check script")
    exit_code = dspy.InputField(desc="Exit code from health check")
    iteration = dspy.InputField(desc="Current monitoring iteration")

    health_status = dspy.OutputField(desc="Overall health status and analysis")
    is_healthy = dspy.OutputField(desc="Boolean indicating if application is healthy")


class AgentflowSystemSignature(dspy.Signature):
    """System prompt for agentflow script generation.

    Provides instructions for autonomous script generation with orchestration.
    """

    work_dir = dspy.InputField(desc="Working directory for script execution")
    loop_bound = dspy.InputField(desc="Maximum iterations for loops")

    system_prompt = dspy.OutputField(
        desc="Comprehensive system instructions with orchestration patterns")


class AgentflowUserSignature(dspy.Signature):
    """Generate agentflow script from user request.

    Converts user requirements into executable Python scripts.
    """

    user_request = dspy.InputField(desc="User's task description")
    clarification_history = dspy.InputField(desc="Previous clarification Q&A", default="")
    work_dir = dspy.InputField(desc="Working directory")
    loop_bound = dspy.InputField(desc="Maximum loop iterations")

    script_code = dspy.OutputField(desc="Generated Python script")
    status = dspy.OutputField(desc="Status: 'ready' or 'clarify'")
    questions = dspy.OutputField(desc="Clarification questions if status='clarify'", default="")


class AgentflowRepairSignature(dspy.Signature):
    """Repair malformed agentflow responses.

    Fixes JSON parsing errors in agentflow responses.
    """

    original_response = dspy.InputField(desc="Original malformed response")
    error_message = dspy.InputField(desc="Error message from JSON parsing")

    repaired_response = dspy.OutputField(desc="Corrected JSON-parseable response")


# Signature registry for easy lookup
SIGNATURES = {
    "deployer_system": DeployerSystemSignature,
    "deployer_generate_script": DeployerGenerateScriptSignature,
    "deployer_fix_error": DeployerFixErrorSignature,
    "deployer_summarize": DeployerSummarizeSignature,
    "code_analyzer_system": CodeAnalyzerSystemSignature,
    "code_analyzer_user": CodeAnalyzerUserSignature,
    "monitor_analyze_health": MonitorAnalyzeHealthSignature,
    "agentflow_system": AgentflowSystemSignature,
    "agentflow_user": AgentflowUserSignature,
    "agentflow_repair": AgentflowRepairSignature,
}


def get_signature(prompt_name: str) -> type[dspy.Signature]:
    """Get DSPy signature for a prompt.

    Args:
        prompt_name: Name of the prompt (e.g., 'deployer_fix_error')

    Returns:
        DSPy Signature class

    Raises:
        KeyError: If prompt_name not found
    """
    if prompt_name not in SIGNATURES:
        raise KeyError(
            f"No signature found for '{prompt_name}'. "
            f"Available prompts: {', '.join(sorted(SIGNATURES.keys()))}"
        )
    return SIGNATURES[prompt_name]
