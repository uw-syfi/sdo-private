import pytest

from libs.agent_cli.cli_agent import CLICodingAgent
from libs.agent_cli.codex import CodexCodingAgent
from libs.agent_cli.mcp_config import HttpMcpServer, StdioMcpServer


@pytest.fixture
def mock_binaries(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mock binary discovery and CLI check."""

    def resolve_binary(cmd: str, path: str | None = None) -> str:
        del path
        return f"/usr/local/bin/{cmd}"

    def skip_check(_agent: CLICodingAgent) -> None:
        return None

    monkeypatch.setattr(
        "libs.agent_cli.cli_agent.shutil.which",
        resolve_binary,
    )
    monkeypatch.setattr(CLICodingAgent, "_check_cli", skip_check)


@pytest.fixture
def agent(mock_binaries: None) -> CodexCodingAgent:
    return CodexCodingAgent(model="test-model")


class TestCodexCommandConstruction:
    def test_command_base_flags(self, agent: CodexCodingAgent):
        cmd = agent._get_command("test")
        assert "exec" in cmd
        assert "--dangerously-bypass-approvals-and-sandbox" in cmd

    def test_command_omits_mcp_when_no_servers(self, agent: CodexCodingAgent):
        cmd = agent._get_command("test")
        assert "-c" not in cmd

    def test_mcp_http_server(self, mock_binaries: None):
        servers = [HttpMcpServer(name="srv", url="http://localhost:9000/sse")]
        agent = CodexCodingAgent(mcp_servers=servers)
        cmd = agent._get_command("test")
        assert "-c" in cmd
        idx = cmd.index("-c")
        assert cmd[idx + 1] == 'mcp_servers.srv.url="http://localhost:9000/sse"'

    def test_mcp_stdio_server(self, mock_binaries: None):
        servers = [StdioMcpServer(name="tool", command="npx", args=["-y", "pkg"])]
        agent = CodexCodingAgent(mcp_servers=servers)
        cmd = agent._get_command("test")
        c_values = [cmd[i + 1] for i, v in enumerate(cmd) if v == "-c"]
        assert 'mcp_servers.tool.command="npx"' in c_values
        assert 'mcp_servers.tool.args=["-y", "pkg"]' in c_values

    def test_mcp_stdio_server_with_env(self, mock_binaries: None):
        servers = [StdioMcpServer(name="t", command="cmd", env={"K1": "v1", "K2": "v2"})]
        agent = CodexCodingAgent(mcp_servers=servers)
        cmd = agent._get_command("test")
        c_values = [cmd[i + 1] for i, v in enumerate(cmd) if v == "-c"]
        assert 'mcp_servers.t.env.K1="v1"' in c_values
        assert 'mcp_servers.t.env.K2="v2"' in c_values

    def test_mcp_multiple_servers(self, mock_binaries: None):
        servers = [
            HttpMcpServer(name="a", url="http://a"),
            StdioMcpServer(name="b", command="cmd"),
        ]
        agent = CodexCodingAgent(mcp_servers=servers)
        cmd = agent._get_command("test")
        c_values = [cmd[i + 1] for i, v in enumerate(cmd) if v == "-c"]
        assert 'mcp_servers.a.url="http://a"' in c_values
        assert 'mcp_servers.b.command="cmd"' in c_values
