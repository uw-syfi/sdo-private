"""Interactive terminal chat session built on the hybrid RLM architecture."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from app_operator.cli_agent.hybrid_agent import HybridCodingAgent
from app_operator.cli_agent.rlm.environment import RLMContext
from app_operator.cli_agent.rlm.recursive_agent import RecursiveDeploymentAgent
from app_operator.cli_agent.subagent_agent import SubagentCodingAgent

if TYPE_CHECKING:
    from app_operator.prompts import DSPyConfigProtocol

DEFAULT_CHAT_INTRO_PROMPT = (
    "You are a terminal codebase assistant for this repository. "
    "Inspect the local code before answering when the user asks about implementation details. "
    "Use concise, concrete answers unless the user asks for depth. "
    "Use specialist_call when a focused high-level summary will save context."
)

DEFAULT_INITIAL_CHAT_MESSAGE = (
    "Check over this codebase and introduce yourself. "
    "Summarize the main components, likely purpose, and what you can help with."
)

DEFAULT_CHAT_TASK_GUIDANCE = """You are in interactive terminal chat mode.

Answer the user's current question about the repository loaded in `cwd`.
Use `execute_code` to inspect files, search the tree, and gather evidence before answering.
Use `specialist_call` when a focused summary of a large context slice would help.
If the user asks you to edit files, write the changes via `execute_code` before returning `final_answer`.
Keep answers concise and practical.
"""


@dataclass
class ChatTurn:
    role: str
    content: str


class HybridTerminalChatSession:
    """Stateful terminal chat wrapper around the hybrid RLM loop."""

    def __init__(
        self,
        repo_path: str,
        model: str | None = None,
        location: str | None = None,
        dspy_config: DSPyConfigProtocol | None = None,
        rlm_mode: str = "compatibility",
        intro_prompt: str = DEFAULT_CHAT_INTRO_PROMPT,
    ):
        self.repo_path = Path(repo_path)
        self.intro_prompt = intro_prompt.strip()
        self.rlm_mode = rlm_mode
        self._history: list[ChatTurn] = []
        self._hybrid_agent = HybridCodingAgent(
            model=model,
            location=location,
            dspy_config=dspy_config,
            rlm_mode=rlm_mode,
        )
        self._helper = SubagentCodingAgent(model=model, location=location, dspy_config=dspy_config)

    def reply(self, user_message: str) -> str:
        """Answer one interactive chat turn while preserving prior conversation."""
        context = self._build_context()
        agent = RecursiveDeploymentAgent(
            llm_provider=self._hybrid_agent.model,
            vertex_location=self._hybrid_agent.location,
            dspy_config=self._hybrid_agent.dspy_config,
            rlm_mode=self.rlm_mode,
            specialist_dispatcher=lambda specialist, task: self._hybrid_agent.run_specialist_analysis(
                helper=self._helper,
                repo_path=self.repo_path,
                specialist=specialist,
                task=task,
                token_acc=None,
            ),
            available_specialists=self._hybrid_agent.available_specialists,
        )
        task = self._build_task(user_message)
        answer = agent.run_task(
            task=task,
            context=context,
            repo_path=str(self.repo_path),
            task_guidance=DEFAULT_CHAT_TASK_GUIDANCE,
        )
        self._history.append(ChatTurn(role="user", content=user_message))
        self._history.append(ChatTurn(role="assistant", content=answer))
        return answer

    def _build_task(self, user_message: str) -> str:
        """Build the current chat task from the intro prompt and recent history."""
        history_text = self._format_history()
        return (
            f"{self.intro_prompt}\n\n"
            f"Repository root: {self.repo_path}\n\n"
            f"Conversation so far:\n{history_text}\n\n"
            f"Current user message:\n{user_message}\n"
        )

    def _format_history(self, max_turns: int = 12) -> str:
        """Render recent chat history into the task prompt."""
        if not self._history:
            return "(none)"
        turns = self._history[-max_turns:]
        return "\n\n".join(f"{turn.role.title()}: {turn.content}" for turn in turns)

    def _build_context(self) -> RLMContext:
        """Build an RLM context from the current repository state."""
        sds = self.repo_path / ".sds"
        deploy_log = self._helper.read_text(sds / "logs" / "deploy.log")
        health_log = self._helper.read_text(sds / "logs" / "health_check.log")
        return RLMContext(
            error_log=(deploy_log + "\n" + health_log).strip(),
            deployment_script=self._helper.read_text(sds / "deploy.sh"),
            health_check_output=health_log,
            dockerfile=self._helper.read_text(self.repo_path / "Dockerfile"),
            docker_compose=(
                self._helper.read_text(self.repo_path / "docker-compose.yml")
                or self._helper.read_text(self.repo_path / "docker-compose.yaml")
            ),
            readme=(
                self._helper.read_text(self.repo_path / "README.md")
                or self._helper.read_text(self.repo_path / "README.rst")
                or self._helper.read_text(self.repo_path / "README")
            ),
            analysis_report=self._helper.read_text(sds / "code_analysis.md"),
            original_script=self._helper.read_text(sds / "deploy.sh.bak"),
        )
