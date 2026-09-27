from typing import TYPE_CHECKING, Any

from .base import CodingAgent
from .claude import ClaudeCodeCodingAgent
from .codex import CodexCodingAgent
from .gemini import GeminiCodingAgent
from .mcp_config import HttpMcpServer, McpServerConfig, StdioMcpServer
from .opencode import OpencodeCodingAgent
from .sandbox import SandboxConfig

if TYPE_CHECKING:
    from .llm_client import LiteLLMClient
    from .subagent import call_subagent, litellm_call_with_retry

__all__ = [
    "CodingAgent",
    "CodexCodingAgent",
    "GeminiCodingAgent",
    "OpencodeCodingAgent",
    "ClaudeCodeCodingAgent",
    "HttpMcpServer",
    "McpServerConfig",
    "StdioMcpServer",
    "SandboxConfig",
    "call_subagent",
    "litellm_call_with_retry",
    "LiteLLMClient",
]


def __getattr__(name: str) -> Any:
    """Load optional LiteLLM-backed helpers only when callers request them."""

    if name == "LiteLLMClient":
        from .llm_client import LiteLLMClient

        globals()[name] = LiteLLMClient
        return LiteLLMClient
    if name in {"call_subagent", "litellm_call_with_retry"}:
        from .subagent import call_subagent, litellm_call_with_retry

        globals().update(
            call_subagent=call_subagent,
            litellm_call_with_retry=litellm_call_with_retry,
        )
        return globals()[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
