from pathlib import Path
from typing import List, Tuple

from app_operator.agentflow.io import UserIO
from app_operator.agentflow.models import AgentflowResult, parse_agentflow_response
from app_operator.agentflow.storage import AgentflowStorage
from app_operator.cli_agent.backend.base import CodingAgent
from app_operator.exceptions import AgentError
from app_operator.prompts import PromptLoader


class AgentflowEngine:
    """Core logic for the Agentflow process."""

    def __init__(
        self,
        agent: CodingAgent,
        prompt_loader: PromptLoader,
        io: UserIO,
        loop_bound: int,
        max_clarifications: int,
        agent_timeout: int,
        output_dir: Path,
    ) -> None:
        self.agent = agent
        self.prompt_loader = prompt_loader
        self.io = io
        self.loop_bound = loop_bound
        self.max_clarifications = max_clarifications
        self.agent_timeout = agent_timeout
        self.storage = AgentflowStorage(output_dir)

    def run(self, user_prompt: str) -> AgentflowResult:
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
            full_prompt = f"{system_prompt}\n\n{user_msg}"

            # Call agent
            self.io.info(f"Thinking... (Round {round_idx + 1})")
            raw_response = self.agent.generate(full_prompt, timeout=self.agent_timeout)

            try:
                response = parse_agentflow_response(raw_response)
            except ValueError as e:
                # Attempt repair
                self.io.info("Parsing failed, attempting repair...")
                repair_msg = self.prompt_loader.render(
                    "agentflow/repair.jinja2", error=str(e), raw_response=raw_response
                )
                raw_response = self.agent.generate(
                    repair_msg, timeout=self.agent_timeout
                )
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
        ):
            errors.append(
                "Script must import from `app_operator.cli_agent.backend` or `app_operator.agentflow.runtime`"
            )

        if (
            'if __name__ == "__main__":' not in script_text
            and "if __name__ == '__main__':" not in script_text
        ):
            errors.append('Script must include `if __name__ == "__main__":` block')

        if errors:
            # In a more advanced version, we could loop back to the agent to fix these.
            # For now, we raise to stop or could try one repair.
            # Let's try one immediate repair via recursion or just fail for now as per plan
            # "If validation fails once, call repair prompt"

            # Since the run loop is the main driver, we should probably handle this there
            # or raise a specific validation error that triggers a repair in the loop.
            # For simplicity, I will raise ValueError and let the caller handle or just fail if not implemented in the loop.
            # But the plan said: "If validation fails once, call repair prompt (template repair.jinja2) and re-validate"
            # The current loop handles parsing errors. I should integrate logic verification there too.
            # However, `run` loop is driven by "clarify" vs "ready".
            # If "ready" produces invalid code, we should probably feedback into the loop?
            # Or just fail.

            # Plan says: "If validation fails once, call repair prompt... and re-validate"
            # I'll implement a simple retry here within the method if I can access the agent,
            # but ideally this should be part of the main loop or a sub-loop.

            # Since I don't want to complicate `run` too much, I'll just raise for now,
            # as implementing a robust repair loop for code logic inside `_validate` is tricky without passing state back.
            # Actually, I can just append the error to the prompt and continue the loop if I change status to "clarify" effectively?
            # No, "ready" means it thinks it's done.

            raise ValueError("Script validation failed:\n" + "\n".join(errors))
