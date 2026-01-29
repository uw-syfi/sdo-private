from pathlib import Path
from typing import List, Tuple, Any

from app_operator.agentflow.io import UserIO
from app_operator.agentflow.models import AgentflowResult, parse_agentflow_response
from app_operator.agentflow.storage import AgentflowStorage
from app_operator.adk.runner import AdkAgentRunner
from app_operator.adk.agent_factory import build_adk_agent
from app_operator.exceptions import AgentError
from app_operator.prompts import PromptLoader
from google.genai import types


class AgentflowEngine:
    """Core logic for the Agentflow process."""

    def __init__(
        self,
        runner: AdkAgentRunner,
        model: Any,
        prompt_loader: PromptLoader,
        io: UserIO,
        loop_bound: int,
        max_clarifications: int,
        agent_timeout: int,
        output_dir: Path,
    ) -> None:
        self.runner = runner
        self.model = model
        self.prompt_loader = prompt_loader
        self.io = io
        self.loop_bound = loop_bound
        self.max_clarifications = max_clarifications
        self.agent_timeout = agent_timeout
        self.storage = AgentflowStorage(output_dir)

    def _on_event(self, event: Any) -> None:
        """Handle ADK events for streaming output."""
        content = getattr(event, "content", None)
        if content:
            parts = getattr(content, "parts", None) or []
            for part in parts:
                text = getattr(part, "text", None)
                if text:
                    self.io.print_stream(text)

                fn_call = getattr(part, "function_call", None)
                if fn_call:
                    name = getattr(fn_call, "name", "tool")
                    args = getattr(fn_call, "args", {})
                    self.io.info(f"\n[Tool Use] {name}({args})")

    async def run_async(self, user_prompt: str) -> AgentflowResult:
        """Run the clarification loop and generate the script."""
        qa_pairs: List[Tuple[str, str]] = []

        for round_idx in range(self.max_clarifications + 1):
            if round_idx == self.max_clarifications:
                raise AgentError(
                    "Max clarifications exceeded without reaching 'ready' state."
                )

            # Render prompts
            system_prompt = self.prompt_loader.render("agentflow/system.jinja2")
            user_msg = self.prompt_loader.render(
                "agentflow/user.jinja2",
                user_prompt=user_prompt,
                qa_pairs=qa_pairs,
                loop_bound=self.loop_bound,
            )

            # Build ADK agent
            agent = build_adk_agent(
                name="Agentflow",
                instruction=system_prompt,
                model=self.model,
                tools=[],
            )

            # Call agent
            self.io.info(f"Thinking... (Round {round_idx + 1})")
            raw_response = await self.runner.run_async(
                agent, user_msg, on_event=self._on_event
            )
            self.io.info("")

            try:
                response = parse_agentflow_response(raw_response)
            except ValueError as e:
                # Attempt repair
                self.io.info("Parsing failed, attempting repair...")
                repair_msg = self.prompt_loader.render(
                    "agentflow/repair.jinja2", error=str(e), raw_response=raw_response
                )
                # Include context in repair
                full_repair_prompt = f"{user_msg}\n\n{repair_msg}"
                raw_response = await self.runner.run_async(
                    agent, full_repair_prompt, on_event=self._on_event
                )
                self.io.info("")
                response = parse_agentflow_response(raw_response)

            if response.status == "clarify":
                self.io.info("Agent needs clarification:")
                answers = self.io.ask_questions(response.questions)
                # Store Q&A
                for q, a in zip(response.questions, answers):
                    qa_pairs.append((q, a))

            elif response.status == "ready":
                script_text = response.python_script
                if not script_text:
                    raise AgentError("Status is ready but no script provided.")

                # Validate script
                self._validate_script(script_text)

                # Write to file
                script_path = self.storage.write_script(script_text)

                return AgentflowResult(
                    script_path=script_path,
                    script_text=script_text,
                    clarifications=qa_pairs,
                )

        raise AgentError("Unreachable code")

    def _validate_script(self, script_text: str) -> None:
        """Validate the generated script content."""
        errors = []

        if f"MAX_ITERATIONS = {self.loop_bound}" not in script_text:
            errors.append(f"Script must define `MAX_ITERATIONS = {self.loop_bound}`")

        if (
            "app_operator.cli_agent.backend" not in script_text
            and "app_operator.agentflow.runtime" not in script_text
            and "app_operator" not in script_text
        ):
            errors.append(
                "Script must import from `app_operator.agentflow.runtime` or related modules"
            )

        if (
            'if __name__ == "__main__":' not in script_text
            and "if __name__ == '__main__':" not in script_text
        ):
            errors.append('Script must include `if __name__ == "__main__":` block')

        if errors:
            # Include script snippet in error for debugging
            snippet = (
                script_text[:500] + "..." if len(script_text) > 500 else script_text
            )
            raise ValueError(
                f"Script validation failed:\n{snippet}\n" + "\n".join(errors)
            )
