"""Concrete AgentRunner implementations backed by cli_agent agents."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

from app_operator.cli_agent.agents.app_monitor import AppMonitor
from app_operator.cli_agent.agents.code_analyzer import CodeAnalyzerAgent
from app_operator.cli_agent.agents.deployer import DeploymentAgent


class DeployerRunner:
    def __init__(self, max_attempts: int = 2) -> None:
        self.max_attempts = max_attempts

    def run(self, repo_path: Path, agent: Any, filesystem: Any, recorder: Any) -> None:
        DeploymentAgent(
            repo_path=repo_path,
            coding_agent=agent,
            filesystem=filesystem,
            recorder=recorder,
        ).run(max_attempts=self.max_attempts)


class MonitorRunner:
    def __init__(self, interval: int = 5, max_checks: int = 1) -> None:
        self.interval = interval
        self.max_checks = max_checks

    def run(self, repo_path: Path, agent: Any, filesystem: Any, recorder: Any) -> None:
        AppMonitor(
            repo_path=repo_path,
            agent=agent,
            filesystem=filesystem,
            recorder=recorder,
        ).run(interval=self.interval, max_checks=self.max_checks)


class CodeAnalyzerRunner:
    def run(self, repo_path: Path, agent: Any, filesystem: Any, recorder: Any) -> None:
        CodeAnalyzerAgent(
            repo_path=repo_path,
            coding_agent=agent,
            filesystem=filesystem,
            recorder=recorder,
        ).run()
