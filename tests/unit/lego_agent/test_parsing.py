import pytest

from lego_agent.models import parse_lego_agent_response


def test_extract_json_from_markdown():
    text = """
    Here is the JSON:
    ```json
    {
        "status": "ready",
        "yaml_config": "workflow: ..."
    }
    ```
    """
    response = parse_lego_agent_response(text)
    assert response.status == "ready"
    assert response.yaml_config == "workflow: ..."


def test_extract_json_raw():
    text = """
    {
        "status": "clarify",
        "questions": ["Why?"]
    }
    """
    response = parse_lego_agent_response(text)
    assert response.status == "clarify"
    assert response.questions == ["Why?"]


def test_validate_clarify_no_questions():
    text = '{"status": "clarify"}'
    with pytest.raises(ValueError, match="no questions provided"):
        parse_lego_agent_response(text)


def test_validate_ready_no_config():
    text = '{"status": "ready"}'
    with pytest.raises(ValueError, match="no yaml_config provided"):
        parse_lego_agent_response(text)


def test_invalid_json():
    text = "Not JSON"
    with pytest.raises(ValueError, match="Failed to parse JSON"):
        parse_lego_agent_response(text)


def test_extract_json_with_nested_backticks():
    """Test that JSON extraction works even if the script string contains markdown fences."""
    text = r"""
    Here is the response:
    ```json
    {
      "status": "ready",
      "yaml_config": "code: | \n  ```python\n  print('hello')\n  ```"
    }
    ```
    """
    response = parse_lego_agent_response(text)
    assert response.status == "ready"
    assert response.yaml_config is not None
    assert "```python" in response.yaml_config
