"""RLM-enabled deployment agent for SDS.

Implements a deployment agent that uses RLM to handle long error logs
and iteration history efficiently.
"""

import json
import os
import re
import time
from pathlib import Path
from typing import Any

import litellm

from app_operator.logger import logger
from app_operator.prompts import DSPyConfigProtocol
from app_operator.prompts.rlm import render_fix_error_task_prompt
from app_operator.rate_limit_handler import detect_rate_limit_error
from app_operator.rlm.environment import (
    RLMEnvironment,
    RLMContext,
    RLMCall,
    ActionType,
    _validate_file_refs,
)
from app_operator.trajectory import TrajectoryRecorderProtocol


class RecursiveDeploymentAgent:
    """Deployment agent using RLM for long-context handling.

    Instead of feeding entire error logs into prompts, this agent:
    1. Stores context in RLM environment as variables
    2. Lets LLM programmatically query/filter context
    3. Makes focused recursive calls on specific issues
    4. Tracks all RLM actions in trajectory for optimization
    """

    def __init__(
        self,
        trajectory: TrajectoryRecorderProtocol | None = None,
        max_recursion_depth: int = 5,
        llm_provider: str = "gemini",
        vertex_location: str | None = None,
        max_iterations: int = 10,
        consecutive_explore_limit: int = 5,
        max_consecutive_errors: int | None = None,
        compaction: bool = False,
        compaction_threshold: float = 0.85,
        model_context_tokens: int = 32_768,
        dspy_config: DSPyConfigProtocol | None = None,
    ):
        """Initialize RLM deployment agent.

        Args:
            trajectory: Trajectory recorder for tracking RLM calls
            max_recursion_depth: Maximum allowed recursion depth
            llm_provider: LLM provider to use (e.g. vertex_ai/gemini-2.0-flash)
            vertex_location: Vertex AI location override (e.g. "global", "us-central1").
                Passed as ``vertex_location`` to litellm. Falls back to the
                ``VERTEX_LOCATION`` environment variable when None.
            max_iterations: Maximum RLM loop iterations before giving up.
            consecutive_explore_limit: Number of consecutive non-final-answer steps
                before nudging the LLM to wrap up.
            max_consecutive_errors: Stop the loop after this many consecutive code
                execution errors. None disables the limit.
            compaction: Enable automatic conversation history compaction when the
                context approaches the model's token limit.
            compaction_threshold: Fraction of model_context_tokens at which to
                compact (default 0.85 = 85%).
            model_context_tokens: Model's context window size used for compaction
                threshold calculation.
        """
        self.trajectory = trajectory
        self.max_recursion_depth = max_recursion_depth
        self.llm_provider = llm_provider
        self.vertex_location = vertex_location
        self.max_iterations = max_iterations
        self.consecutive_explore_limit = consecutive_explore_limit
        self.max_consecutive_errors = max_consecutive_errors
        self.compaction = compaction
        self.compaction_threshold = compaction_threshold
        self.model_context_tokens = model_context_tokens
        self.dspy_config = dspy_config
        self.rlm_env: RLMEnvironment | None = None
        self._system_prompt: str = ""
        self._messages: list[dict[str, str]] = []
        self._token_usage: dict[str, int] = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }

    def _record_rlm_call(self, call: RLMCall) -> None:
        """Callback to record RLM calls in trajectory."""
        if self.trajectory:
            self.trajectory.add_assistant_message(
                f"[RLM {call.action_type.value} at depth {call.depth}]\n"
                f"Input: {call.input_prompt[:200]}\n"
                f"Output: {call.output[:200]}\n"
                f"Tokens saved: ~{call.tokens_saved}"
            )

    def _create_rlm_context(
        self,
        error_log: str,
        deployment_script: str,
        previous_attempts: list[dict[str, Any]],
        repo_path: Path,
    ) -> RLMContext:
        """Create RLM context from deployment state."""
        # Try to read relevant files
        dockerfile = ""
        docker_compose = ""
        readme = ""
        analysis_report = ""

        try:
            sds_dir = repo_path / ".sds"
            if (sds_dir / "code_analysis.md").exists():
                analysis_report = (sds_dir / "code_analysis.md").read_text()

            if (repo_path / "Dockerfile").exists():
                dockerfile = (repo_path / "Dockerfile").read_text()

            compose_files = ["docker-compose.yml", "docker-compose.yaml"]
            for cf in compose_files:
                if (repo_path / cf).exists():
                    docker_compose = (repo_path / cf).read_text()
                    break

            readme_files = ["README.md", "README.txt", "README"]
            for rf in readme_files:
                if (repo_path / rf).exists():
                    readme = (repo_path / rf).read_text()
                    break
        except Exception as e:
            logger.warning(f"Failed to read some context files: {e}")

        # Get trajectory data
        trajectory_data = self.trajectory.trajectory if self.trajectory else {}

        return RLMContext(
            error_log=error_log,
            deployment_script=deployment_script,
            previous_attempts=previous_attempts,
            trajectory_data=trajectory_data,
            dockerfile=dockerfile,
            docker_compose=docker_compose,
            readme=readme,
            analysis_report=analysis_report,
            attempt_number=len(previous_attempts) + 1,
        )

    def _parse_rlm_response(self, response: str) -> dict[str, Any]:
        """Parse LLM response to extract action type and content.

        Expected format:
        ACTION: execute_code
        DESCRIPTION: Extract error messages
        CODE:
        result = re.findall(r'Error: (.*)', error_log)

        OR:

        ACTION: recursive_call
        SUBTASK: Analyze Docker network configuration
        CONTEXT: {"dockerfile": dockerfile, "error_log": error_snippet}

        OR:

        ACTION: final_answer
        ANSWER: <deployment fix>
        """
        try:
            # Extract action with regex — avoids IndexError when ACTION: is missing
            action_match = re.search(
                r'^ACTION:\s*(\S+)', response, re.MULTILINE | re.IGNORECASE
            )
            if not action_match:
                logger.warning("[RLM] No ACTION: line found, continuing loop as no-op")
                return {
                    "action": ActionType.EXECUTE_CODE,
                    "code": "",
                    "description": "no-op",
                }

            action = action_match.group(1).strip().lower()

            if action == "execute_code":
                # Extract code block — bounded by the next section keyword or EOF
                code_match = re.search(
                    r'^CODE:\s*\n(.*?)(?=\n(?:ACTION:|DESCRIPTION:|ANSWER:)\s|\Z)',
                    response,
                    re.MULTILINE | re.DOTALL,
                )
                code = code_match.group(1).strip() if code_match else ""

                desc_match = re.search(r'^DESCRIPTION:\s*(.+)$', response, re.MULTILINE)
                description = desc_match.group(1).strip() if desc_match else ""

                return {
                    "action": ActionType.EXECUTE_CODE,
                    "code": code,
                    "description": description,
                }

            elif action == "recursive_call":
                subtask_match = re.search(r'^SUBTASK:\s*(.+)$', response, re.MULTILINE)
                subtask = subtask_match.group(1).strip() if subtask_match else ""

                context_start = response.find("CONTEXT:")
                filtered_context = None
                if context_start != -1:
                    context_str = response[context_start + 8:].strip()
                    try:
                        parsed_ctx = json.loads(context_str)
                        if isinstance(parsed_ctx, dict) and parsed_ctx:
                            filtered_context = parsed_ctx
                        else:
                            logger.warning(
                                "CONTEXT parsed but empty or not a dict, using full context"
                            )
                    except json.JSONDecodeError:
                        logger.warning(
                            "Failed to parse CONTEXT as JSON, using full context"
                        )

                return {
                    "action": ActionType.RECURSIVE_CALL,
                    "subtask": subtask,
                    "context": filtered_context,
                }

            elif action == "final_answer":
                answer_match = re.search(
                    r'^ANSWER:\s*(.*)', response, re.MULTILINE | re.DOTALL
                )
                if answer_match:
                    answer = answer_match.group(1).strip()
                else:
                    lines = response.strip().split("\n")
                    answer = "\n".join(lines[1:])

                return {"action": ActionType.FINAL_ANSWER, "answer": answer}

            else:
                raise ValueError(f"Unknown action: {action}")

        except Exception as e:
            logger.error(f"Failed to parse RLM response: {e}")
            logger.debug(f"Response was: {response}")

            # Fallback: treat entire response as final answer
            return {"action": ActionType.FINAL_ANSWER, "answer": response}

    def _call_llm_isolated(self, sub_prompt: str, filtered_context: dict | None = None) -> str:
        """Make an isolated LLM call for recursive sub-tasks.

        Unlike ``_call_llm``, this builds a completely fresh ``messages``
        list (system + user) so the sub-call does not see (or pollute) the
        main conversation history.  Token usage is still accumulated into
        ``self._token_usage``.

        Args:
            sub_prompt: The focused question / task for the sub-call.
            filtered_context: Optional dict of context variables to include.
                Keys are variable names, values are their content.

        Returns:
            The assistant's response text.
        """
        from libs.agent_cli import call_subagent  # noqa: PLC0415

        context_section = ""
        if filtered_context:
            parts = []
            for key, value in filtered_context.items():
                parts.append(f"--- {key} ---\n{value}")
            context_section = "\n\n".join(parts)

        system_prompt = (
            "You are a focused analysis subagent. Answer the question below "
            "using ONLY the provided context. Be concise and specific."
        )
        user_prompt = sub_prompt
        if context_section:
            user_prompt = f"Context:\n{context_section}\n\nTask:\n{sub_prompt}"

        return call_subagent(
            model=self.llm_provider,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            location=self.vertex_location,
            token_acc=self._token_usage,
        )

    def _call_llm(self, prompt: str) -> str:
        """Call the LLM via litellm, accumulating conversation history.

        Appends ``prompt`` as a user message to ``self._messages``, calls
        litellm with the full conversation, then appends the assistant
        response.  This gives the LLM memory of prior turns (code results,
        recursive call outputs, etc.) across the RLM loop.

        ``self._messages`` must be initialised (at least with the system
        message) before calling this method — ``run_task()`` does this
        automatically.

        Vertex AI auth is handled automatically by litellm via
        ``GOOGLE_APPLICATION_CREDENTIALS`` or ``VERTEX_PROJECT`` /
        ``VERTEX_LOCATION`` environment variables.

        ``vertex_location`` is forwarded to litellm when set, either from the
        constructor argument or from the ``VERTEX_LOCATION`` environment variable.
        """
        logger.info(f"[RLM] LLM call to {self.llm_provider}, prompt length: {len(prompt)} chars")

        self._messages.append({"role": "user", "content": prompt})

        kwargs: dict[str, Any] = {
            "model": self.llm_provider,
            "messages": self._messages,
            "cache": {"no-cache": True},
        }
        location = self.vertex_location or os.environ.get("VERTEX_LOCATION")
        if location:
            kwargs["vertex_location"] = location

        max_attempts = 3
        for attempt in range(max_attempts):
            try:
                response = litellm.completion(**kwargs)
                usage = getattr(response, "usage", None)
                if usage:
                    self._token_usage["prompt_tokens"] += getattr(usage, "prompt_tokens", 0) or 0
                    self._token_usage["completion_tokens"] += getattr(usage, "completion_tokens", 0) or 0
                    self._token_usage["total_tokens"] += getattr(usage, "total_tokens", 0) or 0
                content = response.choices[0].message.content or ""
                self._messages.append({"role": "assistant", "content": content})
                return content
            except Exception as e:
                rate_err = detect_rate_limit_error(str(e), -1, self.llm_provider)
                if rate_err and attempt < max_attempts - 1:
                    delay = (rate_err.retry_after or 15) * (2 ** attempt)
                    logger.warning(
                        f"[RLM] Transient network error (attempt {attempt + 1}/{max_attempts}), "
                        f"retrying in {delay}s: {e}"
                    )
                    time.sleep(delay)
                else:
                    logger.error(f"[RLM] LLM call failed: {e}")
                    content = f"ACTION: final_answer\nANSWER: LLM call failed: {e}"
                    self._messages.append({"role": "assistant", "content": content})
                    return content
        content = f"ACTION: final_answer\nANSWER: LLM call failed after {max_attempts} attempts"
        self._messages.append({"role": "assistant", "content": content})
        return content

    def _should_compact(self) -> bool:
        """Return True if the conversation history should be compacted."""
        if not self.compaction:
            return False
        try:
            count = litellm.token_counter(model=self.llm_provider, messages=self._messages)
        except Exception:
            count = sum(len(m.get("content", "")) for m in self._messages) // 4
        return count >= self.model_context_tokens * self.compaction_threshold

    def _compact_history(self) -> None:
        """Summarize conversation history and reset to system + summary.

        Prevents the O(n²) token re-send problem over many iterations.
        """
        summary_messages = self._messages + [{
            "role": "user",
            "content": (
                "Summarize your progress so far. Include: "
                "(1) which exploration steps you completed and what they revealed, "
                "(2) any concrete findings (error patterns, missing files, etc.), "
                "(3) what your next action should be. "
                "Be concise (1-3 paragraphs) but preserve all key findings."
            ),
        }]
        kwargs: dict[str, Any] = {
            "model": self.llm_provider,
            "messages": summary_messages,
            "cache": {"no-cache": True},
        }
        location = self.vertex_location or os.environ.get("VERTEX_LOCATION")
        if location:
            kwargs["vertex_location"] = location
        try:
            response = litellm.completion(**kwargs)
            summary = response.choices[0].message.content or ""
        except Exception as e:
            logger.warning(f"[RLM] Compaction failed: {e}, keeping full history")
            return

        system_msg = self._messages[0]  # preserve system prompt
        self._messages = [
            system_msg,
            {"role": "assistant", "content": summary},
            {
                "role": "user",
                "content": (
                    "Your conversation has been compacted. Continue from the summary above. "
                    "Do not repeat completed work. Your next action:"
                ),
            },
        ]
        logger.info("[RLM] Conversation history compacted")

    def run_task(self, task: str, context: RLMContext, repo_path: str) -> str:
        """Run any task using the RLM loop.

        This is the generic entry-point used by ``RLMCodingAgent.generate()``.
        ``fix_deployment_error()`` delegates here after building an
        ``RLMContext`` from its structured parameters.

        Args:
            task: Free-form task description / rendered prompt from the operator.
            context: Pre-built ``RLMContext`` with available file contents.
            repo_path: Repository path (string) for trajectory metadata.

        Returns:
            Final answer produced by the LLM after the RLM loop.
        """
        logger.info("[RLM] Starting run_task")

        self.rlm_env = RLMEnvironment(
            context=context,
            max_recursion_depth=self.max_recursion_depth,
            record_callback=self._record_rlm_call,
            cwd=repo_path,
        )
        self._system_prompt = self.rlm_env.get_system_prompt()
        self._messages: list[dict[str, str]] = [
            {"role": "system", "content": self._system_prompt},
        ]

        if self.trajectory:
            self.trajectory.add_user_message(self._system_prompt)

        rendered_wrapper = render_fix_error_task_prompt(
            repo_path=repo_path,
            available_variables=", ".join(sorted(context.to_dict().keys())),
            error_log_size=str(len(context.error_log)),
            attempt=str(context.attempt_number),
            max_attempts=str(self.max_iterations),
            has_original_script=str(bool(context.original_script)),
            dspy_config=self.dspy_config,
            recorder=self.trajectory if hasattr(self.trajectory, "record_prompt_kwargs") else None,
        )
        current_prompt = f"Task:\n{task}\n\n{rendered_wrapper}"

        consecutive_explore_count = 0
        consecutive_errors = 0
        action = None
        response = ""
        for iteration in range(self.max_iterations):
            logger.info(f"[RLM] Iteration {iteration + 1}/{self.max_iterations}")

            # After several consecutive explore steps, nudge (not override) the prompt.
            if consecutive_explore_count >= self.consecutive_explore_limit:
                current_prompt = (
                    f"You have explored for {consecutive_explore_count} consecutive steps "
                    "without providing a final answer. Please wrap up now.\n"
                    "Write all required output files using execute_code (open() calls), "
                    "then provide ACTION: final_answer.\n\n"
                    f"Previous context: {current_prompt}"
                )

            response = self._call_llm(current_prompt)

            if self._should_compact():
                self._compact_history()

            if self.trajectory:
                self.trajectory.add_assistant_message(response, duration=0.0)

            parsed = self._parse_rlm_response(response)
            action = parsed["action"]

            if action == ActionType.FINAL_ANSWER:
                consecutive_explore_count = 0
            else:
                consecutive_explore_count += 1

            if action == ActionType.EXECUTE_CODE:
                try:
                    result = self.rlm_env.execute_code(
                        parsed["code"], parsed.get("description", "")
                    )
                    consecutive_errors = 0
                except RuntimeError as e:
                    result = f"Code execution error: {e}"
                    consecutive_errors += 1
                    if (
                        self.max_consecutive_errors is not None
                        and consecutive_errors >= self.max_consecutive_errors
                    ):
                        logger.warning(
                            f"[RLM] {consecutive_errors} consecutive errors, stopping loop"
                        )
                        return result
                current_prompt = f"Result:\n{result}\n\nContinue or provide FINAL_ANSWER."

            elif action == ActionType.RECURSIVE_CALL:
                filtered_ctx = parsed.get("context", {})
                result = self.rlm_env.recursive_call(
                    sub_prompt=parsed.get("subtask", ""),
                    filtered_context=filtered_ctx,
                    llm_function=lambda p: self._call_llm_isolated(p, filtered_ctx),
                )
                current_prompt = (
                    f"Recursive result:\n{result}\n\nContinue or provide FINAL_ANSWER."
                )

            elif action == ActionType.FINAL_ANSWER:
                # Auto-validate deploy.sh if it exists before accepting the answer
                answer = parsed.get("answer", response)
                answer = self._auto_validate_deploy_sh(repo_path, answer)

                if self.trajectory:
                    self.trajectory.add_assistant_message(
                        f"RLM Statistics: {json.dumps(self.get_rlm_statistics())}", duration=0.0
                    )
                return answer

        logger.warning(f"[RLM] Max iterations ({self.max_iterations}) reached without FINAL_ANSWER")
        return response

    def fix_deployment_error(
        self,
        error_log: str,
        deployment_script: str,
        previous_attempts: list[dict[str, Any]],
        repo_path: Path,
    ) -> str:
        """Fix deployment error using RLM.

        Builds an ``RLMContext`` from the structured deployment parameters and
        delegates to ``run_task()``.

        Args:
            error_log: Error output from failed deployment
            deployment_script: Current deployment script
            previous_attempts: History of previous fix attempts
            repo_path: Path to repository

        Returns:
            Fixed deployment script or analysis
        """
        logger.info("Starting RLM-based deployment error fixing")

        context = self._create_rlm_context(
            error_log=error_log,
            deployment_script=deployment_script,
            previous_attempts=previous_attempts,
            repo_path=repo_path,
        )

        task = (
            f"Analyze the deployment error and provide a fix.\n\n"
            f"The error log contains {len(error_log)} characters.\n"
            f"Previous attempts: {len(previous_attempts)}\n\n"
            "Use RLM features to:\n"
            "1. Extract and categorize errors from error_log\n"
            "2. Identify patterns across previous_attempts\n"
            "3. Generate a targeted fix\n\n"
            "Remember: Use EXECUTE_CODE to query context before making decisions."
        )

        return self.run_task(task=task, context=context, repo_path=str(repo_path))

    def _auto_validate_deploy_sh(self, repo_path: str, answer: str) -> str:
        """Validate deploy.sh after the RLM loop and log warnings for missing refs.

        This ensures that even if the LLM skipped calling validate_file_refs(),
        any broken file references are caught and reported.

        Args:
            repo_path: Repository path string.
            answer: The final answer from the RLM loop.

        Returns:
            The answer, potentially with a validation warning appended.
        """
        deploy_sh = Path(repo_path) / ".sds" / "deploy.sh"
        if not deploy_sh.exists():
            return answer

        try:
            script_content = deploy_sh.read_text()
            validation = _validate_file_refs(script_content, repo_path)
            if "MISSING" in validation:
                warning = (
                    f"\n[AUTO-VALIDATION WARNING] deploy.sh references missing paths:\n"
                    f"{validation}"
                )
                logger.warning(f"[RLM]{warning}")
                if self.trajectory:
                    self.trajectory.add_assistant_message(
                        f"[RLM Auto-Validation]{warning}", duration=0.0
                    )
                return answer + warning
        except Exception as e:
            logger.warning(f"[RLM] Auto-validation of deploy.sh failed: {e}")

        return answer

    def get_rlm_statistics(self) -> dict[str, Any]:
        """Get RLM usage statistics from last run.

        total_tokens_saved is the difference between the estimated single-call
        baseline prompt cost and the actual prompt tokens consumed across all
        LLM turns (from litellm).  A negative value means RLM spent more tokens
        than a single call would have, which is expected for short tasks.
        """
        stats = self.rlm_env.get_statistics() if self.rlm_env else {}
        actual_prompt_tokens = self._token_usage.get("prompt_tokens", 0)
        baseline_context_tokens = stats.get("baseline_context_tokens", 0)
        stats["actual_prompt_tokens"] = actual_prompt_tokens
        stats["total_tokens_saved"] = baseline_context_tokens - actual_prompt_tokens
        stats["token_usage"] = self._token_usage.copy()
        return stats
