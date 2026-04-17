"""Factory for creating operator instances based on runtime configuration."""

import importlib
from typing import Any

from app_operator.core import Config
from app_operator.operator_base import OperatorBase


def create_operator(shared_kwargs: dict[str, Any], config: Config) -> OperatorBase:
    """Create the appropriate operator instance based on the runtime configuration.

    Runtime modules are loaded via ``importlib`` so that this module does not
    introduce any static import-graph edges to the concrete runtime packages.
    This keeps the ``app_operator.commands`` import graph free of references
    to ``app_operator.cli_agent``, satisfying the arch contract.

    Args:
        shared_kwargs: Keyword arguments passed to the operator constructor
            (repo_path, health_check_interval, health_check_max_count,
            max_deployment_attempts, config).
        config: The loaded operator configuration.

    Returns:
        OperatorBase: An operator instance for the configured runtime.
    """
    impl = config.runtime.impl

    if impl == "pydantic_ai":
        pai_mod = importlib.import_module("app_operator.pydantic_ai")
        return pai_mod.PydanticAIOperator(**shared_kwargs)

    # Fall through to the default cli_agent runtime.
    cli_mod = importlib.import_module("app_operator.cli_agent")
    agent = cli_mod.create_agent_from_config(shared_kwargs["repo_path"], config=config)
    return cli_mod.AppOperator(**shared_kwargs, agent=agent)
