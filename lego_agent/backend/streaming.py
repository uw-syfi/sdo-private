"""Shared streaming and content-extraction utilities.

Used by both ``lego_agent.backend.runtime.LangGraphAgent`` and
``lego_agent.backend.engine.LegoAgentEngine`` to avoid duplicating the
chunk-content parsing and tool-result extraction logic.
"""

import ast
import json
from typing import Any

from loguru import logger

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
        parts: list[Any] = content  # pyright: ignore[reportUnknownVariableType]
        for part in parts:
            if isinstance(part, dict):
                part_dict: dict[str, Any] = part  # pyright: ignore[reportUnknownVariableType]
                if part_dict.get("type") == "text":
                    text += str(part_dict.get("text", ""))
                elif part_dict.get("type") == "thinking":
                    text += str(part_dict.get("thinking", ""))
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
    status: str = "unknown"
    result_text: str = ""

    content: Any = getattr(output, "content", output)

    try:
        if isinstance(content, str):
            content_dict: Any = None
            try:
                content_dict = json.loads(content)
            except json.JSONDecodeError:
                try:
                    content_dict = ast.literal_eval(content)
                except (ValueError, SyntaxError):
                    content_dict = None

            if isinstance(content_dict, dict):
                cd: dict[str, Any] = content_dict  # pyright: ignore[reportUnknownVariableType]
                status = str(cd.get("status", "unknown"))
                result_text = str(cd.get("output", ""))
            else:
                result_text = content
        elif isinstance(content, dict):
            cd2: dict[str, Any] = content  # pyright: ignore[reportUnknownVariableType]
            status = str(cd2.get("status", "unknown"))
            result_text = str(cd2.get("output", ""))
        else:
            result_text = str(content)
    except (TypeError, AttributeError) as e:
        logger.debug("Failed to parse tool result: %s", e)
        result_text = str(content)  # pyright: ignore[reportUnknownArgumentType]

    if len(result_text) > max_length:
        result_text = result_text[:max_length] + "\n... (truncated)"

    return status, result_text
