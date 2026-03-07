from typing import TypedDict

from langchain_core.messages import BaseMessage


class OperatorState(TypedDict):
    messages: list[BaseMessage]
    repo_path: str
    attempt: int
    max_attempts: int
    analysis_done: bool
    scripts_done: bool
    deploy_result: dict | None
    health_result: dict | None
    health_verdict: dict | None
    monitor_count: int
    monitor_max: int | None
    analysis_summary: str | None
    last_fix_summary: str | None
    token_usage: dict  # {"input": int, "output": int, "total": int}
