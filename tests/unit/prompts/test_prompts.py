import pytest
from app_operator.prompts import get_loader, reset_loader, PromptLoader


@pytest.fixture
def loader():
    """Fixture that provides a loader and ensures cleanup after test."""
    loader = get_loader()
    yield loader
    reset_loader()  # Ensure clean state for next test


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
        platform="auto",
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


def test_fix_error_template_with_code_analysis(loader):
    """Template renders architecture reconciliation block when has_code_analysis=True."""
    rendered = loader.render(
        "deployer/fix_error.jinja2",
        repo_path="/repo",
        attempt=1,
        max_attempts=3,
        error_context="error",
        previous_summary_note="",
        deploy_script=".sds/deploy.sh",
        health_check_script=".sds/health_check.sh",
        platform="auto",
        has_code_analysis=True,
    )
    assert "code_analysis.md" in rendered
    assert "Architecture Reconciliation" in rendered
    # Spot-check key reconciliation categories are mentioned
    assert "Missing services" in rendered
    assert "Phantom services" in rendered
    assert "Database mismatches" in rendered
    assert "Port mismatches" in rendered


def test_fix_error_template_without_code_analysis(loader):
    """Template omits reconciliation block when has_code_analysis=False."""
    rendered = loader.render(
        "deployer/fix_error.jinja2",
        repo_path="/repo",
        attempt=1,
        max_attempts=3,
        error_context="error",
        previous_summary_note="",
        deploy_script=".sds/deploy.sh",
        health_check_script=".sds/health_check.sh",
        platform="auto",
        has_code_analysis=False,
    )
    assert "Architecture Reconciliation" not in rendered
    assert "Phantom services" not in rendered


def test_fix_error_template_with_deployment_issues(loader):
    """Template renders TODO instructions when has_deployment_issues=True."""
    rendered = loader.render(
        "deployer/fix_error.jinja2",
        repo_path="/repo",
        attempt=1,
        max_attempts=3,
        error_context="error",
        previous_summary_note="",
        deploy_script=".sds/deploy.sh",
        health_check_script=".sds/health_check.sh",
        platform="auto",
        has_deployment_issues=True,
    )
    assert "deployment_issues.md" in rendered
    assert "- [x]" in rendered
    assert "Confidence" in rendered


def test_fix_error_template_without_deployment_issues(loader):
    """Template omits TODO block when has_deployment_issues=False."""
    rendered = loader.render(
        "deployer/fix_error.jinja2",
        repo_path="/repo",
        attempt=1,
        max_attempts=3,
        error_context="error",
        previous_summary_note="",
        deploy_script=".sds/deploy.sh",
        health_check_script=".sds/health_check.sh",
        platform="auto",
        has_deployment_issues=False,
    )
    assert "TODO" not in rendered
    assert "- [x]" not in rendered


def test_code_analyzer_system_prompt_requires_issue_ids(loader):
    """System prompt instructs model to assign numeric IDs to issues."""
    rendered = loader.render("code_analyzer/system.jinja2")
    assert "#1" in rendered or "numeric id" in rendered.lower()


def test_code_analyzer_system_prompt_requires_confidence_with_reason(loader):
    """System prompt instructs model to include a confidence field with justification."""
    rendered = loader.render("code_analyzer/system.jinja2")
    assert "Confidence" in rendered
    assert "justif" in rendered.lower() or "reason" in rendered.lower() or "justifying" in rendered.lower()


def test_code_analyzer_system_prompt_requires_todo_section(loader):
    """System prompt instructs model to output a ## TODO checklist."""
    rendered = loader.render("code_analyzer/system.jinja2")
    assert "## TODO" in rendered
    assert "- [ ]" in rendered


def test_reset_loader():
    """Test that reset_loader clears the singleton."""
    # Get initial loader
    loader1 = get_loader()
    assert loader1 is not None

    # Get it again - should be same instance (singleton)
    loader2 = get_loader()
    assert loader1 is loader2

    # Reset the loader
    reset_loader()

    # Get a new loader - should be a different instance
    loader3 = get_loader()
    assert loader3 is not None
    assert loader3 is not loader1


def test_reset_loader_with_custom_templates_dir(tmp_path):
    """Test that reset allows switching to custom templates directory."""
    # Create a custom templates directory
    custom_templates = tmp_path / "custom_prompts"
    custom_templates.mkdir()
    (custom_templates / "test.jinja2").write_text("Custom template")

    # Reset to ensure clean state
    reset_loader()

    # Get default loader first
    default_loader = get_loader()
    default_dir = default_loader.templates_dir

    # Reset and create a custom loader
    reset_loader()

    # Note: PromptLoader can be instantiated with custom dir,
    # but get_loader() always uses default. This test verifies
    # reset functionality for potential future extensions.
    custom_loader = PromptLoader(custom_templates)
    assert custom_loader.templates_dir == custom_templates
    assert custom_loader.templates_dir != default_dir

    # Verify the custom loader can render custom templates
    rendered = custom_loader.render("test.jinja2")
    assert rendered == "Custom template"

    # Clean up
    reset_loader()


def test_reset_loader_idempotent():
    """Test that calling reset_loader multiple times is safe."""
    reset_loader()
    reset_loader()
    reset_loader()

    # Should still be able to get a loader
    loader = get_loader()
    assert loader is not None
    assert loader.templates_dir.exists()

    # Clean up
    reset_loader()
