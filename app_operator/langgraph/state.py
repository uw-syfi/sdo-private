from typing import TypedDict, Optional, List

from langchain_core.messages import BaseMessage


class OperatorState(TypedDict):
    messages: List[BaseMessage]
    repo_path: str
    attempt: int
    max_attempts: int
    analysis_done: bool
    scripts_done: bool
    deploy_result: Optional[dict]
    health_result: Optional[dict]
    monitor_count: int
    monitor_max: Optional[int]
    analysis_summary: Optional[str]
    last_fix_summary: Optional[str]
