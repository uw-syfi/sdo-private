"""Tests verifying correct template names and system/user split for all langgraph nodes."""

from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

from app_operator.config import AgentConfig, Config, DeploymentConfig
from app_operator.filesystem import InMemoryFilesystem
from app_operator.langgraph.context import NodeContext
from app_operator.langgraph.nodes.analyzer import analyze_code
from app_operator.langgraph.nodes.deployer import FixSummaryResponse, fix_errors
from app_operator.langgraph.nodes.generator import generate_scripts
from app_operator.langgraph.nodes.monitor import HealthVerdictResponse, health_check
from app_operator.langgraph.state import OperatorState
from app_operator.langgraph.utils import AgentResult
from app_operator.trajectory import NullTrajectoryRecorder
from libs.model_config import ModelConfig


def _make_state(**overrides) -> OperatorState:
    defaults = {
        "attempt": 1,
        "max_attempts": 3,
        "messages": [],
        "scripts_done": False,
        "deploy_result": None,
        "health_verdict": None,
        "analysis_done": False,
        "monitor_count": 0,
        "agent_token_usage": [],
    }
    defaults.update(overrides)
    return defaults  # type: ignore[return-value]


def _make_ctx(
    repo_path: Path,
    config: Config,
    loader=None,
    filesystem=None,
    check_shutdown=None,
    recorder=None,
) -> NodeContext:
    return NodeContext(
        repo_path=repo_path,
        filesystem=filesystem or InMemoryFilesystem(),
        loader=loader or Mock(),
        config=config,
        context_limit=10000,
        recorder=recorder or NullTrajectoryRecorder(),
        check_shutdown=check_shutdown,
    )


def _agent_result(structured=None):
    return AgentResult(text="ok", messages=[], structured=structured)


# ---------------------------------------------------------------------------
# analyze_code
# ---------------------------------------------------------------------------


class TestAnalyzeCodeTemplates:
    """Verify analyze_code renders code_analyzer system + user templates."""

    def test_renders_system_and_user_templates(self):
        repo_path = Path("/test/repo")
        config = Config(agent=AgentConfig(backend="codex", model_config=ModelConfig(provider="openai", model="m")))
        filesystem = InMemoryFilesystem()
        loader = Mock()
        loader.render.side_effect = lambda tmpl, **kw: f"rendered:{tmpl}"

        recorder = Mock(spec=NullTrajectoryRecorder())
        recorder.phase = MagicMock(return_value=MagicMock(__enter__=Mock(), __exit__=Mock()))
        ctx = _make_ctx(repo_path, config, loader=loader, filesystem=filesystem, recorder=recorder)
        ctx.invoke = Mock(return_value=_agent_result())

        analyze_code(_make_state(analysis_done=False), ctx, Mock())

        render_calls = [c[0][0] for c in loader.render.call_args_list]
        assert "code_analyzer/system.jinja2" in render_calls
        assert "code_analyzer/user.jinja2" in render_calls

    def test_system_template_passed_to_invoke(self):
        repo_path = Path("/test/repo")
        config = Config(agent=AgentConfig(backend="codex", model_config=ModelConfig(provider="openai", model="m")))
        filesystem = InMemoryFilesystem()

        def render_side_effect(tmpl, **kw):
            return f"rendered:{tmpl}"

        loader = Mock()
        loader.render.side_effect = render_side_effect

        recorder = Mock(spec=NullTrajectoryRecorder())
        recorder.phase = MagicMock(return_value=MagicMock(__enter__=Mock(), __exit__=Mock()))
        ctx = _make_ctx(repo_path, config, loader=loader, filesystem=filesystem, recorder=recorder)
        ctx.invoke = Mock(return_value=_agent_result())

        analyze_code(_make_state(analysis_done=False), ctx, Mock())

        first_invoke = ctx.invoke.call_args_list[0]
        system_arg = first_invoke[0][2]
        user_arg = first_invoke[0][3]
        assert system_arg == "rendered:code_analyzer/system.jinja2"
        assert user_arg == "rendered:code_analyzer/user.jinja2"

    def test_user_template_receives_repo_path(self):
        repo_path = Path("/test/repo")
        config = Config(agent=AgentConfig(backend="codex", model_config=ModelConfig(provider="openai", model="m")))
        filesystem = InMemoryFilesystem()

        captured_kwargs: dict = {}

        def render_side_effect(tmpl, **kw):
            if tmpl == "code_analyzer/user.jinja2":
                captured_kwargs.update(kw)
            return "rendered"

        loader = Mock()
        loader.render.side_effect = render_side_effect

        recorder = Mock(spec=NullTrajectoryRecorder())
        recorder.phase = MagicMock(return_value=MagicMock(__enter__=Mock(), __exit__=Mock()))
        ctx = _make_ctx(repo_path, config, loader=loader, filesystem=filesystem, recorder=recorder)
        ctx.invoke = Mock(return_value=_agent_result())

        analyze_code(_make_state(analysis_done=False), ctx, Mock())

        assert captured_kwargs.get("repo_path") == repo_path


