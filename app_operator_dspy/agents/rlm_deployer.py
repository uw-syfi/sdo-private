"""RLM-enhanced deployment agent.

Uses dspy.RLM for the repair loop to handle large error logs
programmatically instead of truncating them. Script generation
and health judging still use ReAct since they work well with
bounded context.
"""

import os
import re
import stat

import dspy
from dspy.predict.rlm import RLM

from app_operator_dspy.constants import CLEANUP_TIMEOUT, DEPLOY_TIMEOUT
from app_operator_dspy.logger import get_logger
from app_operator_dspy.signatures import (
    ConsolidateFixSummary,
    GenerateDeployScript,
    GenerateHealthCheckScript,
    JudgeHealthCheck,
    RepairDeploymentErrorRLM,
)
from app_operator_dspy.tools import DEPLOYER_TOOLS, write_file
from app_operator_dspy.tools.agent_tools import (
    read_file_tool,
    run_health_check_tool,
    run_shell_tool,
    write_file_tool,
)
from app_operator_dspy.tools.shell import ShellResult, run_shell

log = get_logger("rlm_deployer")
DEFAULT_MAX_ATTEMPTS = 5

_CODE_FENCE_RE = re.compile(r"^```[a-zA-Z]*\n(.*?)```\s*$", re.DOTALL)


def strip_code_fences(text: str) -> str:
    """Remove markdown code fences from LLM output."""
    m = _CODE_FENCE_RE.match(text.strip())
    return m.group(1).strip() if m else text.strip()


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... ({len(text) - limit} chars truncated)"


class _FixHistory(dspy.Module):
    """Tracks fix attempt summaries and consolidates them."""

    def __init__(self):
        super().__init__()
        self.consolidate = dspy.ChainOfThought(ConsolidateFixSummary)
        self._history: str = ""

    @property
    def text(self) -> str:
        return self._history

    def append(self, attempt: int, fix_summary: str) -> None:
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
        self._history = ""


class RLMDeploymentAgent(dspy.Module):
    """Deployment agent with RLM-powered repair.

    Script generation and health judging use ReAct (bounded context).
    The repair loop uses RLM so it can programmatically analyze full
    error logs instead of working with truncated 5KB snippets.
    """

    def __init__(self, sub_lm: dspy.LM | None = None):
        super().__init__()
        self.gen_deploy = dspy.ReAct(
            GenerateDeployScript,
            tools=DEPLOYER_TOOLS,
            max_iters=12,
        )
        self.gen_health = dspy.ReAct(
            GenerateHealthCheckScript,
            tools=DEPLOYER_TOOLS,
            max_iters=12,
        )
        self.health_judge = dspy.ReAct(
            JudgeHealthCheck,
            tools=DEPLOYER_TOOLS,
            max_iters=12,
        )
        self.repair_agent = RLM(
            RepairDeploymentErrorRLM,
            tools=[read_file_tool, write_file_tool, run_shell_tool, run_health_check_tool],
            max_iterations=15,
            max_llm_calls=30,
            max_output_chars=100_000,
            verbose=True,
            sub_lm=sub_lm,
        )
        self._fix_history = _FixHistory()

    def forward(
        self,
        repo_path: str,
        code_analysis: str,
        deployment_issues: str,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        deploy_timeout: int = DEPLOY_TIMEOUT,
        platform: str = "docker",
    ) -> dspy.Prediction:
        sds_dir = os.path.join(repo_path, ".sds")
        deploy_path = os.path.join(sds_dir, "deploy.sh")
        health_path = os.path.join(sds_dir, "health_check.sh")

        log.info("generating deploy.sh and health_check.sh...")
        self._generate_scripts(
            repo_path,
            code_analysis,
            deployment_issues,
            deploy_path,
            health_path,
        )
        log.info("scripts generated")

        self._fix_history.reset()

        for attempt in range(1, max_attempts + 1):
            deploy = self._run_deploy(repo_path, deploy_path, deploy_timeout)
            if not deploy.succeeded:
                error_output = deploy.output
            else:
                verdict = self._judge_health(repo_path, deploy.output, platform)
                if verdict.healthy:
                    return self._success(attempt)
                error_output = verdict.diagnosis or verdict.assessment

            if attempt == max_attempts:
                return self._failure(attempt, error_output)
            self._cleanup(repo_path, deploy_path)
            self._repair(
                repo_path,
                deploy_path,
                health_path,
                error_output,
                attempt,
                max_attempts,
            )

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
            raise ValueError("deploy_script must not contain markdown code fences")
        self._write_script(deploy_path, deploy_script)

        health_result = self.gen_health(
            repo_path=repo_path,
            code_analysis=code_analysis,
            deployment_issues=issues,
        )
        health_script = self._validate(health_result.health_check_script)
        if health_script.strip().startswith("```"):
            raise ValueError("health_check_script must not contain markdown code fences")
        self._write_script(health_path, health_script)

    def _run_deploy(
        self,
        repo_path: str,
        deploy_path: str,
        deploy_timeout: int,
    ) -> ShellResult:
        return run_shell(
            f"{deploy_path} start",
            cwd=repo_path,
            timeout=deploy_timeout,
        )

    def _judge_health(
        self,
        repo_path: str,
        deploy_output: str,
        platform: str,
    ) -> dspy.Prediction:
        result = self.health_judge(
            repo_path=repo_path,
            deploy_output=_truncate(deploy_output, 5000),
            platform=platform,
        )
        return dspy.Prediction(
            healthy=result.healthy,
            assessment=result.assessment,
            diagnosis=result.diagnosis,
            script_was_fixed=result.script_was_fixed,
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
        """Use RLM to analyze full error output and apply targeted fixes."""
        error_context = (
            f"## Repository\nrepo_path: {repo_path}\n\n"
            f"## Script Paths\n"
            f"deploy_path: {deploy_path}\n"
            f"health_path: {health_path}\n\n"
            f"## Error Output\n{error_output}\n\n"
            f"## Fix History\n{self._fix_history.text or '(first attempt)'}\n\n"
            f"## Attempt\n{attempt} of {max_attempts}\n\n"
            f"## Instructions\n"
            f"1. Use read_file to read the deploy and health scripts\n"
            f"2. Analyze the error output to identify root cause\n"
            f"3. Use write_file to apply targeted fixes\n"
            f"4. Do NOT rewrite scripts from scratch\n"
            f"5. Never use sudo, never switch platforms\n"
        )

        fix_result = self.repair_agent(error_context=error_context)

        if os.path.exists(deploy_path):
            os.chmod(deploy_path, os.stat(deploy_path).st_mode | stat.S_IEXEC)
        if os.path.exists(health_path):
            os.chmod(health_path, os.stat(health_path).st_mode | stat.S_IEXEC)

        log.info("fix summary: {}", fix_result.fix_summary)
        self._fix_history.append(attempt, fix_result.fix_summary)

    def _cleanup(self, repo_path: str, deploy_path: str) -> None:
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
        if not content or not content.strip():
            return "#!/bin/bash\necho 'Error: LLM returned empty script'\nexit 1"
        return content

    @staticmethod
    def _write_script(path: str, content: str) -> None:
        write_file(path, content)
        os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
