from unittest.mock import MagicMock, patch
from lego_agent.runtime import create_agent


@patch("lego_agent.runtime.load_config")
@patch("lego_agent.runtime.build_llm")
@patch("lego_agent.runtime.build_tools")
@patch("lego_agent.runtime.RealFilesystem")
@patch("lego_agent.runtime.LangGraphAgent")
def test_create_agent_with_tools_filtering(
    MockAgent, MockFS, MockBuildTools, MockBuildLLM, MockLoadConfig
):
    # Setup mocks
    mock_tools = []
    tool_names = ["read_file", "write_file", "list_files"]
    for name in tool_names:
        t = MagicMock()
        t.__name__ = name
        mock_tools.append(t)

    # Add an extra tool that should be filtered out
    extra_tool = MagicMock()
    extra_tool.__name__ = "unused_tool"
    mock_tools.append(extra_tool)

    MockBuildTools.return_value = mock_tools

    # Test create_agent with specific tools
    create_agent(tools=["read_file", "write_file"])

    # Verify LangGraphAgent initialized with correct tools
    _, kwargs = MockAgent.call_args
    assert len(kwargs["tools"]) == 2
    names = [t.__name__ for t in kwargs["tools"]]
    assert "read_file" in names
    assert "write_file" in names
    assert "list_files" not in names
    assert "unused_tool" not in names


@patch("lego_agent.runtime.load_config")
@patch("lego_agent.runtime.build_llm")
@patch("lego_agent.runtime.build_tools")
@patch("lego_agent.runtime.RealFilesystem")
@patch("lego_agent.runtime.LangGraphAgent")
def test_create_agent_default_tools(
    MockAgent, MockFS, MockBuildTools, MockBuildLLM, MockLoadConfig
):
    # Setup mocks
    mock_tools = [MagicMock(), MagicMock()]
    MockBuildTools.return_value = mock_tools

    # Test create_agent without tools arg
    create_agent()

    # Verify LangGraphAgent initialized with all tools
    _, kwargs = MockAgent.call_args
    assert len(kwargs["tools"]) == 2
    assert kwargs["tools"] == mock_tools
