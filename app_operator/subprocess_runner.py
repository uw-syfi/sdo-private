import importlib

SubprocessRunner = importlib.import_module("app_operator.cli_agent.subprocess_runner").SubprocessRunner

__all__ = ["SubprocessRunner"]
