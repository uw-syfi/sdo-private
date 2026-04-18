"""JudgeAgent — encapsulates the judge agent role: tool assembly, prompt
rendering, deps construction, and the submit-reminder retry loop.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from libs.pydantic_agent import UsageCollector
    from sregym_agents.crucible._prompts import PromptRenderer
    from sregym_agents.crucible.agents.base import AgentDriver, AgentResult
    from sregym_agents.crucible.tools import SharedFile

logger = logging.getLogger(__name__)


class JudgeAgent:
    """Encapsulates the judge agent role: tool assembly, prompt rendering,
    deps construction, and the submit-reminder retry loop.
    """

    MAX_SUBMIT_REMINDERS = 3

    def __init__(
        self,
        driver: AgentDriver,
        model_id: Any,
        renderer: PromptRenderer,
    ) -> None:
        self._driver = driver
        self._model_id = model_id
        self._renderer = renderer

    def _assemble_tools(self) -> list[Any]:
        """Return the tool list for the judge."""
        from sregym_agents.crucible.tools import (
            exec_bash,
            grep,
            read_file,
            reveal_agent_hypothesis,
            str_replace_file,
            submit_independent_findings,
            submit_verdict,
            write_file,
        )

        return [
            exec_bash,
            read_file,
            grep,
            write_file,
            str_replace_file,
            submit_independent_findings,
            reveal_agent_hypothesis,
            submit_verdict,
        ]

    def _model_settings(self) -> dict[str, Any]:
        """Return model_settings for judge runs."""
        from libs.pydantic_agent import thinking_settings
        from sregym_agents.crucible.tools import THINKING_BUDGET

        return dict(thinking_settings(self._model_id, THINKING_BUDGET))

    async def run(
        self,
        *,
        app_info: dict[str, Any],
        stage: str,
        iteration: int,
        shared_file: SharedFile,
        shared_content: str,
        submit_mcp_url: str,
        hypothesis_text: str = "",
        architecture_content: str = "",
        lt_summary_content: str = "",
        lessons_content: str = "",
        usage_collector: UsageCollector | None = None,
    ) -> AgentResult[str]:
        """Run the judge agent with submit-reminder retry loop.

        Checks ``deps.state.submitted`` even on ``completed=False`` — the
        judge may have called ``submit_verdict`` before the model error.
        """
        from sregym_agents.crucible.tools import JudgeDeps, SharedState

        state = SharedState()
        deps = JudgeDeps(
            namespace=app_info.get("namespace", "default"),
            shared_file=shared_file,
            iteration=iteration,
            stage=stage,
            submit_mcp_url=submit_mcp_url,
            renderer=self._renderer,
            hypothesis_text=hypothesis_text,
            state=state,
            usage_collector=usage_collector,
        )

        system_prompt = self._renderer.render(f"{stage}_judge_system")
        user_prompt = self._renderer.render(
            f"{stage}_judge_user",
            app_name=app_info.get("app_name", "unknown"),
            namespace=app_info.get("namespace", "default"),
            iteration=iteration,
            shared_content=shared_content,
            shared_file=str(shared_file),
            architecture_content=architecture_content,
            lt_summary_content=lt_summary_content,
            lessons_content=lessons_content,
        )
        logger.info(f"[{stage}-judge] SYSTEM PROMPT:\n{system_prompt}")
        logger.info(f"[{stage}-judge] USER PROMPT:\n{user_prompt}")

        tools = self._assemble_tools()
        model_settings = self._model_settings()

        message_history: list[Any] | None = None
        current_prompt = user_prompt
        reminder_count = 0
        last_result: AgentResult[str] | None = None

        while True:
            result: AgentResult[str] = await self._driver.run(
                prompt=current_prompt,
                system_prompt=system_prompt,
                tools=tools,
                output_type=str,
                agent_name=f"judge-{stage}",
                model_settings=model_settings,
                message_history=message_history,
                usage_collector=usage_collector,
                deps=deps,
                run_ctx={"stage": stage, "iteration": iteration, "role": "judge"},
            )
            last_result = result

            if not result.completed:
                # Check if judge submitted before the error
                if state.submitted:
                    logger.warning("Judge model error after submitting; treating as complete.")
                else:
                    logger.warning("Judge model error without submitting; treating as unsubmitted.")
                break

            if state.submitted:
                break

            if reminder_count >= self.MAX_SUBMIT_REMINDERS:
                logger.warning("Max judge submit reminders reached — giving up.")
                break

            reminder_count += 1
            logger.warning(f"Judge stopped without submitting (reminder {reminder_count}/{self.MAX_SUBMIT_REMINDERS}).")
            message_history = result.messages
            current_prompt = (
                "You have not submitted your verdict yet. "
                "Please call `submit_verdict` with your final verdict before finishing."
            )

        # Attach state for orchestrator access
        assert last_result is not None  # loop always runs at least once
        last_result.state = state
        return last_result
