"""Shared constants for app_operator."""

FIX_SUMMARY_FILENAME = "fix_summary.md"

# LangGraph recursion limits
LANGGRAPH_OUTER_RECURSION_LIMIT = 50   # outer operator graph (analyze→generate→deploy→fix→monitor)
LANGGRAPH_AGENT_RECURSION_LIMIT = 10000  # inner react agents (fix, analyze, script, subagent)