# ---------------------------------------------------------------------------
# generate_scripts
# ---------------------------------------------------------------------------


class TestGenerateScriptsTemplates:
    """Verify generate_scripts uses script_generator templates and proper system/user split."""

    def test_renders_script_generator_system_template(self):
        repo_path = Path("/test/repo")
        config = Config(
            agent=AgentConfig(backend="codex", model_config=ModelConfig(provider="openai", model="m")),
            deployment=DeploymentConfig(platform="docker"),
        )
        loader = Mock()
        loader.render.return_value = "system-content"

        recorder = Mock(spec=NullTrajectoryRecorder())
        recorder.phase = MagicMock(return_value=MagicMock(__enter__=Mock(), __exit__=Mock()))
        ctx = _make_ctx(repo_path, config, loader=loader, recorder=recorder)
        ctx.invoke = Mock(return_value=_agent_result())

        with patch("app_operator.langgraph.nodes.generator.analyze_repository", return_value="ctx"):
            with patch("app_operator.langgraph.nodes.generator.create_generate_script_prompt", return_value="prompt"):
                generate_scripts(_make_state(scripts_done=False), ctx, Mock())

        render_calls = [c[0][0] for c in loader.render.call_args_list]
        assert "script_generator/system.jinja2" in render_calls

    def test_system_template_receives_platform(self):
        repo_path = Path("/test/repo")
        config = Config(
            agent=AgentConfig(backend="codex", model_config=ModelConfig(provider="openai", model="m")),
            deployment=DeploymentConfig(platform="k8s"),
        )
        captured: dict = {}

        def render_side_effect(tmpl, **kw):
            if tmpl == "script_generator/system.jinja2":
                captured.update(kw)
            return "rendered"

        loader = Mock()
        loader.render.side_effect = render_side_effect

        recorder = Mock(spec=NullTrajectoryRecorder())
        recorder.phase = MagicMock(return_value=MagicMock(__enter__=Mock(), __exit__=Mock()))
        ctx = _make_ctx(repo_path, config, loader=loader, recorder=recorder)
        ctx.invoke = Mock(return_value=_agent_result())

        with patch("app_operator.langgraph.nodes.generator.analyze_repository", return_value="ctx"):
            with patch("app_operator.langgraph.nodes.generator.create_generate_script_prompt", return_value="prompt"):
                generate_scripts(_make_state(scripts_done=False), ctx, Mock())

        assert captured.get("platform") == "k8s"

    def test_invoke_called_with_system_prompt_as_system_arg(self):
        repo_path = Path("/test/repo")
        config = Config(
            agent=AgentConfig(backend="codex", model_config=ModelConfig(provider="openai", model="m")),
            deployment=DeploymentConfig(platform="docker"),
        )
        loader = Mock()
        loader.render.return_value = "script-generator-system"

        recorder = Mock(spec=NullTrajectoryRecorder())
        recorder.phase = MagicMock(return_value=MagicMock(__enter__=Mock(), __exit__=Mock()))
        ctx = _make_ctx(repo_path, config, loader=loader, recorder=recorder)
        ctx.invoke = Mock(return_value=_agent_result())

        with patch("app_operator.langgraph.nodes.generator.analyze_repository", return_value="ctx"):
            with patch("app_operator.langgraph.nodes.generator.create_generate_script_prompt", return_value="user-p"):
                generate_scripts(_make_state(scripts_done=False), ctx, Mock())

        first_invoke = ctx.invoke.call_args_list[0]
        system_arg = first_invoke[0][2]
        user_arg = first_invoke[0][3]
        assert system_arg == "script-generator-system"
        assert user_arg == "user-p"

    def test_create_generate_script_prompt_called_without_system_prompt_kwarg(self):
        """create_generate_script_prompt must not receive system_prompt keyword argument."""
        repo_path = Path("/test/repo")
        config = Config(
            agent=AgentConfig(backend="codex", model_config=ModelConfig(provider="openai", model="m")),
            deployment=DeploymentConfig(platform="docker"),
        )
        loader = Mock()
        loader.render.return_value = "sys"

        recorder = Mock(spec=NullTrajectoryRecorder())
        recorder.phase = MagicMock(return_value=MagicMock(__enter__=Mock(), __exit__=Mock()))
        ctx = _make_ctx(repo_path, config, loader=loader, recorder=recorder)
        ctx.invoke = Mock(return_value=_agent_result())

        with patch("app_operator.langgraph.nodes.generator.analyze_repository", return_value="ctx"):
            with patch(
                "app_operator.langgraph.nodes.generator.create_generate_script_prompt", return_value="p"
            ) as mock_prompt:
                generate_scripts(_make_state(scripts_done=False), ctx, Mock())

        for call in mock_prompt.call_args_list:
            assert "system_prompt" not in call[1], "system_prompt kwarg should not be passed"


