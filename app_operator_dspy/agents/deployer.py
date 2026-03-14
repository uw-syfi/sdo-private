"""DSPy-native deployment agent."""

import os
import re
import stat

import dspy

from app_operator_dspy.constants import CLEANUP_TIMEOUT, DEPLOY_TIMEOUT, HEALTH_CHECK_TIMEOUT
from app_operator_dspy.logger import get_logger
from app_operator_dspy.signatures import (
    ConsolidateFixSummary,
    GenerateDeployScript,
    GenerateHealthCheckScript,
    RepairDeploymentError,
)
from app_operator_dspy.tools import DEPLOYER_TOOLS, write_file
from app_operator_dspy.tools.shell import ShellResult, run_shell

log = get_logger("deployer")
DEFAULT_MAX_ATTEMPTS = 5

# Truncation limits to keep LLM context manageable
_MAX_ERROR_CHARS = 5000
_MAX_FIX_HISTORY_CHARS = 20_000

_CODE_FENCE_RE = re.compile(r"^```[a-zA-Z]*\n(.*?)```\s*$", re.DOTALL)


def strip_code_fences(text: str) -> str:
    """Remove markdown code fences from LLM output."""
    m = _CODE_FENCE_RE.match(text.strip())
    return m.group(1).strip() if m else text.strip()


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... ({len(text) - limit} chars truncated)"


def _health_error_context(deploy: ShellResult, health: ShellResult) -> str:
    """Build combined deploy + health output for the repair agent when health check fails."""
    return f"Deploy output:\n{deploy.output}\n\nHealth check output:\n{health.output}"


class _FixHistory(dspy.Module):
    """Tracks fix attempt summaries and consolidates them to avoid repeating failed approaches.

    Maintains history as internal state. On first fix: stores the summary as-is (no LLM call).
    On subsequent fixes: calls ConsolidateFixSummary to merge new attempts into grouped history.
    """

    def __init__(self):
        super().__init__()
        self.consolidate = dspy.ChainOfThought(ConsolidateFixSummary)
        self._history: str = ""

    @property
    def text(self) -> str:
        """Current fix history for passing to the repair agent."""
        return self._history

    def append(self, attempt: int, fix_summary: str) -> None:
        """Append a new fix summary; consolidate when prior history exists."""
        summary = f"Attempt {attempt}: {fix_summary}"
        if not self._history:
            self._history = summary
            return
        result = self.consolidate(
            existing_summary=self._history,
            new_attempts=summary,
        )
        self._history = result.consolidated_summary

    def reset(self) -> None:
        """Clear history at the start of a new deployment run."""
        self._history = ""


