"""Factory for creating operator instances based on runtime configuration."""

import importlib

from app_operator.config import Config
from app_operator.operator_base import OperatorBase


def create_operator(shared_kwargs: dict, config: Config) -> OperatorBase:
    """Create the appropriate operator instance based on the runtime configuration.

    Runtime modules are loaded via ``importlib`` so that this module does not
    introduce any static import-graph edges to the concrete runtime packages.
    This keeps the ``app_operator.commands`` import graph free of references
    to ``app_operator.cli_agent``, ``app_operator.langgraph``, and
    ``app_operator.adk``, satisfying the arch contract.

    Args:
        shared_kwargs: Keyword arguments passed to the operator constructor
            (repo_path, health_check_interval, health_check_max_count,
            max_deployment_attempts, config).
        config: The loaded operator configuration.

    Returns:
        OperatorBase: An operator instance for the configured runtime.
    """
    impl = config.runtime.impl

    if impl == "langgraph":
        lg_mod = importlib.import_module("app_operator.langgraph")
        return lg_mod.LangGraphOperator(**shared_kwargs)

    if impl == "adk":
        adk_mod = importlib.import_module("app_operator.adk")
        return adk_mod.AdkOperator(**shared_kwargs)

    # Default: cli_agent
    cli_mod = importlib.import_module("app_operator.cli_agent")
    agent = cli_mod.create_agent_from_config(shared_kwargs["repo_path"], config=config)
    return cli_mod.AppOperator(**shared_kwargs, agent=agent)


def create_tui_app(shared_kwargs: dict, config: Config) -> int:
    """Create and run a TUI application for the cli_agent runtime.

    Args:
        shared_kwargs: Keyword arguments for the AppOperator constructor.
        config: The loaded operator configuration.

    Returns:
        Exit code from the TUI application.
    """
    ui_mod = importlib.import_module("app_operator.ui.textual_tui")
    cli_mod = importlib.import_module("app_operator.cli_agent")

    def op_factory(ui):
        agent = cli_mod.create_agent_from_config(shared_kwargs["repo_path"], config=config)
        return cli_mod.AppOperator(**shared_kwargs, agent=agent, ui=ui)

    app = ui_mod.OperatorTUI(op_factory)
    app.run()
    return app._exit_code
