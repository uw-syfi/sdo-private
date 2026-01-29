from unittest.mock import MagicMock, patch
from app_operator.adk import agent_factory


def test_build_adk_agent_wraps_async_tool():
    """Verify that async tools are wrapped directly in FunctionTool."""

    async def async_tool(x):
        return x

    mock_init = MagicMock()

    class MockFunctionToolClass:
        def __init__(self, *args, **kwargs):
            mock_init(*args, **kwargs)

    with (
        patch("app_operator.adk.agent_factory.FunctionTool", new=MockFunctionToolClass),
        patch("app_operator.adk.agent_factory.LlmAgent") as MockLlmAgent,
    ):
        agent_factory.build_adk_agent(
            "test_agent", "instruction", "model", [async_tool]
        )

        # Verify FunctionTool was initialized with the async tool
        mock_init.assert_called_with(async_tool)

        # Verify LlmAgent was initialized with the wrapped tool
        MockLlmAgent.assert_called_once()
        call_kwargs = MockLlmAgent.call_args[1]
        tools_passed = call_kwargs["tools"]
        assert len(tools_passed) == 1
        assert isinstance(tools_passed[0], MockFunctionToolClass)
        assert call_kwargs["name"] == "test_agent"
        assert call_kwargs["instruction"] == "instruction"
        assert call_kwargs["model"] == "model"


def test_build_adk_agent_wraps_sync_tool():
    """Verify that sync tools are wrapped in FunctionTool."""

    def sync_tool(x):
        return x * 2

    mock_init = MagicMock()

    class MockFunctionToolClass:
        def __init__(self, *args, **kwargs):
            mock_init(*args, **kwargs)

    with (
        patch("app_operator.adk.agent_factory.FunctionTool", new=MockFunctionToolClass),
        patch("app_operator.adk.agent_factory.LlmAgent"),
    ):
        agent_factory.build_adk_agent(
            "test_agent", "instruction", "model", [sync_tool]
        )

        # Verify FunctionTool was called with the sync tool
        mock_init.assert_called_with(sync_tool)


def test_build_adk_agent_preserves_existing_function_tools():
    """Verify that existing FunctionTool instances are passed through."""

    # Let's create a fake class for testing isinstance
    class FakeFunctionTool:
        pass

    with (
        patch("app_operator.adk.agent_factory.FunctionTool", new=FakeFunctionTool),
        patch("app_operator.adk.agent_factory.LlmAgent") as MockLlmAgent,
    ):
        existing_tool = FakeFunctionTool()

        agent_factory.build_adk_agent(
            "test_agent", "instruction", "model", [existing_tool]
        )

        # Verify LlmAgent received the existing tool exactly
        MockLlmAgent.assert_called_once()
        call_kwargs = MockLlmAgent.call_args[1]
        assert call_kwargs["tools"] == [existing_tool]


def test_build_adk_agent_mixed_tools():
    """Verify a mix of async functions, sync functions, and FunctionTools."""

    async def tool1():
        pass

    def tool2():
        pass

    class MockToolClass:
        def __init__(self, fn=None):
            self.fn = fn
            self.called_with = fn

    with (
        patch("app_operator.adk.agent_factory.FunctionTool", new=MockToolClass),
        patch("app_operator.adk.agent_factory.LlmAgent") as MockLlmAgent,
    ):
        existing_tool = MockToolClass()

        agent_factory.build_adk_agent(
            "agent", "inst", "model", [tool1, tool2, existing_tool]
        )

        call_kwargs = MockLlmAgent.call_args[1]
        tools_arg = call_kwargs["tools"]
        assert len(tools_arg) == 3

        # 1. Async tool
        assert isinstance(tools_arg[0], MockToolClass)
        assert tools_arg[0].fn == tool1

        # 2. Sync tool
        assert isinstance(tools_arg[1], MockToolClass)
        assert tools_arg[1].fn == tool2

        # 3. Existing tool
        assert tools_arg[2] is existing_tool
