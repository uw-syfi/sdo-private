import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal


@dataclass
class LegoAgentResponse:
    """Structured response from the agent."""

    status: Literal["clarify", "ready"]
    questions: list[str] = field(default_factory=list)
    yaml_config: str | None = None

    def validate(self) -> None:
        """Validate the response consistency."""
        if self.status == "clarify":
            if not self.questions:
                raise ValueError("Status is 'clarify' but no questions provided.")
        elif self.status == "ready":
            if not self.yaml_config:
                raise ValueError("Status is 'ready' but no yaml_config provided.")
        else:
            raise ValueError(f"Invalid status: {self.status}")


@dataclass
class LegoAgentResult:
    """Result of an LegoAgent run."""

    script_path: Path
    config_path: Path
    script_text: str
    clarifications: list[tuple[str, str]]


def extract_json(text: str) -> str:
    """Extract JSON from text, handling markdown fences."""
    # SDS-REVIEW: Architecture - Utility function in models module.
    # Suggest moving to `lego_agent.utils` or `lego_agent.parsing`.
    # Try to find ```json ... ``` or just ``` ... ```
    pattern = r"```(?:json)?\s*(.*?)\s*```"
    match = re.search(pattern, text, re.DOTALL)

    if match:
        candidate = match.group(1)
        try:
            json.loads(candidate)
            return candidate
        except json.JSONDecodeError:
            # Regex match is not valid JSON; fall through to brace-slice fallback
            pass

    # Fallback: look for the first { and last }
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1:
        candidate = text[start: end + 1]
        try:
            json.loads(candidate)
            return candidate
        except json.JSONDecodeError:
            # Brace-slice is also not valid JSON; return best-effort text below
            pass

    # If we get here, neither method produced valid JSON.
    # Return the regex match if it existed (most specific),
    # otherwise the brace slice, otherwise the original text.
    if match:
        return match.group(1)
    if start != -1 and end != -1:
        return text[start: end + 1]

    return text


def parse_lego_agent_response(text: str) -> LegoAgentResponse:
    """Parse agent response text into LegoAgentResponse."""
    json_text = extract_json(text)
    try:
        data = json.loads(json_text)
    except json.JSONDecodeError as e:
        raise ValueError(f"Failed to parse JSON response: {e}\nRaw text: {text}")

    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object, got {type(data).__name__}: {json_text}")

    response = LegoAgentResponse(
        status=data.get("status"),
        questions=data.get("questions", []),
        yaml_config=data.get("yaml_config"),
    )
    response.validate()
    return response
