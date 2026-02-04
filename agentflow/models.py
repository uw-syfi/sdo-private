import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Literal, Optional, Tuple


@dataclass
class AgentflowResponse:
    """Structured response from the agent."""

    status: Literal["clarify", "ready"]
    questions: List[str] = field(default_factory=list)
    python_script: Optional[str] = None

    def validate(self) -> None:
        """Validate the response consistency."""
        if self.status == "clarify":
            if not self.questions:
                raise ValueError("Status is 'clarify' but no questions provided.")
        elif self.status == "ready":
            if not self.python_script:
                raise ValueError("Status is 'ready' but no python_script provided.")
        else:
            raise ValueError(f"Invalid status: {self.status}")


@dataclass
class AgentflowResult:
    """Result of an Agentflow run."""

    script_path: Path
    script_text: str
    clarifications: List[Tuple[str, str]]


def get_agentflow_response_schema() -> dict:
    """Return the JSON schema for AgentflowResponse."""
    return {
        "type": "object",
        "properties": {
            "status": {
                "type": "string",
                "enum": ["clarify", "ready"]
            },
            "questions": {
                "type": "array",
                "items": {
                    "type": "string"
                }
            },
            "python_script": {
                "type": "string"
            }
        },
        "required": ["status"]
    }


def extract_json(text: str) -> str:
    """Extract JSON from text, handling markdown fences."""
    # Try to find ```json ... ``` or just ``` ... ```
    pattern = r"```(?:json)?\s*(.*?)\s*```"
    match = re.search(pattern, text, re.DOTALL)
    
    if match:
        candidate = match.group(1)
        try:
            json.loads(candidate)
            return candidate
        except json.JSONDecodeError:
            pass  # Try fallback if regex extraction isn't valid JSON

    # Fallback: look for the first { and last }
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1:
        candidate = text[start: end + 1]
        try:
            json.loads(candidate)
            return candidate
        except json.JSONDecodeError:
            pass

    # If we get here, neither method produced valid JSON.
    # Return the regex match if it existed (most specific),
    # otherwise the brace slice, otherwise the original text.
    if match:
        return match.group(1)
    if start != -1 and end != -1:
        return text[start: end + 1]

    return text


def parse_agentflow_response(text: str) -> AgentflowResponse:
    """Parse agent response text into AgentflowResponse."""
    json_text = extract_json(text)
    try:
        data = json.loads(json_text)
    except json.JSONDecodeError as e:
        raise ValueError(f"Failed to parse JSON response: {e}\nRaw text: {text}")

    response = AgentflowResponse(
        status=data.get("status"),
        questions=data.get("questions", []),
        python_script=data.get("python_script"),
    )
    response.validate()
    return response
