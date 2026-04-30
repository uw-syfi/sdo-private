import re
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import yaml
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.tools import StructuredTool, tool  # pyright: ignore[reportUnknownVariableType]
from langgraph.prebuilt import create_react_agent  # pyright: ignore[reportUnknownVariableType, reportDeprecated]
from loguru import logger

from lego_agent.prompts import PromptLoader
from libs.sds_core.filesystem import RealFilesystem
from libs.sds_core.tools import build_readonly_tools

from .config import Config
from .exceptions import AgentError
from .io import UserIO
from .llm import build_llm
from .models import (
    LegoAgentResponse,
    LegoAgentResult,
    parse_lego_agent_response,
)
from .storage import LegoAgentStorage
from .streaming import extract_tool_result, parse_chunk_content


class LegoAgentEngine:
    """Core logic for the LegoAgent process."""

    def __init__(
        self,
        config: Config,
        prompt_loader: PromptLoader,
        io: UserIO,
        loop_bound: int,
        max_clarifications: int,
        agent_timeout: int,
        output_dir: Path,
        work_dir: Path,
    ) -> None:
        self.config = config
        self.prompt_loader = prompt_loader
        self.io = io
        self.loop_bound = loop_bound
        self.max_clarifications = max_clarifications
        self.agent_timeout = agent_timeout
        self.storage = LegoAgentStorage(output_dir)
        self.work_dir = work_dir
        self._thinking_started = False

    def _wrap_tool(self, func: Callable[..., Any], name: str) -> StructuredTool:
        """Wrap a callable into a LangChain StructuredTool."""
        # Use the function's docstring and name
        t = tool(func)
        t.name = name  # Ensure name is set correctly if needed
        return t  # type: ignore[reportReturnType]

    def _submit_response(self, status: str, questions: list[str] | None = None, yaml_config: str | None = None) -> str:
        """
        Submit the final response to the user.

        Args:
            status: 'clarify' if you have questions, or 'ready' if the script is complete.
            questions: List of questions if status is 'clarify'.
            yaml_config: The complete yaml config if status is 'ready'.
        """
        return "Response submitted."

    @staticmethod
    def _parse_chunk_content(content: Any) -> str:
        """Delegate to shared utility."""
        return parse_chunk_content(content)

    @staticmethod
    def _extract_message_content(last_msg_content: Any) -> str:
        """Extract text content from an agent message response."""
        if isinstance(last_msg_content, list):
            parts: list[str] = []
            items: list[Any] = last_msg_content  # pyright: ignore[reportUnknownVariableType]
            for part in items:
                if isinstance(part, dict):
                    part_d: dict[str, Any] = part  # pyright: ignore[reportUnknownVariableType]
                    if part_d.get("type") == "text":
                        parts.append(str(part_d.get("text", "")))
                elif isinstance(part, str):
                    parts.append(part)
            return "".join(parts)
        return str(last_msg_content)

    async def _run_clarification_round(
        self, agent: Any, messages: list[BaseMessage]
    ) -> tuple[str, dict[str, Any] | None]:
        """Stream one clarification round and return (final_content, final_response_data).

        Handles streaming events from the agent, rendering thinking chunks and
        tool calls to the IO layer. Falls back to ``ainvoke`` when streaming
        yields no content.

        Raises ``AgentError`` on execution failure.
        """
        final_response_data: dict[str, Any] | None = None

        try:
            accumulated_text: list[str] = []
            async for event in agent.astream_events(
                {"messages": messages}, version="v1", config={"recursion_limit": 50}
            ):
                kind = event["event"]

                if kind == "on_chat_model_stream":
                    chunk = event["data"].get("chunk")
                    if not chunk:
                        continue
                    content = chunk.content
                    text_chunk = self._parse_chunk_content(content)

                    if text_chunk:
                        if not self._thinking_started:
                            self.io.render_thinking_chunk("\nThinking: ")
                            self._thinking_started = True
                        self.io.render_thinking_chunk(text_chunk)
                        accumulated_text.append(text_chunk)

                elif kind == "on_tool_start":
                    name = event["name"]
                    inputs: Any = event["data"].get("input")
                    if name == "submit_response":
                        final_response_data = cast(
                            "dict[str, Any] | None", inputs if isinstance(inputs, dict) else None
                        )
                        # Optimization: If response is valid, stop agent immediately
                        # to avoid re-invoking the LLM with the tool output.
                        if final_response_data:
                            try:
                                status_raw = final_response_data.get("status")
                                LegoAgentResponse(
                                    status=cast("Any", status_raw),
                                    questions=final_response_data.get("questions", []) or [],
                                    yaml_config=final_response_data.get("yaml_config"),
                                ).validate()
                                break
                            except (ValueError, TypeError, KeyError) as e:
                                logger.debug("Early response validation failed, continuing agent execution: %s", e)

                    if self._thinking_started:
                        self.io.info("")  # Newline
                        self._thinking_started = False
                    self.io.render_tool_start(name, str(cast("Any", inputs)))

                elif kind == "on_tool_end":
                    name = event["name"]
                    output = event["data"].get("output")
                    status, result_text = extract_tool_result(output)
                    self.io.render_tool_end(name, result_text, status)

            final_content = "".join(accumulated_text)
            if self._thinking_started:
                self.io.info("")
                self._thinking_started = False

            if not final_content and final_response_data is None:
                # Fallback if streaming failed to capture or model didn't stream
                logger.warning("Streaming yielded no content, running invoke...")
                result = await agent.ainvoke({"messages": messages})
                last_msg_content = result["messages"][-1].content
                final_content = self._extract_message_content(last_msg_content)

        except Exception as e:
            logger.error(f"Error during agent execution: {e}")
            raise AgentError(f"Agent execution failed: {e}") from e

        return final_content, final_response_data

    def _parse_response(
        self,
        final_content: str,
        final_response_data: dict[str, Any] | None,
    ) -> LegoAgentResponse:
        """Parse and validate the agent response from a clarification round.

        Tries structured tool data first (``final_response_data``), then falls
        back to JSON extraction from ``final_content``. Validates that a
        ``ready`` response includes a valid YAML config.

        Raises ``ValueError`` when parsing or validation fails.
        """
        response = None
        if final_response_data:
            try:
                response = LegoAgentResponse(
                    status=final_response_data.get("status", "ready"),  # type: ignore[reportArgumentType]
                    questions=final_response_data.get("questions", []) or [],
                    yaml_config=final_response_data.get("yaml_config"),
                )
                response.validate()
            except Exception as e:
                logger.warning("Response validation failed, falling back to text parsing: %s", e)
                self.io.render_error(f"Response validation failed: {e}")

        if not response:
            response = parse_lego_agent_response(final_content)

        # Validate YAML immediately to trigger repair loop if needed
        if response.status == "ready":
            if not response.yaml_config:
                raise ValueError("Status is ready but no yaml_config provided.")
            response.yaml_config = self._extract_yaml_block(response.yaml_config)
            self._validate_config(response.yaml_config)

        return response

    def _handle_ready_response(
        self,
        response: LegoAgentResponse,
        qa_pairs: list[tuple[str, str]],
    ) -> LegoAgentResult:
        """Process a 'ready' response: validate config, write files, return result."""
        yaml_text = response.yaml_config
        if not yaml_text:
            raise AgentError("Status is ready but no yaml_config provided.")

        # Validate config
        self._validate_config(yaml_text)

        # Write YAML config
        config_path = self.storage.write_config(yaml_text)

        # Render graph in UI
        try:
            config_dict = yaml.safe_load(yaml_text)
            self.io.render_graph(config_dict)
        except Exception as e:
            logger.warning(f"Failed to render graph: {e}")

        # Generate Python launcher script
        launcher_script = (
            f"#!/usr/bin/env python3\n"
            f"import os\n"
            f"import sys\n"
            f"from pathlib import Path\n"
            f"from lego_agent.backend.pipeline_builder import run_yaml_v2\n\n"
            f"MAX_ITERATIONS = {self.loop_bound}\n\n"
            f"if __name__ == '__main__':\n"
            f"    config_path = Path(__file__).parent / {Path(config_path).name!r}\n"
            f"    repo_root = Path(__file__).resolve().parents[2]\n"
            f"    os.chdir(repo_root)\n"
            f"    run_yaml_v2(str(config_path))\n"
        )

        script_path = self.storage.write_script(launcher_script)

        return LegoAgentResult(
            script_path=script_path,
            config_path=config_path,
            script_text=launcher_script,
            clarifications=qa_pairs,
        )

    async def run_async(self, user_prompt: str) -> LegoAgentResult:
        """Run the clarification loop and generate the script."""
        qa_pairs: list[tuple[str, str]] = []

        # Setup tools
        filesystem = RealFilesystem()
        readonly_tools = build_readonly_tools(self.work_dir, filesystem)
        tools = [self._wrap_tool(t, t.__name__) for t in readonly_tools] + [
            self._wrap_tool(self._submit_response, "submit_response")
        ]

        # Build LLM
        llm = build_llm(self.config)

        for round_idx in range(self.max_clarifications + 1):
            if round_idx == self.max_clarifications:
                raise AgentError("Max clarifications exceeded without reaching 'ready' state.")

            self._thinking_started = False
            # Render prompts
            system_prompt = self.prompt_loader.render("lego_agent/system.jinja2", qa_pairs=qa_pairs)
            user_msg_text = self.prompt_loader.render("lego_agent/user.jinja2", user_prompt=user_prompt)

            # Create agent graph
            # We recreate it each time to reset state or we could persist it,
            # but since we are changing the prompt (QA pairs), it's easier to treat each round as a fresh generation
            # with full context in the prompt.
            agent = create_react_agent(llm, tools, prompt=system_prompt)  # pyright: ignore[reportDeprecated, reportUnknownVariableType]

            self.io.info(f"Thinking... (Round {round_idx + 1})")

            messages: list[BaseMessage] = [HumanMessage(content=user_msg_text)]

            # Run one clarification round: stream events, collect response
            final_content, final_response_data = await self._run_clarification_round(agent, messages)

            # Parse and validate the response, with repair on failure
            try:
                response = self._parse_response(final_content, final_response_data)
            except ValueError as e:
                # Attempt repair
                self.io.render_error(f"Parsing/Validation failed, attempting repair... {e}")
                repair_msg_text = self.prompt_loader.render(
                    "lego_agent/repair.jinja2", error=str(e), raw_response=final_content
                )

                # Append repair message to history (simulated by extending messages)
                messages.append(AIMessage(content=final_content))
                messages.append(HumanMessage(content=repair_msg_text))

                final_content, final_response_data = await self._run_clarification_round(agent, messages)
                response = self._parse_response(final_content, final_response_data)

            if response.status == "clarify":
                self.io.render_info("Agent needs clarification:")
                answers = await self.io.ask_questions(response.questions)
                # Store Q&A
                for q, a in zip(response.questions, answers, strict=False):
                    qa_pairs.append((q, a))

            elif response.status == "ready":
                return self._handle_ready_response(response, qa_pairs)

        raise AgentError("Max clarifications exceeded without reaching 'ready' state.")

    @staticmethod
    def _extract_yaml_block(text: str) -> str:
        """Strip prose and code fences that LLMs sometimes prepend to yaml_config."""
        if not text:
            return text
        # Strip markdown code fence wrapper (```yaml ... ``` or ``` ... ```)
        fence = re.search(r'```(?:yaml|json)?\s*\n(.*?)(?:\n```|$)', text.strip(), re.DOTALL)
        if fence:
            text = fence.group(1).strip()
        # Strip any prose before the first 'workflow:' at the start of a line
        workflow = re.search(r'(?:^|\n)(workflow:.*)', text, re.DOTALL)
        if workflow:
            return workflow.group(1)
        return text

    def _validate_config(self, yaml_text: str) -> None:
        """Validate the generated YAML config."""
        try:
            config = yaml.safe_load(yaml_text)
            if not isinstance(config, dict):
                raise ValueError("YAML must be a dictionary")
            if "workflow" not in config:
                raise ValueError("YAML must contain 'workflow' key")
        except yaml.YAMLError as e:
            raise ValueError(f"Invalid YAML: {e}") from e
        except Exception as e:
            raise ValueError(f"Config validation failed: {e}") from e