# ---------------------------------------------------------------------------
# fix_errors (repair agent)
# ---------------------------------------------------------------------------


class TestFixErrorsTemplates:
    """Verify fix_errors renders repair_agent/system.jinja2 and passes it to ctx.invoke."""

    def test_renders_repair_agent_system_template(self):
        repo_path = Path("/test/repo")
        config = Config(agent=AgentConfig(backend="codex", model_config=ModelConfig(provider="openai", model="m")))
        filesystem = InMemoryFilesystem()
        loader = Mock()
        loader.render.return_value = "repair-system"

        check_shutdown = Mock(return_value=False)
        recorder = Mock(spec=NullTrajectoryRecorder())
        recorder.end_phase = Mock()
        ctx = _make_ctx(
            repo_path, config, loader=loader, filesystem=filesystem, check_shutdown=check_shutdown, recorder=recorder
        )
        ctx.invoke = Mock(return_value=_agent_result(structured=FixSummaryResponse(summary="fixed")))

        state = _make_state(
            scripts_done=True,
            deploy_result={"exit_code": 1, "success": False},
        )

        with patch("app_operator.langgraph.nodes.deployer.prepare_error_context", return_value="err"):
            with patch("app_operator.langgraph.nodes.deployer.create_fix_prompt", return_value="fix-prompt"):
                fix_errors(state, ctx, Mock())

        render_calls = [c[0][0] for c in loader.render.call_args_list]
        assert "repair_agent/system.jinja2" in render_calls

    def test_repair_system_prompt_passed_as_system_arg_to_invoke(self):
        repo_path = Path("/test/repo")
        config = Config(agent=AgentConfig(backend="codex", model_config=ModelConfig(provider="openai", model="m")))
        filesystem = InMemoryFilesystem()
        loader = Mock()
        loader.render.return_value = "repair-system-content"

        check_shutdown = Mock(return_value=False)
        recorder = Mock(spec=NullTrajectoryRecorder())
        recorder.end_phase = Mock()
        ctx = _make_ctx(
            repo_path, config, loader=loader, filesystem=filesystem, check_shutdown=check_shutdown, recorder=recorder
        )
        ctx.invoke = Mock(return_value=_agent_result(structured=FixSummaryResponse(summary="fixed")))

        state = _make_state(
            scripts_done=True,
            deploy_result={"exit_code": 1, "success": False},
        )

        with patch("app_operator.langgraph.nodes.deployer.prepare_error_context", return_value="err"):
            with patch("app_operator.langgraph.nodes.deployer.create_fix_prompt", return_value="user-fix"):
                fix_errors(state, ctx, Mock())

        invoke_call = ctx.invoke.call_args_list[0]
        system_arg = invoke_call[0][2]
        user_arg = invoke_call[0][3]
        assert system_arg == "repair-system-content"
        assert user_arg == "user-fix"


