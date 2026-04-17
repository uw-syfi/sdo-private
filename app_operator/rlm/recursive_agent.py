import importlib

RecursiveDeploymentAgent = importlib.import_module(
    "app_operator.cli_agent.rlm.recursive_agent"
).RecursiveDeploymentAgent

__all__ = ["RecursiveDeploymentAgent"]
