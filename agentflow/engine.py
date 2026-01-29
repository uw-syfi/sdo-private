import json
import ast
from pathlib import Path
from typing import List, Tuple, Any, Callable

from langchain_core.messages import HumanMessage, AIMessage
from langchain_core.tools import tool, StructuredTool
from langgraph.prebuilt import create_react_agent

from agentflow.io import UserIO, Colors
from agentflow.models import AgentflowResult, parse_agentflow_response, AgentflowResponse
from agentflow.storage import AgentflowStorage
from agentflow.prompts import PromptLoader

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


class AgentflowEngine:
    """Core logic for the Agentflow process."""

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
        self.storage = AgentflowStorage(output_dir)
        self.work_dir = work_dir
        self._thinking_started = False

    def _wrap_tool(self, func: Callable, name: str) -> StructuredTool:
        """Wrap a callable into a LangChain StructuredTool."""
        # Use the function's docstring and name
        t = tool(func)
        t.name = name  # Ensure name is set correctly if needed
        return t

    def _submit_response(self, status: str, questions: List[str] = None, python_script: str = None) -> str:
        """
        Submit the final response to the user.

        Args:
            status: 'clarify' if you have questions, or 'ready' if the script is complete.
            questions: List of questions if status is 'clarify'.
            python_script: The complete python script if status is 'ready'.
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

    async def run_async(self, user_prompt: str) -> AgentflowResult:
        """Run the clarification loop and generate the script."""
        qa_pairs: List[Tuple[str, str]] = []

        # Setup tools
        filesystem = RealFilesystem()
        context = ToolContext(repo_root=self.work_dir, filesystem=filesystem)
        
        # Build specific tools used by Agentflow (read-only mostly)
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
            system_prompt = self.prompt_loader.render("agentflow/system.jinja2")
            user_msg_text = self.prompt_loader.render(
                "agentflow/user.jinja2",
                user_prompt=user_prompt,
                qa_pairs=qa_pairs,
                loop_bound=self.loop_bound,
            )

            # Create agent graph
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
                # Use astream_events to capture thoughts and tool calls
                accumulated_text = []
                async for event in agent.astream_events(
                    {"messages": messages}, 
                    version="v1",
                    config={"recursion_limit": 50}
                ):
                    kind = event["event"]
                    
                    if kind == "on_chat_model_stream":
                        content = event["data"]["chunk"].content
                        text_chunk = self._parse_chunk_content(content)
                        
                        if text_chunk:
                            if not self._thinking_started:
                                self.io.info(f"\n{Colors.LIGHT_GRAY}[Thinking]{Colors.ENDC}")
                                self._thinking_started = True
                            self.io.print_stream(f"{Colors.LIGHT_GRAY}{text_chunk}{Colors.ENDC}")
                            accumulated_text.append(text_chunk)
                    
                    elif kind == "on_tool_start":
                        name = event["name"]
                        inputs = event["data"].get("input")
                        if name == "submit_response":
                            final_response_data = inputs
                        if self._thinking_started:
                             self.io.info("") # Newline
                             self._thinking_started = False
                        self.io.info(f"\n{Colors.BLUE}[Tool Use] {name}({inputs}){Colors.ENDC}")

                    elif kind == "on_tool_end":
                        name = event["name"]
                        output = event["data"].get("output")
                        
                        symbol = ""
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
                                    status = content_dict.get("status")
                                    if status == "success":
                                        symbol = f"{Colors.GREEN}✓{Colors.ENDC} "
                                    elif status == "error":
                                        symbol = f"{Colors.RED}✗{Colors.ENDC} "
                                    
                                    result_text = str(content_dict.get("output", ""))
                                else:
                                    result_text = content
                            elif isinstance(content, dict):
                                status = content.get("status")
                                if status == "success":
                                    symbol = f"{Colors.GREEN}✓{Colors.ENDC} "
                                elif status == "error":
                                    symbol = f"{Colors.RED}✗{Colors.ENDC} "
                                result_text = str(content.get("output", ""))
                            else:
                                result_text = str(content)
                        except Exception:
                            result_text = str(content)

                        if len(result_text) > 500:
                            result_text = result_text[:500] + "\n... (truncated)"
                        self.io.info(f"\n{Colors.BLUE}[Tool Result] {name}: {symbol}{Colors.ENDC}\n{Colors.LIGHT_GRAY}{result_text}{Colors.ENDC}")
                final_content = "".join(accumulated_text)
                if self._thinking_started:
                     self.io.print_stream(Colors.ENDC)
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
                        response = AgentflowResponse(
                            status=final_response_data.get("status"),
                            questions=final_response_data.get("questions", []) or [],
                            python_script=final_response_data.get("python_script"),
                        )
                        response.validate()
                    except Exception as e:
                        self.io.info(f"{Colors.RED}Response validation failed: {e}{Colors.ENDC}")

                if not response:
                    response = parse_agentflow_response(final_content)
            except ValueError as e:
                # Attempt repair
                self.io.info(f"{Colors.RED}Parsing failed, attempting repair...{Colors.ENDC}")
                repair_msg_text = self.prompt_loader.render(
                    "agentflow/repair.jinja2", error=str(e), raw_response=final_content
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
                response = parse_agentflow_response(final_content)

            if response.status == "clarify":
                self.io.info(f"{Colors.BOLD}{Colors.YELLOW}Agent needs clarification:{Colors.ENDC}")
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