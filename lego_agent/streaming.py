"""Shared streaming and content-extraction utilities.

Used by both ``lego_agent.runtime.LangGraphAgent`` and
``lego_agent.engine.LegoAgentEngine`` to avoid duplicating the
chunk-content parsing and tool-result extraction logic.
"""

import ast
import json
from typing import Any

from app_operator.logger import logger

DEFAULT_TOOL_RESULT_MAX_LENGTH = 500  # characters before truncating tool result output


def parse_chunk_content(content: Any) -> str:
    """Extract text from a streaming chunk's content field.

    Handles plain strings, lists of typed dicts (``{"type": "text", ...}``
    or ``{"type": "thinking", ...}``), and lists of plain strings.

    Returns the concatenated text.
    """
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


def extract_tool_result(
    output: Any,
    max_length: int = DEFAULT_TOOL_RESULT_MAX_LENGTH,
) -> tuple[str, str]:
    """Parse a tool-end event output into ``(status, result_text)``.

    *status* is one of ``"success"``, ``"error"``, or ``"unknown"``.
    *result_text* is the human-readable output, truncated to *max_length*.
    """
    status = "unknown"
    result_text = ""

    content = getattr(output, "content", output)

    try:
        if isinstance(content, str):
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
    except (TypeError, AttributeError) as e:
        logger.debug("Failed to parse tool result: %s", e)
        result_text = str(content)

    if len(result_text) > max_length:
        result_text = result_text[:max_length] + "\n... (truncated)"

    return status, result_text