class DeploymentAgent(dspy.Module):
    """Generates deployment scripts and self-heals on failure.

    Flow:
    1. Generate deploy.sh and health_check.sh once
    2. Run deploy — if fail — cleanup, repair — retry
    3. Run health check — if fail — repair — advance to next iteration
    4. Consolidate fix history to avoid repeating failed approaches
    """

    def __init__(self):
        super().__init__()
        self.gen_deploy = dspy.ReAct(GenerateDeployScript, tools=DEPLOYER_TOOLS)
        self.gen_health = dspy.ReAct(GenerateHealthCheckScript, tools=DEPLOYER_TOOLS)
        self.repair_agent = dspy.ReAct(RepairDeploymentError, tools=DEPLOYER_TOOLS)
        self._fix_history = _FixHistory()

    def forward(
        self,
        repo_path: str,
        code_analysis: str,
        deployment_issues: str,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        deploy_timeout: int = DEPLOY_TIMEOUT,
        health_check_timeout: int = HEALTH_CHECK_TIMEOUT,
    ) -> dspy.Prediction:
        sds_dir = os.path.join(repo_path, ".sds")
        deploy_path = os.path.join(sds_dir, "deploy.sh")
        health_path = os.path.join(sds_dir, "health_check.sh")

        log.info("generating deploy.sh and health_check.sh...")
        self._generate_scripts(repo_path, code_analysis, deployment_issues, deploy_path, health_path)
        log.info("scripts generated")

        self._fix_history.reset()

        for attempt in range(1, max_attempts + 1):
            deploy = self._run_deploy(repo_path, deploy_path, deploy_timeout)
            if not deploy.succeeded:
                error_output = deploy.output
            else:
                health = self._run_health_check(repo_path, health_path, health_check_timeout)
                if health.succeeded:
                    return self._success(attempt)
                error_output = _health_error_context(deploy, health)

            if attempt == max_attempts:
                return self._failure(attempt, error_output)
            self._cleanup(repo_path, deploy_path)
            self._repair(repo_path, deploy_path, health_path, error_output, attempt, max_attempts)

        return self._failure(max_attempts, "max attempts reached")

    def _generate_scripts(
        self,
        repo_path: str,
        code_analysis: str,
        issues: str,
        deploy_path: str,
        health_path: str,
    ) -> None:
        deploy_result = self.gen_deploy(
            repo_path=repo_path,
            code_analysis=code_analysis,
            deployment_issues=issues,
        )
        deploy_script = self._validate(deploy_result.deploy_script)
        if deploy_script.strip().startswith("```"):
            raise ValueError(
                "deploy_script must not contain markdown code fences; "
                "instruct the model to output raw bash only"
            )
        self._write_script(deploy_path, deploy_script)

        health_result = self.gen_health(
            repo_path=repo_path,
            code_analysis=code_analysis,
            deployment_issues=issues,
        )
        health_script = self._validate(health_result.health_check_script)
        if health_script.strip().startswith("```"):
            raise ValueError(
                "health_check_script must not contain markdown code fences; "
                "instruct the model to output raw bash only"
            )
        self._write_script(health_path, health_script)

    def _run_deploy(self, repo_path: str, deploy_path: str, deploy_timeout: int) -> ShellResult:
        """Run deploy.sh start; return structured result."""
        return run_shell(
            f"{deploy_path} start",
            cwd=repo_path,
            timeout=deploy_timeout,
        )

    def _run_health_check(
        self, repo_path: str, health_path: str, health_check_timeout: int
    ) -> ShellResult:
        """Run health_check.sh; return structured result."""
        return run_shell(
            health_path,
            cwd=repo_path,
            timeout=health_check_timeout,
        )

    def _repair(
        self,
        repo_path: str,
        deploy_path: str,
        health_path: str,
        error_output: str,
        attempt: int,
        max_attempts: int,
    ) -> None:
        """Invoke repair agent, ensure exec bits, append to fix history."""
        fix_result = self.repair_agent(
            repo_path=repo_path,
            deploy_path=deploy_path,
            health_path=health_path,
            error_output=_truncate(error_output, _MAX_ERROR_CHARS),
            fix_history=_truncate(self._fix_history.text, _MAX_FIX_HISTORY_CHARS),
            attempt=attempt,
            max_attempts=max_attempts,
        )

        if os.path.exists(deploy_path):
            os.chmod(deploy_path, os.stat(deploy_path).st_mode | stat.S_IEXEC)
        if os.path.exists(health_path):
            os.chmod(health_path, os.stat(health_path).st_mode | stat.S_IEXEC)

        log.info("fix summary: %s", fix_result.fix_summary)
        self._fix_history.append(attempt, fix_result.fix_summary)

    def _cleanup(self, repo_path: str, deploy_path: str) -> None:
        """Stop and remove containers from a failed deployment."""
        run_shell(
            f"{deploy_path} cleanup",
            cwd=repo_path,
            timeout=CLEANUP_TIMEOUT,
        )

    @staticmethod
    def _success(attempt: int) -> dspy.Prediction:
        return dspy.Prediction(success=True, attempts=attempt, error=None)

    @staticmethod
    def _failure(attempt: int, error: str) -> dspy.Prediction:
        return dspy.Prediction(success=False, attempts=attempt, error=error)

    @staticmethod
    def _validate(content: str) -> str:
        """Ensure LLM output is a non-empty script."""
        if not content or not content.strip():
            return "#!/bin/bash\necho 'Error: LLM returned empty script'\nexit 1"
        return content

    @staticmethod
    def _write_script(path: str, content: str) -> None:
        write_file(path, content)
        os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
