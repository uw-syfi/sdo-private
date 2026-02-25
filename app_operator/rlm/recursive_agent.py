"""Recursive Deployment Agent using the RLM (Recursive Language Model) paradigm.

The agent exposes deployment artifacts as Python variables in a REPL so the
LLM can query/filter them with execute_code actions before returning a final
answer.  This reduces token usage ~50-75% on large logs.
"""

import os
import re
from typing import List, Optional

import litellm

from app_operator.logger import logger
from app_operator.rlm.environment import RLMContext

_SYSTEM_PROMPT = """\
You are an RLM deployment assistant. Use execute_code actions to inspect
deployment artifacts, then provide a final answer.

For each response, use exactly ONE of these formats:

  ACTION: execute_code
  DESCRIPTION: <brief description>
  CODE:
  <python code; set result = <your output>>

  or:

  ACTION: final_answer
  ANSWER: <your fix or analysis>

Available variables in CODE: error_log, deployment_script,
health_check_output, dockerfile, docker_compose, readme, analysis_report,
repo_path. Always assign to `result` to capture output.
"""


class RecursiveDeploymentAgent:
    """LLM agent that uses a REPL loop to inspect deployment artifacts."""

    def __init__(
        self,
        trajectory=None,
        max_recursion_depth: int = 5,
        llm_provider: str = "vertex_ai/gemini-2.0-flash",
        vertex_location: Optional[str] = None,
    ):
        self.trajectory = trajectory
        self.max_recursion_depth = max_recursion_depth
        self.llm_provider = llm_provider
        self.vertex_location = vertex_location
        self._system_prompt = _SYSTEM_PROMPT

    def run_task(
        self,
        task: str,
        context: RLMContext,
        repo_path: str,
    ) -> str:
        """Run a task using the RLM loop.

        Does NOT call trajectory.start_phase or trajectory.end_phase — that is
        the caller's responsibility.

        Args:
            task: Natural-language task description.
            context: Deployment artifacts available to the LLM.
            repo_path: Path to the repository (available as a variable).

        Returns:
            Final answer string from the LLM.
        """
        namespace = {
            "error_log": context.error_log,
            "deployment_script": context.deployment_script,
            "health_check_output": context.health_check_output,
            "dockerfile": context.dockerfile,
            "docker_compose": context.docker_compose,
            "readme": context.readme,
            "analysis_report": context.analysis_report,
            "repo_path": repo_path,
        }

        prompt = self._build_initial_prompt(task, context)

        for _ in range(self.max_recursion_depth):
            response = self._call_llm(prompt)
            action, content = self._parse_response(response)

            if action == "final_answer":
                return content

            if action == "execute_code":
                exec_result = self._execute_code(content, namespace)
                prompt = f"{prompt}\n\nAssistant:\n{response}\n\nCode result:\n{exec_result}\n\nUser: Continue."
                continue

            # Unknown action — return as-is
            return response

        # Max iterations reached — return last prompt as fallback
        return prompt

    def fix_deployment_error(
        self,
        error_log: str,
        deployment_script: str,
        previous_attempts: List[str],
        repo_path,
    ) -> str:
        """Fix a deployment error using the RLM loop.

        Args:
            error_log: Deployment error log content.
            deployment_script: Current deploy.sh content.
            previous_attempts: List of previous fix attempts (for context).
            repo_path: Path to the repository.

        Returns:
            Fix instructions from the LLM.
        """
        context = RLMContext(
            error_log=error_log,
            deployment_script=deployment_script,
        )
        attempts_str = (
            "\n\nPrevious fix attempts:\n" + "\n---\n".join(previous_attempts)
            if previous_attempts
            else ""
        )
        task = f"Fix the following deployment error:\n{error_log}{attempts_str}"
        return self.run_task(task=task, context=context, repo_path=str(repo_path))

    def _call_llm(self, prompt: str) -> str:
        """Call the LLM and return the response text.

        Args:
            prompt: User message to send.

        Returns:
            LLM response text, or a fallback FINAL_ANSWER on error.
        """
        kwargs = {
            "model": self.llm_provider,
            "messages": [
                {"role": "system", "content": self._system_prompt},
                {"role": "user", "content": prompt},
            ],
            "cache": {"no-cache": True},
        }

        location = self.vertex_location or os.environ.get("VERTEX_LOCATION")
        if location:
            kwargs["vertex_location"] = location

        try:
            response = litellm.completion(**kwargs)
            return response.choices[0].message.content or ""
        except Exception as exc:
            logger.error(f"[RLM] LLM call failed: {exc}")
            return f"ACTION: final_answer\nANSWER: LLM call failed: {exc}"

    def _parse_response(self, response: str):
        """Parse an LLM response into (action, content).

        Returns:
            Tuple of (action_str, content_str).
        """
        if "ACTION: final_answer" in response:
            match = re.search(r"ANSWER:\s*(.+)", response, re.DOTALL)
            content = match.group(1).strip() if match else response
            return "final_answer", content

        if "ACTION: execute_code" in response:
            match = re.search(r"CODE:\n(.+)", response, re.DOTALL)
            code = match.group(1).strip() if match else ""
            return "execute_code", code

        return "unknown", response

    def _execute_code(self, code: str, namespace: dict) -> str:
        """Execute *code* in *namespace* and return the `result` variable.

        Args:
            code: Python code to execute.
            namespace: Variable namespace (modified in-place).

        Returns:
            String representation of `result`, or an error message.
        """
        try:
            exec(code, namespace)  # noqa: S102
            return str(namespace.get("result", ""))
        except Exception as exc:
            return f"Error: {exc}"

    def _build_initial_prompt(self, task: str, context: RLMContext) -> str:
        """Build the first user prompt from task and context summary."""
        parts = [f"Task: {task}"]
        if context.error_log:
            parts.append(f"\nError log:\n{context.error_log}")
        if context.deployment_script:
            parts.append(f"\nDeployment script:\n{context.deployment_script}")
        if context.docker_compose:
            parts.append(f"\nDocker Compose:\n{context.docker_compose}")
        if context.dockerfile:
            parts.append(f"\nDockerfile:\n{context.dockerfile}")
        if context.readme:
            parts.append(f"\nREADME:\n{context.readme}")
        if context.analysis_report:
            parts.append(f"\nCode Analysis:\n{context.analysis_report}")
        return "\n".join(parts)
