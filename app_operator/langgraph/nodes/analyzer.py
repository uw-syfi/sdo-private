from app_operator.langgraph.context import NodeContext
from app_operator.langgraph.state import OperatorState
from app_operator.langgraph.utils import invoke_agent
from app_operator.logger import logger
from app_operator.progress import emit_progress
from app_operator.trajectory import Phase

logger = logger.bind(node="analyzer")


def analyze_code(state: OperatorState, ctx: NodeContext) -> OperatorState:
    if state["analysis_done"]:
        return state

    with ctx.recorder.phase(Phase.EXPLORATION):
        # Check if analysis files already exist
        sds_dir = ctx.repo_path / ".sds"
        analysis_file = sds_dir / "code_analysis.md"
        issues_file = sds_dir / "deployment_issues.md"

        if ctx.filesystem.exists(analysis_file) and ctx.filesystem.exists(issues_file):
            logger.info("Code analysis files already exist. Skipping analysis.")
            state["analysis_done"] = True
            return state

        emit_progress("code_analysis")
        system_prompt = ctx.loader.render("code_analyzer/system.jinja2")
        user_prompt = ctx.loader.render("code_analyzer/user.jinja2", repo_path=ctx.repo_path)

        result = invoke_agent(
            state,
            ctx.analyze_agent,
            system_prompt,
            user_prompt,
            agent_name="Code Analyzer",
            context_limit=ctx.context_limit,
            recorder=ctx.recorder,
            logger=logger,
        )

        state["analysis_done"] = True
        state["messages"] = result.messages
        return state
