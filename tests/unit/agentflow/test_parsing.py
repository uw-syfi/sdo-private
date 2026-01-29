import pytest
from agentflow.models import parse_agentflow_response


def test_extract_json_from_markdown():
    text = """
    Here is the JSON:
    ```json
    {
        "status": "ready",
        "python_script": "print('hello')"
    }
    ```
    """
    response = parse_agentflow_response(text)
    assert response.status == "ready"
    assert response.python_script == "print('hello')"


def test_extract_json_raw():
    text = """
    {
        "status": "clarify",
        "questions": ["Why?"]
    }
    """
    response = parse_agentflow_response(text)
    assert response.status == "clarify"
    assert response.questions == ["Why?"]


def test_validate_clarify_no_questions():
    text = '{"status": "clarify"}'
    with pytest.raises(ValueError, match="no questions provided"):
        parse_agentflow_response(text)


def test_validate_ready_no_script():
    text = '{"status": "ready"}'
    with pytest.raises(ValueError, match="no python_script provided"):
        parse_agentflow_response(text)


def test_invalid_json():
    text = "Not JSON"
    with pytest.raises(ValueError, match="Failed to parse JSON"):
        parse_agentflow_response(text)

def test_extract_json_with_nested_backticks():
    """Test that JSON extraction works even if the script string contains markdown fences."""
    text = r"""
    Here is the response:
    ```json
    {
      "status": "ready",
      "python_script": "code = code.split(\"```\")[0]"
    }
    ```
    """
    response = parse_agentflow_response(text)
    assert response.status == "ready"
    assert 'code.split("```")' in response.python_script