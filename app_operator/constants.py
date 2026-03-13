"""Shared constants for app_operator."""

DEPLOYMENT_PROGRESS_FILENAME = "deployment_progress.md"

# LangGraph recursion limits
LANGGRAPH_OUTER_RECURSION_LIMIT = 10000  # outer operator graph (analyze→generate→deploy→fix→monitor)
LANGGRAPH_AGENT_RECURSION_LIMIT = 10000  # inner react agents (fix, analyze, script, subagent)
