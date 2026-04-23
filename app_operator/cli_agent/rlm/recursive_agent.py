"""RLM-enabled deployment agent for SDS.

Implements a deployment agent that uses RLM to handle long error logs
and iteration history efficiently.
"""

import copy
import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import litellm

from app_operator.cli_agent._subagent_utils import call_subagent
from app_operator.cli_agent.rlm.environment import (
    ActionType,
    RLMCall,
    RLMContext,
    RLMEnvironment,
    validate_file_refs,
)
from app_operator.core import logger
from app_operator.prompts import DSPyConfigProtocol, render_fix_error_task_prompt
from app_operator.trajectory import TrajectoryRecorderProtocol
from libs.llm_rt import LiteLLMClient


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
        max_consecutive_errors: int | None = 3,
        compaction: bool = False,
        compaction_threshold: float = 0.85,
        model_context_tokens: int = 32_768,
        dspy_config: DSPyConfigProtocol | None = None,
        specialist_dispatcher: Callable[[str, str], str] | None = None,
        available_specialists: dict[str, str] | None = None,
        rlm_mode: str = "compatibility",
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
                execution errors. Set to None to disable the limit.
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
        self.specialist_dispatcher = specialist_dispatcher
        self.available_specialists = dict(available_specialists or {})
        self.rlm_mode = rlm_mode
        self.rlm_env: RLMEnvironment | None = None
        self._system_prompt: str = ""
        self._messages: list[dict[str, str]] = []
        self._llm_client = LiteLLMClient(llm_provider, vertex_location, trajectory)
        self._shared_call_history: list[RLMCall] | None = None
        self._initial_depth = 0
        self._metadata_feedback_count = 0
        self._feedback_turns = 0
        self._finalization_type = "final_answer"

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
        except OSError as e:
            logger.warning(f"Failed to read some context files: {e}")

        # Get trajectory data
        trajectory_data: dict[str, Any] = (
            cast("dict[str, Any]", self.trajectory.trajectory)  # type: ignore[reportAttributeAccessIssue]
            if self.trajectory
            else {}
        )

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
            final_var_match = re.match(r"^\s*FINAL_VAR:\s*([A-Za-z_][A-Za-z0-9_]*)\s*$", response.strip())
            if final_var_match:
                return {
                    "action": ActionType.FINAL_ANSWER,
                    "final_var": final_var_match.group(1),
                }

            # Extract action with regex — avoids IndexError when ACTION: is missing
            action_match = re.search(r"^ACTION:\s*(\S+)", response, re.MULTILINE | re.IGNORECASE)
            if not action_match:
                logger.warning("[RLM] No ACTION: line found, requesting strict format retry")
                return {
                    "action": ActionType.EXECUTE_CODE,
                    "code": "",
                    "description": "invalid-format",
                    "invalid_format": True,
                }

            action = action_match.group(1).strip().lower()

            if action == "execute_code":
                # Extract code block — bounded by the next section keyword or EOF
                code_match = re.search(
                    r"^CODE:\s*\n(.*?)(?=\n(?:ACTION:|DESCRIPTION:|ANSWER:)\s|\Z)",
                    response,
                    re.MULTILINE | re.DOTALL,
                )
                code = code_match.group(1).strip() if code_match else ""

                desc_match = re.search(r"^DESCRIPTION:\s*(.+)$", response, re.MULTILINE)
                description = desc_match.group(1).strip() if desc_match else ""

                return {
                    "action": ActionType.EXECUTE_CODE,
                    "code": code,
                    "description": description,
                }

            if action == "recursive_call":
                subtask_match = re.search(r"^SUBTASK:\s*(.+)$", response, re.MULTILINE)
                subtask = subtask_match.group(1).strip() if subtask_match else ""

                context_start = response.find("CONTEXT:")
                filtered_context: dict[str, Any] | None = None
                if context_start != -1:
                    context_str = response[context_start + 8 :].strip()
                    try:
                        parsed_ctx = json.loads(context_str)
                        if isinstance(parsed_ctx, dict) and parsed_ctx:
                            filtered_context = cast("dict[str, Any]", parsed_ctx)
                        else:
                            logger.warning("CONTEXT parsed but empty or not a dict, using full context")
                    except json.JSONDecodeError:
                        logger.warning("Failed to parse CONTEXT as JSON, using full context")

                return {
                    "action": ActionType.RECURSIVE_CALL,
                    "subtask": subtask,
                    "context": filtered_context,
                }

            if action == "specialist_call":
                specialist_match = re.search(r"^SPECIALIST:\s*(.+)$", response, re.MULTILINE)
                task_match = re.search(r"^TASK:\s*(.+)$", response, re.MULTILINE)
                specialist = specialist_match.group(1).strip() if specialist_match else ""
                task = task_match.group(1).strip() if task_match else ""
                return {
                    "action": ActionType.SPECIALIST_CALL,
                    "specialist": specialist,
                    "task": task,
                }

            if action == "final_answer":
                answer_match = re.search(r"^ANSWER:\s*(.*)", response, re.MULTILINE | re.DOTALL)
                if answer_match:
                    answer = answer_match.group(1).strip()
                else:
                    lines = response.strip().split("\n")
                    answer = "\n".join(lines[1:])

                return {"action": ActionType.FINAL_ANSWER, "answer": answer}

            raise ValueError(f"Unknown action: {action}")

        except (json.JSONDecodeError, ValueError, KeyError) as e:
            logger.error(f"Failed to parse RLM response: {e}")
            logger.debug(f"Response was: {response}")

            # Fallback: treat entire response as final answer
            return {"action": ActionType.FINAL_ANSWER, "answer": response}

    def _build_recursive_subtask_context(self, filtered_context: dict[str, Any] | None) -> RLMContext:
        """Build a context for a nested recursive subtask."""
        if self.rlm_env is None:
            raise RuntimeError("RLM environment is not initialized")

        parent_context = self.rlm_env.context
        if not filtered_context:
            return copy.deepcopy(parent_context)

        child_context = RLMContext(
            attempt_number=parent_context.attempt_number,
            total_tokens_used=parent_context.total_tokens_used,
        )
        extra_variables: dict[str, Any] = {}
        known_fields = set(RLMContext.__dataclass_fields__.keys()) - {"extra_variables"}

        for key, value in filtered_context.items():
            if key in known_fields:
                setattr(child_context, key, value)
            else:
                extra_variables[key] = value

        child_context.extra_variables = extra_variables
        return child_context

    @staticmethod
    def _recursive_task_guidance(filtered_context: dict[str, Any] | None) -> str:
        """Return a lightweight wrapper prompt for nested recursive tasks."""
        available = ", ".join(sorted(filtered_context)) if filtered_context else "all inherited context variables"
        return (
            "Solve this focused recursive subtask using the REPL environment.\n"
            f"Context loaded for this subtask: {available}.\n"
            "Use execute_code to inspect variables. "
            "Use sub_rlm(...) from within code if you need deeper decomposition.\n"
            "Keep intermediate state in REPL variables and return ACTION: final_answer when the subtask is complete."
        )

    def _paper_faithful_task_guidance(self) -> str:
        """Return a minimal wrapper prompt for paper-faithful mode."""
        return (
            "Paper-faithful RLM mode is enabled.\n"
            "The full task is stored in `task_prompt`.\n"
            "Inspect it programmatically instead of relying on this wrapper.\n"
            "Use execute_code to inspect and transform context.\n"
            "Use sub_rlm(...) from within code for recursive decomposition.\n"
            "Avoid shortcutting through specialist summaries.\n"
            "Keep intermediate state in REPL variables and return final_answer only when complete."
        )

    def _metadata_feedback(self, variable_name: str, label: str) -> str:
        """Build compact feedback that points the model back to REPL state."""
        if self.rlm_env is None:
            raise RuntimeError("RLM environment is not initialized")
        self._metadata_feedback_count += 1
        self._feedback_turns += 1
        return self.rlm_env.describe_value(variable_name, label)

    def _spawn_recursive_child(self) -> "RecursiveDeploymentAgent":
        """Create a child RLM agent for a nested recursive subtask."""
        child = RecursiveDeploymentAgent(
            trajectory=self.trajectory,
            max_recursion_depth=self.max_recursion_depth,
            llm_provider=self.llm_provider,
            vertex_location=self.vertex_location,
            max_iterations=self.max_iterations,
            consecutive_explore_limit=self.consecutive_explore_limit,
            max_consecutive_errors=self.max_consecutive_errors,
            compaction=self.compaction,
            compaction_threshold=self.compaction_threshold,
            model_context_tokens=self.model_context_tokens,
            dspy_config=self.dspy_config,
            specialist_dispatcher=self.specialist_dispatcher,
            available_specialists=self.available_specialists,
            rlm_mode=self.rlm_mode,
        )
        child._llm_client = self._llm_client
        child._shared_call_history = self._shared_call_history
        child._initial_depth = self.rlm_env.current_depth if self.rlm_env is not None else 0
        return child

    def _call_recursive_subtask(self, sub_prompt: str, filtered_context: dict[str, Any] | None = None) -> str:
        """Run a recursive subtask in a nested RLM loop with isolated state/history."""
        if self.rlm_env is None:
            raise RuntimeError("RLM environment is not initialized")

        child = self._spawn_recursive_child()
        child_context = self._build_recursive_subtask_context(filtered_context)
        return child.run_task(
            task=sub_prompt,
            context=child_context,
            repo_path=self.rlm_env.cwd,
            task_guidance=self._recursive_task_guidance(filtered_context),
        )

    def _call_llm_isolated(self, sub_prompt: str, filtered_context: dict[str, Any] | None = None) -> str:
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
        context_section = ""
        if filtered_context:
            parts: list[str] = []
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
            token_acc=self._llm_client._token_usage,  # pyright: ignore[reportPrivateUsage]
        )

    def _call_llm(self, prompt: str) -> str:
        """Call the LLM, accumulating conversation history.

        Appends ``prompt`` as a user message to ``self._messages``, calls
        litellm with the full conversation via ``_llm_client``, then appends
        the assistant response.  Token tracking and recorder updates happen
        automatically inside ``_llm_client.complete()``.
        """
        logger.info(f"[RLM] LLM call to {self.llm_provider}, prompt length: {len(prompt)} chars")
        self._messages.append({"role": "user", "content": prompt})
        try:
            content = self._llm_client.complete(self._messages, label="rlm main loop")
        except (ConnectionError, TimeoutError, RuntimeError) as e:
            logger.error(f"[RLM] LLM call failed: {e}")
            content = f"ACTION: final_answer\nANSWER: LLM call failed: {e}"
        self._messages.append({"role": "assistant", "content": content})
        return content

    def _should_compact(self) -> bool:
        """Return True if the conversation history should be compacted."""
        if not self.compaction:
            return False
        try:
            count = litellm.token_counter(  # pyright: ignore[reportUnknownMemberType]
                model=self.llm_provider, messages=self._messages
            )
        except (ValueError, RuntimeError):
            count = sum(len(m.get("content", "")) for m in self._messages) // 4
        return count >= self.model_context_tokens * self.compaction_threshold

    def _compact_history(self) -> None:
        """Summarize conversation history and reset to system + summary.

        Prevents the O(n²) token re-send problem over many iterations.
        Compaction tokens are tracked via ``_llm_client`` like all other calls.
        """
        summary_messages = self._messages + [
            {
                "role": "user",
                "content": (
                    "Summarize your progress so far. Include: "
                    "(1) which exploration steps you completed and what they revealed, "
                    "(2) any concrete findings (error patterns, missing files, etc.), "
                    "(3) what your next action should be. "
                    "Be concise (1-3 paragraphs) but preserve all key findings."
                ),
            }
        ]
        try:
            summary = self._llm_client.complete(summary_messages, label="rlm compaction")
        except (ConnectionError, TimeoutError, RuntimeError) as e:
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

    def run_task(self, task: str, context: RLMContext, repo_path: str, task_guidance: str | None = None) -> str:
        """Run any task using the RLM loop.

        This is the generic entry-point used by ``RLMCodingAgent.generate()``.
        ``fix_deployment_error()`` delegates here after building an
        ``RLMContext`` from its structured parameters.

        Args:
            task: Free-form task description / rendered prompt from the operator.
            context: Pre-built ``RLMContext`` with available file contents.
            repo_path: Repository path (string) for trajectory metadata.
            task_guidance: Optional task-mode wrapper prompt. When omitted, uses
                the deployment-fix wrapper prompt.

        Returns:
            Final answer produced by the LLM after the RLM loop.
        """
        logger.info("[RLM] Starting run_task")
        self._metadata_feedback_count = 0
        self._feedback_turns = 0
        self._finalization_type = "final_answer"

        self.rlm_env = RLMEnvironment(
            context=context,
            max_recursion_depth=self.max_recursion_depth,
            record_callback=self._record_rlm_call,
            cwd=repo_path,
            available_specialists=self.available_specialists,
            task_prompt=task,
            sub_rlm_fn=self._call_recursive_subtask,
            initial_depth=self._initial_depth,
            shared_call_history=self._shared_call_history,
        )
        self._shared_call_history = self.rlm_env.call_history
        self._system_prompt = self.rlm_env.get_system_prompt()
        self._messages: list[dict[str, str]] = [
            {"role": "system", "content": self._system_prompt},
        ]

        if self.trajectory:
            self.trajectory.add_user_message(self._system_prompt)

        rendered_wrapper = task_guidance
        if rendered_wrapper is None:
            if self.rlm_mode == "paper_faithful":
                rendered_wrapper = self._paper_faithful_task_guidance()
            else:
                rendered_wrapper = render_fix_error_task_prompt(
                    repo_path=repo_path,
                    available_variables=", ".join(sorted(context.to_dict().keys())),
                    available_specialists=", ".join(sorted(self.available_specialists)),
                    error_log_size=str(len(context.error_log)),
                    attempt=str(context.attempt_number),
                    max_attempts=str(self.max_iterations),
                    has_original_script=str(bool(context.original_script)),
                    dspy_config=self.dspy_config,
                    recorder=self.trajectory if hasattr(self.trajectory, "record_prompt_kwargs") else None,
                )
        if task_guidance is None:
            # Per RLM paper Algorithm 1: only constant-size metadata about the
            # task goes into the LLM context window; the full task lives as
            # `task_prompt` in the REPL so the LLM can query it programmatically.
            task_preview = task[:200] + "..." if len(task) > 200 else task
            current_prompt = (
                f"Task loaded as `task_prompt` ({len(task)} chars). Preview: {task_preview}\n\n{rendered_wrapper}"
            )
        else:
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

            if parsed.get("invalid_format"):
                specialist_retry = (
                    "ACTION: specialist_call\nSPECIALIST: <name>\nTASK: <focused task>\n\n"
                    if self.available_specialists
                    else ""
                )
                current_prompt = (
                    "Invalid format. Reply with EXACTLY one block using one of:\n"
                    "ACTION: execute_code\nDESCRIPTION: <one line>\nCODE:\n<python code>\n\n"
                    'ACTION: recursive_call\nSUBTASK: <task>\nCONTEXT: {"key": "value"}\n\n'
                    + specialist_retry
                    + "ACTION: final_answer\nANSWER: <final response>\n\n"
                    + "FINAL_VAR: <repl_variable_name>\n\n"
                    "Rules: first line must start with ACTION:, and do not use markdown code fences."
                )
                continue

            action = parsed["action"]

            if action == ActionType.FINAL_ANSWER:
                consecutive_explore_count = 0
            else:
                consecutive_explore_count += 1

            if action == ActionType.EXECUTE_CODE:
                try:
                    result = self.rlm_env.execute_code(parsed["code"], parsed.get("description", ""))
                    consecutive_errors = 0
                except RuntimeError as e:
                    result = f"Code execution error: {e}"
                    consecutive_errors += 1
                    if self.max_consecutive_errors is not None and consecutive_errors >= self.max_consecutive_errors:
                        logger.warning(f"[RLM] {consecutive_errors} consecutive errors, stopping loop")
                        return result
                feedback = self._metadata_feedback("last_result", "Code execution result")
                current_prompt = f"{feedback}\n\nContinue or provide FINAL_ANSWER."

            elif action == ActionType.RECURSIVE_CALL:
                filtered_ctx = parsed.get("context", {})
                result = self.rlm_env.recursive_call(
                    sub_prompt=parsed.get("subtask", ""),
                    filtered_context=filtered_ctx,
                    llm_function=self._call_recursive_subtask,
                )
                self.rlm_env.set_repl_value("last_recursive_result", result)
                feedback = self._metadata_feedback("last_recursive_result", "Recursive subcall result")
                current_prompt = f"{feedback}\n\nContinue or provide FINAL_ANSWER."

            elif action == ActionType.SPECIALIST_CALL:
                specialist = parsed.get("specialist", "").strip()
                task_text = parsed.get("task", "").strip()
                result = self._run_specialist_call(specialist, task_text)
                self.rlm_env.set_repl_value("last_specialist_name", specialist)
                self.rlm_env.set_repl_value("last_specialist_result", result)
                feedback = self._metadata_feedback("last_specialist_result", f"Specialist result from `{specialist}`")
                current_prompt = f"{feedback}\n\nContinue or provide FINAL_ANSWER."

            elif action == ActionType.FINAL_ANSWER:
                # Auto-validate deploy.sh if it exists before accepting the answer
                final_var = parsed.get("final_var")
                if final_var:
                    self._finalization_type = "final_var"
                    answer_value = self.rlm_env.get_repl_value(final_var)
                    if answer_value is None:
                        answer = f"Unknown final variable: {final_var}"
                    else:
                        answer = str(answer_value)
                else:
                    self._finalization_type = "final_answer"
                    answer = parsed.get("answer", response)
                answer = self._auto_validate_deploy_sh(repo_path, answer)

                if self.trajectory:
                    self.trajectory.add_assistant_message(
                        f"RLM Statistics: {json.dumps(self.get_rlm_statistics())}", duration=0.0
                    )
                return answer

        logger.warning(f"[RLM] Max iterations ({self.max_iterations}) reached without FINAL_ANSWER")
        return response

    def _run_specialist_call(self, specialist: str, task: str) -> str:
        """Dispatch a named specialist call and cache the returned summary."""
        import time

        if self.rlm_env is None:
            raise RuntimeError("RLM environment is not initialized")

        if not specialist:
            return "[Specialist unavailable] Missing SPECIALIST name."

        if specialist not in self.available_specialists:
            return f"[Specialist unavailable] Unknown specialist: {specialist}"

        field_map = {
            "trajectory": "trajectory_summary",
            "error_log": "error_summary",
            "script": "script_summary",
            "repo": "repo_summary",
        }
        field_name = field_map.get(specialist)
        if field_name:
            cached = getattr(self.rlm_env.context, field_name, "")
            if cached:
                return cached

        if self.specialist_dispatcher is None:
            return f"[Specialist unavailable] No dispatcher configured for {specialist}."

        try:
            result = self.specialist_dispatcher(specialist, task)
        except (ConnectionError, TimeoutError, RuntimeError) as e:
            logger.warning(f"[RLM] Specialist {specialist} failed: {e}")
            result = f"[Specialist unavailable] {specialist}: {e}"

        if field_name and result:
            self.rlm_env.update_summary(specialist, result)

        call = RLMCall(
            action_type=ActionType.SPECIALIST_CALL,
            depth=self.rlm_env.current_depth,
            input_prompt=task or specialist,
            code_or_subtask=specialist,
            output=result,
            tokens_saved=0,
            timestamp=time.strftime("%Y-%m-%d %H:%M:%S"),
        )
        self.rlm_env.call_history.append(call)
        if self.rlm_env.record_callback:
            self.rlm_env.record_callback(call)
        return result

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
            validation = validate_file_refs(script_content, repo_path)
            if "MISSING" in validation:
                warning = f"\n[AUTO-VALIDATION WARNING] deploy.sh references missing paths:\n{validation}"
                logger.warning(f"[RLM]{warning}")
                if self.trajectory:
                    self.trajectory.add_assistant_message(f"[RLM Auto-Validation]{warning}", duration=0.0)
                return answer + warning
        except OSError as e:
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
        token_usage = self._llm_client._token_usage  # pyright: ignore[reportPrivateUsage]
        actual_prompt_tokens = token_usage.get("prompt_tokens", 0)
        baseline_context_tokens = stats.get("baseline_context_tokens", 0)
        stats["actual_prompt_tokens"] = actual_prompt_tokens
        stats["total_tokens_saved"] = baseline_context_tokens - actual_prompt_tokens
        stats["metadata_feedback_count"] = self._metadata_feedback_count
        stats["feedback_turns"] = self._feedback_turns
        stats["finalization_type"] = self._finalization_type
        stats["token_usage"] = token_usage.copy()
        return stats
