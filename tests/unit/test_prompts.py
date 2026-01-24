import pytest
from app_operator.prompts import get_loader


@pytest.fixture
def loader():
    return get_loader()


def test_loader_initialization(loader):
    # Verify that the loader is correctly pointing to the templates directory
    # and that the expected subdirectories exist
    assert loader.templates_dir.exists()
    assert (loader.templates_dir / "deployer").exists()


def test_deployer_system_prompt_k8s(loader):
    rendered = loader.render("deployer/system.jinja2", platform="k8s")
    # Prompt must contain Kubernetes-specific instructions
    assert "CRITICAL: Target Platform is Kubernetes" in rendered
    assert "kubectl" in rendered
    # Prompt must NOT contain Docker-specific instructions
    assert "Docker Platform Requirements" not in rendered


def test_deployer_system_prompt_docker(loader):
    rendered = loader.render("deployer/system.jinja2", platform="docker")
    # Prompt must contain Docker-specific instructions
    assert "CRITICAL: Target Platform is Docker" in rendered
    assert "docker compose" in rendered
    # Prompt must NOT contain Kubernetes-specific instructions
    assert "Kubernetes Platform Requirements" not in rendered


def test_deployer_system_prompt_auto(loader):
    rendered = loader.render("deployer/system.jinja2", platform="auto")
    # Prompt must contain auto-detection instructions for all platforms
    assert "CRITICAL: Deployment Platform Detection" in rendered
    assert "Kubernetes Platform" in rendered
    assert "Docker Compose Platform" in rendered


def test_generate_deploy_script(loader):
    rendered = loader.render(
        "deployer/generate_script.jinja2",
        system_prompt="SYSTEM_PROMPT",
        script_name="deploy.sh",
        repo_context="REPO_CONTEXT",
        target_dir="/tmp/target",
        platform="docker",
    )
    # Verify variable substitution and critical command instructions for Docker
    assert "SYSTEM_PROMPT" in rendered
    assert "deploy.sh" in rendered
    assert "REPO_CONTEXT" in rendered
    assert "/tmp/target" in rendered
    assert "Create the file at: .sds/deploy.sh" in rendered
    assert "Use `docker compose` or `docker` commands" in rendered


def test_generate_health_check_script(loader):
    rendered = loader.render(
        "deployer/generate_script.jinja2",
        system_prompt="SYSTEM_PROMPT",
        script_name="health_check.sh",
        repo_context="REPO_CONTEXT",
        target_dir="/tmp/target",
        platform="k8s",
    )
    # Verify variable substitution and k8s specific checks
    assert "health_check.sh" in rendered
    assert "Create the file at: .sds/health_check.sh" in rendered
    assert "Check pod status and readiness" in rendered


def test_fix_error_prompt(loader):
    rendered = loader.render(
        "deployer/fix_error.jinja2",
        repo_path="/repo",
        attempt=2,
        max_attempts=5,
        error_context="ERROR_CONTEXT",
        previous_summary_note="PREV_NOTE",
        deploy_script=".sds/deploy.sh",
        health_check_script=".sds/health_check.sh",
    )
    # Verify context injection (attempt count, error details, previous summary)
    assert "Current attempt: 2 of 5" in rendered
    assert "ERROR_CONTEXT" in rendered
    assert "PREV_NOTE" in rendered


def test_summarize_prompt(loader):
    rendered = loader.render(
        "deployer/summarize.jinja2", output_snippet="OUTPUT_SNIPPET"
    )
    # Verify input injection and XML formatting requirements
    assert "OUTPUT_SNIPPET" in rendered
    assert "<output_msg>" in rendered


def test_code_analyzer_system(loader):
    rendered = loader.render("code_analyzer/system.jinja2")
    # Verify agent identity and output file requirements
    assert "Code Analyzer Agent" in rendered
    assert ".sds/code_analysis.md" in rendered


def test_code_analyzer_user(loader):
    rendered = loader.render("code_analyzer/user.jinja2", repo_path="/repo")
    # Verify repository path injection
    assert "analyze the repository at /repo" in rendered


def test_monitor_analyze_health(loader):
    rendered = loader.render(
        "monitor/analyze_health.jinja2", repo_path="/repo", context="HEALTH_CONTEXT"
    )
    # Verify context injection and output format tags
    assert "Repository: /repo" in rendered
    assert "HEALTH_CONTEXT" in rendered
    assert "<exec_summary>" in rendered