# ---------------------------------------------------------------------------
# health_check / monitor_health nodes
# ---------------------------------------------------------------------------


class TestHealthCheckTemplates:
    """Verify health_check uses health_judge_agent templates and proper system/user split."""

    def test_renders_health_judge_system_template(self):
        state = _make_state(deploy_result={"success": True, "exit_code": 0})
        repo_path = Path("/test/repo")
        filesystem = InMemoryFilesystem()
        config = Config(
            agent=AgentConfig(backend="codex", model_config=ModelConfig(provider="openai", model="m")),
            deployment=DeploymentConfig(platform="docker"),
        )
        loader = Mock()
        loader.render.return_value = "rendered"

        recorder = Mock(spec=NullTrajectoryRecorder())
        recorder.end_phase = Mock()
        ctx = _make_ctx(repo_path, config, loader=loader, filesystem=filesystem, recorder=recorder)

        verdict = HealthVerdictResponse(healthy=True, assessment="ok", diagnosis="", script_was_fixed=False)
        with patch("app_operator.langgraph.utils.invoke_agent") as mock_invoke:
            mock_invoke.return_value = _agent_result(structured=verdict)
            health_check(state, ctx, Mock())

        render_calls = [c[0][0] for c in loader.render.call_args_list]
        assert "health_judge_agent/system.jinja2" in render_calls

    def test_renders_health_judge_user_template(self):
        state = _make_state(deploy_result={"success": True, "exit_code": 0})
        repo_path = Path("/test/repo")
        filesystem = InMemoryFilesystem()
        config = Config(
            agent=AgentConfig(backend="codex", model_config=ModelConfig(provider="openai", model="m")),
            deployment=DeploymentConfig(platform="docker"),
        )
        loader = Mock()
        loader.render.return_value = "rendered"

        recorder = Mock(spec=NullTrajectoryRecorder())
        recorder.end_phase = Mock()
        ctx = _make_ctx(repo_path, config, loader=loader, filesystem=filesystem, recorder=recorder)

        verdict = HealthVerdictResponse(healthy=True, assessment="ok", diagnosis="", script_was_fixed=False)
        with patch("app_operator.langgraph.utils.invoke_agent") as mock_invoke:
            mock_invoke.return_value = _agent_result(structured=verdict)
            health_check(state, ctx, Mock())

        render_calls = [c[0][0] for c in loader.render.call_args_list]
        assert "health_judge_agent/user.jinja2" in render_calls

    def test_health_system_prompt_passed_as_system_arg_to_invoke(self):
        state = _make_state(deploy_result={"success": True, "exit_code": 0})
        repo_path = Path("/test/repo")
        filesystem = InMemoryFilesystem()
        config = Config(
            agent=AgentConfig(backend="codex", model_config=ModelConfig(provider="openai", model="m")),
            deployment=DeploymentConfig(platform="docker"),
        )

        def render_side_effect(tmpl, **kw):
            if tmpl == "health_judge_agent/system.jinja2":
                return "health-system"
            return "health-user"

        loader = Mock()
        loader.render.side_effect = render_side_effect

        recorder = Mock(spec=NullTrajectoryRecorder())
        recorder.end_phase = Mock()
        ctx = _make_ctx(repo_path, config, loader=loader, filesystem=filesystem, recorder=recorder)

        verdict = HealthVerdictResponse(healthy=True, assessment="ok", diagnosis="", script_was_fixed=False)
        with patch("app_operator.langgraph.utils.invoke_agent") as mock_invoke:
            mock_invoke.return_value = _agent_result(structured=verdict)
            health_check(state, ctx, Mock())

        # ctx.invoke wraps invoke_agent; check what was passed to invoke_agent
        invoke_call = mock_invoke.call_args_list[0]
        system_arg = invoke_call[0][2]
        user_arg = invoke_call[0][3]
        assert system_arg == "health-system"
        assert user_arg == "health-user"

    def test_user_template_receives_repo_path_and_platform(self):
        state = _make_state(deploy_result={"success": True, "exit_code": 0})
        repo_path = Path("/test/repo")
        filesystem = InMemoryFilesystem()
        config = Config(
            agent=AgentConfig(backend="codex", model_config=ModelConfig(provider="openai", model="m")),
            deployment=DeploymentConfig(platform="k8s"),
        )

        captured: dict = {}

        def render_side_effect(tmpl, **kw):
            if tmpl == "health_judge_agent/user.jinja2":
                captured.update(kw)
            return "rendered"

        loader = Mock()
        loader.render.side_effect = render_side_effect

        recorder = Mock(spec=NullTrajectoryRecorder())
        recorder.end_phase = Mock()
        ctx = _make_ctx(repo_path, config, loader=loader, filesystem=filesystem, recorder=recorder)

        verdict = HealthVerdictResponse(healthy=True, assessment="ok", diagnosis="", script_was_fixed=False)
        with patch("app_operator.langgraph.utils.invoke_agent") as mock_invoke:
            mock_invoke.return_value = _agent_result(structured=verdict)
            health_check(state, ctx, Mock())

        assert captured.get("repo_path") == repo_path
        assert captured.get("platform") == "k8s"


