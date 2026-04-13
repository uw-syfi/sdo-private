"""Recursive Deployment Agent using the RLM (Recursive Language Model) paradigm.

The agent exposes deployment artifacts as Python variables in a REPL so the
LLM can query/filter them with execute_code actions before returning a final
answer.  This reduces token usage ~50-75% on large logs.
"""

import json
import os
import re
import subprocess
import sys
import tempfile

import litellm
from app_operator.rlm.environment import RLMContext

from app_operator.logger import logger

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
        vertex_location: str | None = None,
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

        initial_prompt = self._build_initial_prompt(task, context)
        conversation_history: list[dict] = []

        for _ in range(self.max_recursion_depth):
            prompt = self._rebuild_prompt(initial_prompt, conversation_history)
            response = self._call_llm(prompt)
            action, content = self._parse_response(response)

            if action == "final_answer":
                return content

            if action == "execute_code":
                exec_result = self._execute_code(content, namespace)
                conversation_history.append({
                    "response": response,
                    "result": exec_result,
                })
                # Keep only the last 3 iterations
                conversation_history = conversation_history[-3:]
                continue

            # Unknown action — return as-is
            return response

        # Max iterations reached
        return "ERROR: Max recursion depth reached without resolution"

    def fix_deployment_error(
        self,
        error_log: str,
        deployment_script: str,
        previous_attempts: list[str],
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

    def _rebuild_prompt(
        self, initial_prompt: str, history: list[dict]
    ) -> str:
        """Rebuild the full prompt from initial context and recent history."""
        parts = [initial_prompt]
        for entry in history:
            parts.append(f"\nAssistant:\n{entry['response']}")
            parts.append(f"\nCode result:\n{entry['result']}")
            parts.append("\nUser: Continue.")
        return "\n".join(parts)

    def _execute_code(self, code: str, namespace: dict) -> str:
        """Execute *code* in a subprocess and return the `result` variable.

        The namespace is serialised to a temporary JSON file so the
        subprocess can deserialise it.  A small epilogue is appended to
        the generated script that prints the ``result`` variable as JSON
        on stdout.

        Args:
            code: Python code to execute.
            namespace: Variable namespace (string values).

        Returns:
            String representation of ``result``, or an error message.
        """
        ns_file = None
        code_file = None
        try:
            # Write namespace to a temp JSON file
            ns_file = tempfile.NamedTemporaryFile(  # noqa: SIM115
                mode="w", suffix=".json", delete=False
            )
            json.dump(namespace, ns_file)
            ns_file.close()

            # Build the script: load namespace, run user code, emit result
            preamble = (
                "import json as _json\n"
                f"with open({ns_file.name!r}) as _f:\n"
                "    _ns = _json.load(_f)\n"
                "for _k, _v in _ns.items():\n"
                "    globals()[_k] = _v\n"
            )
            epilogue = (
                "\nimport json as _json2\n"
                "print(_json2.dumps({'result': str(result)}))\n"
            )
            full_code = preamble + code + epilogue

            code_file = tempfile.NamedTemporaryFile(  # noqa: SIM115
                mode="w", suffix=".py", delete=False
            )
            code_file.write(full_code)
            code_file.close()

            proc = subprocess.run(
                [sys.executable, code_file.name],
                capture_output=True,
                text=True,
                timeout=30,
            )

            if proc.returncode != 0:
                return f"Error: {proc.stderr.strip()}"

            output = json.loads(proc.stdout.strip())
            return output.get("result", "")
        except subprocess.TimeoutExpired:
            return "Error: Code execution timed out after 30s"
        except Exception as exc:
            return f"Error: {exc}"
        finally:
            if ns_file is not None:
                try:
                    os.unlink(ns_file.name)
                except OSError:
                    pass
            if code_file is not None:
                try:
                    os.unlink(code_file.name)
                except OSError:
                    pass

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
