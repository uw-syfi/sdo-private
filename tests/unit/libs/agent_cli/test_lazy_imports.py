from __future__ import annotations

import importlib
import sys


def test_agent_cli_import_does_not_require_litellm(monkeypatch) -> None:
    import libs.agent_cli as agent_cli

    monkeypatch.setitem(sys.modules, "litellm", None)
    sys.modules.pop("libs.agent_cli.llm_client", None)
    sys.modules.pop("libs.agent_cli.subagent", None)
    agent_cli.__dict__.pop("LiteLLMClient", None)
    agent_cli.__dict__.pop("call_subagent", None)
    agent_cli.__dict__.pop("litellm_call_with_retry", None)

    reloaded = importlib.reload(agent_cli)

    assert reloaded.CodexCodingAgent.__name__ == "CodexCodingAgent"
    assert "libs.agent_cli.subagent" not in sys.modules
    assert "libs.agent_cli.llm_client" not in sys.modules


def test_lazy_litellm_helpers_preserve_public_api() -> None:
    import libs.agent_cli as agent_cli
    from libs.agent_cli.llm_client import LiteLLMClient
    from libs.agent_cli.subagent import call_subagent, litellm_call_with_retry

    assert agent_cli.LiteLLMClient is LiteLLMClient
    assert agent_cli.call_subagent is call_subagent
    assert agent_cli.litellm_call_with_retry is litellm_call_with_retry
