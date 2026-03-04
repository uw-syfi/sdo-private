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

    system_prompt = dspy.OutputField(
        desc="Comprehensive system instructions for deployment"
    )


class DeployerGenerateDeployScriptSignature(dspy.Signature):
    """Generate deployment script (deploy.sh).

    Analyzes the codebase and generates an appropriate deployment script.
    """

    repo_path = dspy.InputField(desc="Path to the repository")
    code_analysis = dspy.InputField(desc="Code analysis summary")
    deployment_issues = dspy.InputField(desc="Identified deployment issues")

    deployment_script = dspy.OutputField(
        desc="Generated deploy.sh script. The start command must use "
        "'docker compose up --build -d' (not plain 'up -d') so that "
        "images are always built from the current source."
    )


class DeployerGenerateHealthCheckSignature(dspy.Signature):
    """Generate health check script (health_check.sh).

    Analyzes the codebase and generates an appropriate health check script.
    """

    repo_path = dspy.InputField(desc="Path to the repository")
    code_analysis = dspy.InputField(desc="Code analysis summary")
    deployment_issues = dspy.InputField(desc="Identified deployment issues")

    health_check_script = dspy.OutputField(desc="Generated health_check.sh script")


class DeployerGenerateScriptSignature(dspy.Signature):
    """Generate deployment and health check scripts (Legacy/Combined).

    Analyzes the codebase and generates appropriate deployment scripts.
    """

    repo_path = dspy.InputField(desc="Path to the repository")
    code_analysis = dspy.InputField(desc="Code analysis summary")
    deployment_issues = dspy.InputField(desc="Identified deployment issues")

    deployment_script = dspy.OutputField(
        desc="Generated deploy.sh script. The start command must use "
        "'docker compose up --build -d' (not plain 'up -d') so that "
        "images are always built from the current source."
    )
    health_check_script = dspy.OutputField(desc="Generated health_check.sh script")


class DeployerFixErrorSignature(dspy.Signature):
    """Generate the instruction prompt for a DevOps coding agent to diagnose
    and fix a deployment failure.

    The produced prompt will be sent directly to the coding agent. It should
    guide the agent through platform detection, error analysis, targeted fixes,
    and producing a <summary> of findings.

    Context size guardrails apply: the agent must use --tail for container logs,
    diagnose one service at a time, and stop reading once the root cause is
    identified.
    """

    repo_path = dspy.InputField(desc="Path to the repository being deployed")
    error_context = dspy.InputField(
        desc="Error messages and logs from the failed deployment attempt, "
        "including the exit code and status"
    )
    attempt = dspy.InputField(desc="Current deployment attempt number")
    max_attempts = dspy.InputField(desc="Maximum number of deployment attempts allowed")
    deploy_script = dspy.InputField(
        desc="Full path to the deploy.sh script (e.g. /path/to/repo/.sds/deploy.sh)"
    )
    health_check_script = dspy.InputField(
        desc="Full path to the health_check.sh script (e.g. /path/to/repo/.sds/health_check.sh)"
    )
    previous_summary = dspy.InputField(
        desc="Note pointing to the log file containing the previous fix attempt summary. "
        "Includes the log file path pattern .sds/logs/fix_summary_{attempt}.log. "
        "Empty string if this is the first attempt.",
        default="",
    )

    rendered_prompt = dspy.OutputField(
        desc="The full instruction prompt to send to the coding agent. Must include "
        "deployment platform detection guidance, step-by-step error analysis "
        "instructions, and a directive to produce a <summary> of findings "
        "stating the issue(s), fix(es), and deployment platform."
    )


class DeployerSummarizeSignature(dspy.Signature):
    """Generate the instruction prompt for a coding agent to summarize the
    recent output of a deployment command.

    The produced prompt will be sent directly to the coding agent. It should
    ask for a one-line summary of current activity, wrapped in
    <output_msg>...</output_msg> XML tags, with no other text or debug info.
    """

    deployment_log = dspy.InputField(
        desc="Recent output snippet from the deployment command"
    )

    rendered_prompt = dspy.OutputField(
        desc="The full instruction prompt to send to the coding agent. Must ask "
        "for a one-line summary wrapped in <output_msg>...</output_msg> XML tags, "
        "with no other text or debug info."
    )


class RLMDeployerFixErrorSignature(dspy.Signature):
    """Generate an RLM-based instruction prompt for fixing deployment errors.

    Unlike the standard DeployerFixErrorSignature which passes full context
    to a coding agent, this signature produces a prompt that leverages the
    RLM paradigm: context is stored as queryable REPL variables and the agent
    uses execute_code/recursive_call actions to filter and analyze.
    """

    repo_path = dspy.InputField(desc="Path to the repository being deployed")
    available_variables = dspy.InputField(
        desc="Summary of context variables available in the RLM REPL environment "
        "(e.g. error_log, deployment_script, dockerfile)"
    )
    error_log_size = dspy.InputField(
        desc="Size of the error log in characters (used to decide filtering strategy)"
    )
    attempt = dspy.InputField(desc="Current deployment attempt number")
    max_attempts = dspy.InputField(desc="Maximum number of deployment attempts allowed")
    has_original_script = dspy.InputField(
        desc="Whether an original deploy.sh backup is available for backtracking"
    )

    rendered_prompt = dspy.OutputField(
        desc="The task prompt for the RLM agent. Must instruct the agent to: "
        "(1) use execute_code to filter error_log and identify root cause, "
        "(2) validate file references with validate_file_refs, "
        "(3) compare against original_script if available, "
        "(4) write the fixed deploy.sh via execute_code, "
        "(5) provide a final_answer summarizing the fix."
    )


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
        desc="Analysis of codebase structure and deployment requirements"
    )
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
        desc="Comprehensive system instructions with orchestration patterns"
    )


class AgentflowUserSignature(dspy.Signature):
    """Generate agentflow script from user request.

    Converts user requirements into executable Python scripts.
    """

    user_request = dspy.InputField(desc="User's task description")
    clarification_history = dspy.InputField(
        desc="Previous clarification Q&A", default=""
    )
    work_dir = dspy.InputField(desc="Working directory")
    loop_bound = dspy.InputField(desc="Maximum loop iterations")

    script_code = dspy.OutputField(desc="Generated Python script")
    status = dspy.OutputField(desc="Status: 'ready' or 'clarify'")
    questions = dspy.OutputField(
        desc="Clarification questions if status='clarify'", default=""
    )


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
    "deployer_generate_deploy_script": DeployerGenerateDeployScriptSignature,
    "deployer_generate_health_check": DeployerGenerateHealthCheckSignature,
    "deployer_generate_script": DeployerGenerateScriptSignature,
    "deployer_fix_error": DeployerFixErrorSignature,
    "rlm_deployer_fix_error": RLMDeployerFixErrorSignature,
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
