from pathlib import Path

from app_operator.prompts.deployer import prepare_error_context


def test_prepare_error_context_with_logs():
    deploy_result = {"exit_code": 1, "success": False}
    health_result = {"exit_code": 0, "success": True}
    log_file = Path("/tmp/deploy.log")
    health_log = Path("/tmp/health.log")

    context = prepare_error_context(deploy_result, health_result, log_file, health_log)
    assert str(log_file) in context
    assert str(health_log) in context


def test_prepare_error_context_without_health_log():
    deploy_result = {"exit_code": 1, "success": False}
    health_result = None
    log_file = Path("/tmp/deploy.log")
    health_log = None

    context = prepare_error_context(deploy_result, health_result, log_file, health_log)
    assert str(log_file) in context
    assert "Health check outputs available at" not in context