# ---------------------------------------------------------------------------
# invoke_agent message construction (utils.py)
# ---------------------------------------------------------------------------


class TestInvokeAgentMessageConstruction:
    """Verify invoke_agent builds the correct message sequence."""

    def test_non_empty_system_prompt_creates_system_and_human_messages(self):
        from langchain_core.messages import HumanMessage, SystemMessage

        from app_operator.langgraph.utils import invoke_agent

        state: dict = {}
        mock_agent = MagicMock()
        mock_agent.stream.return_value = []

        invoke_agent(state, mock_agent, "sys-prompt", "user-prompt", agent_name="Test")  # type: ignore[arg-type]

        call_args = mock_agent.stream.call_args[0][0]
        msgs = call_args["messages"]
        assert isinstance(msgs[0], SystemMessage)
        assert msgs[0].content == "sys-prompt"
        assert isinstance(msgs[1], HumanMessage)
        assert msgs[1].content == "user-prompt"

    def test_empty_system_prompt_creates_only_human_message(self):
        from langchain_core.messages import HumanMessage

        from app_operator.langgraph.utils import invoke_agent

        state: dict = {}
        mock_agent = MagicMock()
        mock_agent.stream.return_value = []

        invoke_agent(state, mock_agent, "", "user-only", agent_name="Test")  # type: ignore[arg-type]

        call_args = mock_agent.stream.call_args[0][0]
        msgs = call_args["messages"]
        assert len(msgs) == 1
        assert isinstance(msgs[0], HumanMessage)
        assert msgs[0].content == "user-only"

    def test_prior_messages_appended_with_human_message(self):
        from langchain_core.messages import AIMessage, HumanMessage

        from app_operator.langgraph.utils import invoke_agent

        state: dict = {}
        mock_agent = MagicMock()
        mock_agent.stream.return_value = []

        prior = [HumanMessage(content="prior1"), AIMessage(content="response1")]
        invoke_agent(state, mock_agent, "", "new-prompt", prior_messages=prior)  # type: ignore[arg-type]

        call_args = mock_agent.stream.call_args[0][0]
        msgs = call_args["messages"]
        assert len(msgs) == 3
        assert msgs[0].content == "prior1"
        assert msgs[1].content == "response1"
        assert isinstance(msgs[2], HumanMessage)
        assert msgs[2].content == "new-prompt"
