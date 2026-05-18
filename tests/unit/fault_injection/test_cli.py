import argparse
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app_operator.fault_injection import _cli as cli
from app_operator.fault_injection.models import Fault, FaultCategory, FaultSeverity


@pytest.fixture
def mock_compose_faults():
    return [
        Fault(
            fault_id="fault1",
            name="Fault 1",
            description="Description 1",
            category=FaultCategory.MISCONFIGURATION,
            severity=FaultSeverity.LOW,
        ),
        Fault(
            fault_id="fault2",
            name="Fault 2",
            description="Description 2",
            category=FaultCategory.SECURITY,
            severity=FaultSeverity.MEDIUM,
        ),
    ]


def test_cmd_list(capsys, mock_compose_faults):
    with patch("app_operator.fault_injection._cli.COMPOSE_FAULTS", mock_compose_faults):
        result = cli.cmd_list(argparse.Namespace())
        captured = capsys.readouterr()
        assert "fault1" in captured.out
        assert "Fault 1" in captured.out
        assert "fault2" in captured.out
        assert "Fault 2" in captured.out
        assert "Total: 2 faults" in captured.out
        assert result == 0


@patch("app_operator.fault_injection._cli.FaultInjectionOrchestrator")
def test_cmd_inject_success(mock_orchestrator, tmp_path):
    mock_instance = mock_orchestrator.return_value
    mock_instance.inject.return_value = [
        MagicMock(success=True, fault=MagicMock(fault_id="fault1", name="Fault 1"), target_service="service1")
    ]
    args = argparse.Namespace(
        repo_path=str(tmp_path),
        num_faults=1,
        categories=None,
        severities=None,
        seed=None,
    )
    (tmp_path / "docker-compose.yml").write_text("services:\n  service1:\n    image: a")
    result = cli.cmd_inject(args)
    assert result == 0
    mock_instance.inject.assert_called_once()


@patch("app_operator.fault_injection._cli.FaultInjectionOrchestrator")
def test_cmd_inject_no_repo(mock_orchestrator, capsys):
    args = argparse.Namespace(repo_path="/non/existent/path")
    result = cli.cmd_inject(args)
    captured = capsys.readouterr()
    assert "Error: /non/existent/path is not a directory" in captured.err
    assert result == 1


@patch("app_operator.fault_injection._cli.FaultInjectionOrchestrator")
def test_cmd_revert_success(mock_orchestrator, tmp_path):
    mock_instance = mock_orchestrator.return_value
    mock_instance.revert.return_value = True
    args = argparse.Namespace(repo_path=str(tmp_path))
    (tmp_path / "docker-compose.yml").touch()
    result = cli.cmd_revert(args)
    assert result == 0
    mock_instance.revert.assert_called_once_with(Path(str(tmp_path)))


@patch("app_operator.fault_injection._cli.FaultInjectionOrchestrator")
def test_cmd_revert_no_backup(mock_orchestrator, tmp_path, capsys):
    mock_instance = mock_orchestrator.return_value
    mock_instance.revert.return_value = False
    args = argparse.Namespace(repo_path=str(tmp_path))
    (tmp_path / "docker-compose.yml").touch()
    result = cli.cmd_revert(args)
    captured = capsys.readouterr()
    assert "No backup found to restore" in captured.err
    assert result == 1
