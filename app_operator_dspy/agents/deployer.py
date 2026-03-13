"""DSPy-native deployment agent."""

import os
import re
import stat

import dspy

from app_operator_dspy.signatures import (
    ConsolidateFixSummary,
    FixDeploymentError,
    GenerateDeployScript,
    GenerateHealthCheckScript,
)
from app_operator_dspy.tools import DEPLOYER_TOOLS
from app_operator_dspy.tools.filesystem import write_file
from app_operator_dspy.tools.shell import run_shell

DEFAULT_MAX_ATTEMPTS = 5
DEFAULT_DEPLOY_TIMEOUT = 300
DEFAULT_HEALTH_CHECK_TIMEOUT = 300

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


class DeploymentAgent(dspy.Module):
    """Generates deployment scripts and self-heals on failure.

    Flow:
    1. Generate deploy.sh and health_check.sh once
    2. Run deploy — if fail — cleanup, fix scripts — retry
    3. Run health check — if fail — fix scripts — re-run health check
       WITHOUT re-deploying (post-fix recheck) — if still fails, full retry
    4. Consolidate fix history to avoid repeating failed approaches
    """

    def __init__(self):
        super().__init__()
        self.gen_deploy = dspy.ReAct(GenerateDeployScript, tools=DEPLOYER_TOOLS)
        self.gen_health = dspy.ReAct(GenerateHealthCheckScript, tools=DEPLOYER_TOOLS)
        self.fix_error = dspy.ReAct(FixDeploymentError, tools=DEPLOYER_TOOLS)
        self.consolidate = dspy.ChainOfThought(ConsolidateFixSummary)

    def forward(
        self,
        repo_path: str,
        code_analysis: str,
        deployment_issues: str,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        deploy_timeout: int = DEFAULT_DEPLOY_TIMEOUT,
        health_check_timeout: int = DEFAULT_HEALTH_CHECK_TIMEOUT,
    ) -> dspy.Prediction:
        sds_dir = os.path.join(repo_path, ".sds")
        deploy_path = os.path.join(sds_dir, "deploy.sh")
        health_path = os.path.join(sds_dir, "health_check.sh")

        # Step 1: Generate scripts once
        print("[deployer] generating deploy.sh and health_check.sh...")
        self._generate_scripts(
            repo_path,
            code_analysis,
            deployment_issues,
            deploy_path,
            health_path,
        )
        print("[deployer] scripts generated")

        fix_history = ""
        fix_summaries: list[str] = []

        for attempt in range(1, max_attempts + 1):
            # Step 2: Run deploy
            print(f"[deployer] attempt {attempt}/{max_attempts} — running deploy.sh start...")
            deploy_output = run_shell(
                f"{deploy_path} start",
                cwd=repo_path,
                timeout=deploy_timeout,
            )
            if not deploy_output.startswith("Exit code: 0"):
                print(f"[deployer] attempt {attempt} — deploy failed")
                if attempt == max_attempts:
                    return dspy.Prediction(
                        success=False,
                        attempts=attempt,
                        error=deploy_output,
                    )
                # Cleanup failed deployment before retry
                print(f"[deployer] attempt {attempt} — cleaning up, then fixing...")
                self._cleanup(deploy_path, repo_path)
                fix_history, fix_summaries = self._fix_and_track(
                    repo_path,
                    deploy_path,
                    health_path,
                    deploy_output,
                    fix_history,
                    fix_summaries,
                    attempt,
                    max_attempts,
                )
                continue

            # Step 3: Run health check
            print(f"[deployer] attempt {attempt} — deploy succeeded, running health check...")
            health_output = run_shell(
                health_path,
                cwd=repo_path,
                timeout=health_check_timeout,
            )
            if health_output.startswith("Exit code: 0"):
                print(f"[deployer] attempt {attempt} — health check passed")
                return dspy.Prediction(success=True, attempts=attempt)

            # Step 4: Post-fix recheck — fix health check, re-run WITHOUT
            # re-deploying. If the issue was just in the health check script
            # (wrong endpoints, wrong grep pattern), we avoid restarting
            # all containers.
            print(f"[deployer] attempt {attempt} — health check failed, fixing scripts...")
            error_context = f"Deploy output:\n{deploy_output}\n\nHealth check output:\n{health_output}"
            fix_history, fix_summaries = self._fix_and_track(
                repo_path,
                deploy_path,
                health_path,
                error_context,
                fix_history,
                fix_summaries,
                attempt,
                max_attempts,
            )

            # Re-run health check with the fixed script
            print(f"[deployer] attempt {attempt} — post-fix recheck...")
            recheck_output = run_shell(
                health_path,
                cwd=repo_path,
                timeout=health_check_timeout,
            )
            if recheck_output.startswith("Exit code: 0"):
                print(f"[deployer] attempt {attempt} — post-fix recheck passed")
                return dspy.Prediction(success=True, attempts=attempt)

            if attempt == max_attempts:
                print(f"[deployer] attempt {attempt} — post-fix recheck failed, no attempts left")
                return dspy.Prediction(
                    success=False,
                    attempts=attempt,
                    error=recheck_output,
                )

            # Full retry needed — cleanup before next attempt
            print(f"[deployer] attempt {attempt} — full retry needed, cleaning up...")
            self._cleanup(deploy_path, repo_path)

        return dspy.Prediction(
            success=False,
            attempts=max_attempts,
            error="max attempts reached",
        )

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
        health_result = self.gen_health(
            repo_path=repo_path,
            code_analysis=code_analysis,
            deployment_issues=issues,
        )
        self._write_script(deploy_path, strip_code_fences(self._validate(deploy_result.deploy_script)))
        self._write_script(health_path, strip_code_fences(self._validate(health_result.health_check_script)))

    def _fix_and_track(
        self,
        repo_path: str,
        deploy_path: str,
        health_path: str,
        error_output: str,
        fix_history: str,
        fix_summaries: list[str],
        attempt: int,
        max_attempts: int,
    ) -> tuple[str, list[str]]:
        """Fix scripts based on error output and update fix history."""
        fix_result = self.fix_error(
            repo_path=repo_path,
            deploy_path=deploy_path,
            health_path=health_path,
            error_output=_truncate(error_output, _MAX_ERROR_CHARS),
            fix_history=_truncate(fix_history, _MAX_FIX_HISTORY_CHARS),
            attempt=str(attempt),
            max_attempts=str(max_attempts),
        )

        # Ensure scripts remain executable (write_file does not set execute bit)
        if os.path.exists(deploy_path):
            os.chmod(deploy_path, os.stat(deploy_path).st_mode | stat.S_IEXEC)
        if os.path.exists(health_path):
            os.chmod(health_path, os.stat(health_path).st_mode | stat.S_IEXEC)

        # Track fix summary
        summary = f"Attempt {attempt}: {fix_result.fix_summary}"
        print(f"[deployer] fix summary: {fix_result.fix_summary}")
        fix_summaries = [*fix_summaries, summary]

        # Only consolidate when there's prior history worth grouping —
        # skip on first fix (nothing to consolidate) to save an LLM call
        if fix_history:
            fix_history = self._consolidate_history(fix_history, fix_summaries)
            return fix_history, []

        return "\n".join(fix_summaries), []

    def _consolidate_history(
        self,
        existing: str,
        new_summaries: list[str],
    ) -> str:
        if not new_summaries:
            return existing
        result = self.consolidate(
            existing_summary=existing,
            new_attempts="\n".join(new_summaries),
        )
        return result.consolidated_summary

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

    @staticmethod
    def _cleanup(deploy_path: str, repo_path: str) -> None:
        """Stop and remove containers from a failed deployment."""
        run_shell(f"{deploy_path} cleanup", cwd=repo_path, timeout=60)
