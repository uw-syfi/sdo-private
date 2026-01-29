from pathlib import Path
from typing import List, Tuple, Any

from agentflow.io import UserIO
from agentflow.models import AgentflowResult, parse_agentflow_response
from agentflow.storage import AgentflowStorage
from app_operator.adk.runner import AdkAgentRunner
from app_operator.adk.agent_factory import build_adk_agent
from app_operator.adk.tools import (
    ToolContext,
    _build_read_file,
    _build_list_files,
    _build_find_files,
    _build_search_content,
)
from app_operator.filesystem import RealFilesystem
from app_operator.exceptions import AgentError
from agentflow.prompts import PromptLoader


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
        work_dir: Path,
    ) -> None:
        self.runner = runner
        self.model = model
        self.prompt_loader = prompt_loader
        self.io = io
        self.loop_bound = loop_bound
        self.max_clarifications = max_clarifications
        self.agent_timeout = agent_timeout
        self.storage = AgentflowStorage(output_dir)
        self.work_dir = work_dir
        self._thinking_started = False

    def _on_event(self, event: Any) -> None:
        """Handle ADK events for streaming output."""
        # 1. Handle tool calls (ADK can yield these directly)
        fn_calls = []
        if hasattr(event, "get_function_calls"):
            try:
                fn_calls = event.get_function_calls()
            except Exception:
                pass

        for fn_call in fn_calls:
            name = getattr(fn_call, "name", "tool")
            args = getattr(fn_call, "args", {})
            self.io.info(f"\n[Tool Use] {name}({args})")

        # 2. Handle tool responses
        fn_resps = []
        if hasattr(event, "get_function_responses"):
            try:
                fn_resps = event.get_function_responses()
            except Exception:
                pass

        for fn_resp in fn_resps:
            name = getattr(fn_resp, "name", "tool")
            resp = getattr(fn_resp, "response", {})
            # Extract meaningful output from tool response
            output = ""
            if isinstance(resp, dict):
                output = resp.get("output") or resp.get("result") or str(resp)
            else:
                output = str(resp)
            
            # Truncate long output
            if len(output) > 500:
                output = output[:500] + "... (truncated)"
            self.io.info(f"\n[Tool Result] {name}: {output}")

        # 3. Handle content parts (text, thought)
        content = getattr(event, "content", None)
        if content:
            parts = getattr(content, "parts", None) or []
            for part in parts:
                # Thinking
                thought = getattr(part, "thought", None)
                if thought:
                    if not self._thinking_started:
                        self.io.info("\n[Thinking]")
                        self._thinking_started = True
                    self.io.print_stream(thought)
                    continue

                # Text
                text = getattr(part, "text", None)
                if text:
                    if self._thinking_started:
                        self.io.info("")  # Newline after thinking block
                        self._thinking_started = False
                    self.io.print_stream(text)

                # Fallback for tool calls in parts
                fn_call = getattr(part, "function_call", None)
                if fn_call and not fn_calls:
                    name = getattr(fn_call, "name", "tool")
                    args = getattr(fn_call, "args", {})
                    self.io.info(f"\n[Tool Use] {name}({args})")

                # Fallback for tool responses in parts
                fn_resp = getattr(part, "function_response", None)
                if fn_resp and not fn_resps:
                    name = getattr(fn_resp, "name", "tool")
                    resp = getattr(fn_resp, "response", {})
                    self.io.info(f"\n[Tool Result] {name}: {resp}")

    async def run_async(self, user_prompt: str) -> AgentflowResult:
        """Run the clarification loop and generate the script."""
        qa_pairs: List[Tuple[str, str]] = []

        # Setup tools
        filesystem = RealFilesystem()
        context = ToolContext(repo_root=self.work_dir, filesystem=filesystem)
        tools = [
            _build_read_file(context),
            _build_list_files(context),
            _build_find_files(context),
            _build_search_content(context),
        ]

        for round_idx in range(self.max_clarifications + 1):
            if round_idx == self.max_clarifications:
                raise AgentError(
                    "Max clarifications exceeded without reaching 'ready' state."
                )

            self._thinking_started = False
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
                tools=tools,
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
            "libs.agent_cli" not in script_text
            and "agentflow.runtime" not in script_text
            and "app_operator" not in script_text
        ):
            errors.append(
                "Script must import from `agentflow.runtime` or related modules"
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
