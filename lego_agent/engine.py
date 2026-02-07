import json
import ast
import yaml
from pathlib import Path
from typing import List, Tuple, Any, Callable

from langchain_core.messages import HumanMessage, AIMessage
from langchain_core.tools import tool, StructuredTool
from langgraph.prebuilt import create_react_agent

from lego_agent.io import UserIO
from lego_agent.models import (
    LegoAgentResult,
    parse_lego_agent_response,
    LegoAgentResponse,
)
from lego_agent.storage import LegoAgentStorage
from lego_agent.prompts import PromptLoader

# SDS-REVIEW: Architecture - Tight coupling with `app_operator`.
# If `lego_agent` is intended to be a reusable library, these dependencies should be inverted or abstracted.
from app_operator.langgraph.llm import build_llm
from app_operator.config import Config
from app_operator.adk.tools import (
    ToolContext,
    _build_read_file,
    _build_list_files,
    _build_find_files,
    _build_search_content,
)
from app_operator.filesystem import RealFilesystem
from app_operator.exceptions import AgentError
from app_operator.logger import logger


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

    def _wrap_tool(self, func: Callable, name: str) -> StructuredTool:
        """Wrap a callable into a LangChain StructuredTool."""
        # Use the function's docstring and name
        t = tool(func)
        t.name = name  # Ensure name is set correctly if needed
        return t

    def _submit_response(
        self, status: str, questions: List[str] = None, yaml_config: str = None
    ) -> str:
        """
        Submit the final response to the user.

        Args:
            status: 'clarify' if you have questions, or 'ready' if the script is complete.
            questions: List of questions if status is 'clarify'.
            yaml_config: The complete yaml config if status is 'ready'.
        """
        return "Response submitted."

    def _parse_chunk_content(self, content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            text = ""
            for part in content:
                if isinstance(part, dict):
                    if part.get("type") == "text":
                        text += part.get("text", "")
                    elif part.get("type") == "thinking":
                        text += part.get("thinking", "")
                elif isinstance(part, str):
                    text += part
            return text
        return str(content)

    async def run_async(self, user_prompt: str) -> LegoAgentResult:
        """Run the clarification loop and generate the script."""
        # SDS-REVIEW: Architecture - High complexity method.
        # Suggest splitting into: `_setup_agent()`, `_execute_round()`, `_process_stream()`, `_handle_response()`.
        qa_pairs: List[Tuple[str, str]] = []

        # Setup tools
        filesystem = RealFilesystem()
        context = ToolContext(repo_root=self.work_dir, filesystem=filesystem)

        # Build specific tools used by LegoAgent (read-only mostly)
        tools = [
            self._wrap_tool(_build_read_file(context), "read_file"),
            self._wrap_tool(_build_list_files(context), "list_files"),
            self._wrap_tool(_build_find_files(context), "find_files"),
            self._wrap_tool(_build_search_content(context), "search_content"),
            self._wrap_tool(self._submit_response, "submit_response"),
        ]

        # Build LLM
        llm = build_llm(self.config)

        for round_idx in range(self.max_clarifications + 1):
            if round_idx == self.max_clarifications:
                raise AgentError(
                    "Max clarifications exceeded without reaching 'ready' state."
                )

            self._thinking_started = False
            # Render prompts
            system_prompt = self.prompt_loader.render("lego_agent/system.jinja2")
            user_msg_text = self.prompt_loader.render(
                "lego_agent/user.jinja2",
                user_prompt=user_prompt,
                qa_pairs=qa_pairs,
                loop_bound=self.loop_bound,
            )

            # Create agent graph
            # SDS-REVIEW: Performance - Recreating the agent graph every iteration is inefficient.
            # We recreate it each time to reset state or we could persist it,
            # but since we are changing the prompt (QA pairs), it's easier to treat each round as a fresh generation
            # with full context in the prompt.
            agent = create_react_agent(llm, tools, prompt=system_prompt)

            self.io.info(f"Thinking... (Round {round_idx + 1})")

            messages = [HumanMessage(content=user_msg_text)]
            final_content = ""
            final_response_data = None

            # Run with streaming
            try:
                # SDS-REVIEW: Logic - Complex streaming logic. Extract to `_stream_agent_execution()`.
                # Use astream_events to capture thoughts and tool calls
                accumulated_text = []
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
                        inputs = event["data"].get("input")
                        if name == "submit_response":
                            final_response_data = inputs
                            # Optimization: If response is valid, stop agent immediately
                            # to avoid re-invoking the LLM with the tool output.
                            if inputs:
                                try:
                                    LegoAgentResponse(
                                        status=inputs.get("status"),
                                        questions=inputs.get("questions", []) or [],
                                        yaml_config=inputs.get("yaml_config"),
                                    ).validate()
                                    break
                                except Exception:
                                    pass

                        if self._thinking_started:
                            self.io.info("")  # Newline
                            self._thinking_started = False
                        self.io.render_tool_start(name, str(inputs))

                    elif kind == "on_tool_end":
                        name = event["name"]
                        output = event["data"].get("output")

                        status = "unknown"  # Default
                        result_text = ""

                        # Handle ToolMessage or simple output
                        content = getattr(output, "content", output)

                        try:
                            if isinstance(content, str):
                                # Try parsing as JSON first
                                try:
                                    content_dict = json.loads(content)
                                except json.JSONDecodeError:
                                    try:
                                        content_dict = ast.literal_eval(content)
                                    except (ValueError, SyntaxError):
                                        content_dict = None

                                if isinstance(content_dict, dict):
                                    status = content_dict.get("status", "unknown")
                                    result_text = str(content_dict.get("output", ""))
                                else:
                                    result_text = content
                            elif isinstance(content, dict):
                                status = content.get("status", "unknown")
                                result_text = str(content.get("output", ""))
                            else:
                                result_text = str(content)
                        except Exception:
                            result_text = str(content)

                        if len(result_text) > 500:
                            result_text = result_text[:500] + "\n... (truncated)"

                        self.io.render_tool_end(name, result_text, status)

                final_content = "".join(accumulated_text)
                if self._thinking_started:
                    self.io.info("")
                    self._thinking_started = False

                if not final_content:
                    # Fallback if streaming failed to capture or model didn't stream
                    # Run invoke to get it (it will be cached or fast-ish if deterministic?) No.
                    # Just run invoke if empty.
                    logger.warning("Streaming yielded no content, running invoke...")
                    result = await agent.ainvoke({"messages": messages})
                    last_msg_content = result["messages"][-1].content
                    if isinstance(last_msg_content, list):
                        final_content = ""
                        for part in last_msg_content:
                            if isinstance(part, dict) and part.get("type") == "text":
                                final_content += part.get("text", "")
                            elif isinstance(part, str):
                                final_content += part
                    else:
                        final_content = str(last_msg_content)

            except Exception as e:
                logger.error(f"Error during agent execution: {e}")
                raise AgentError(f"Agent execution failed: {e}")

            try:
                response = None
                if final_response_data:
                    try:
                        response = LegoAgentResponse(
                            status=final_response_data.get("status"),
                            questions=final_response_data.get("questions", []) or [],
                            yaml_config=final_response_data.get("yaml_config"),
                        )
                        response.validate()
                    except Exception as e:
                        self.io.render_error(f"Response validation failed: {e}")

                if not response:
                    response = parse_lego_agent_response(final_content)
            except ValueError as e:
                # SDS-REVIEW: Logic - Repair logic should be encapsulated in `_repair_response()`.
                # Attempt repair
                self.io.render_error(f"Parsing failed, attempting repair... {e}")
                repair_msg_text = self.prompt_loader.render(
                    "lego_agent/repair.jinja2", error=str(e), raw_response=final_content
                )

                # Append repair message to history (simulated by extending messages)
                messages.append(AIMessage(content=final_content))
                messages.append(HumanMessage(content=repair_msg_text))

                result = await agent.ainvoke({"messages": messages})
                last_msg_content = result["messages"][-1].content
                if isinstance(last_msg_content, list):
                    final_content = ""
                    for part in last_msg_content:
                        if isinstance(part, dict) and part.get("type") == "text":
                            final_content += part.get("text", "")
                        elif isinstance(part, str):
                            final_content += part
                else:
                    final_content = str(last_msg_content)

                self.io.info("")
                response = parse_lego_agent_response(final_content)

            if response.status == "clarify":
                self.io.render_info("Agent needs clarification:")
                answers = await self.io.ask_questions(response.questions)
                # Store Q&A
                for q, a in zip(response.questions, answers):
                    qa_pairs.append((q, a))

            elif response.status == "ready":
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
                    f"import sys\n"
                    f"from pathlib import Path\n"
                    f"from lego_agent.runtime import run_yaml\n\n"
                    f"MAX_ITERATIONS = {self.loop_bound}\n\n"
                    f"if __name__ == '__main__':\n"
                    f"    config_path = '{config_path}'\n"
                    f"    run_yaml(config_path)\n"
                )

                script_path = self.storage.write_script(launcher_script)

                return LegoAgentResult(
                    script_path=script_path,
                    config_path=config_path,
                    script_text=launcher_script,
                    clarifications=qa_pairs,
                )

        raise AgentError("Unreachable code")

    def _validate_config(self, yaml_text: str) -> None:
        """Validate the generated YAML config."""
        try:
            config = yaml.safe_load(yaml_text)
            if not isinstance(config, dict):
                raise ValueError("YAML must be a dictionary")
            if "workflow" not in config:
                raise ValueError("YAML must contain 'workflow' key")
        except yaml.YAMLError as e:
            raise ValueError(f"Invalid YAML: {e}")
        except Exception as e:
            raise ValueError(f"Config validation failed: {e}")
