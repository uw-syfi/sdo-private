"""Agent factory functions for pydantic_ai operator."""

from collections.abc import Callable

from pydantic_ai import Agent

from app_operator.pydantic_ai._deps import OperatorDeps
from app_operator.pydantic_ai._responses import FixSummaryResponse, HealthVerdictResponse


def build_analyze_agent(model: str, tools: list[Callable]) -> Agent[OperatorDeps, str]:
    """Build agent for code analysis phase."""
    return Agent(
        model,
        deps_type=OperatorDeps,
        output_type=str,
        tools=tools,
    )


def build_script_agent(model: str, tools: list[Callable]) -> Agent[OperatorDeps, str]:
    """Build agent for script generation phase."""
    return Agent(
        model,
        deps_type=OperatorDeps,
        output_type=str,
        tools=tools,
    )


def build_fix_agent(model: str, tools: list[Callable]) -> Agent[OperatorDeps, FixSummaryResponse]:
    """Build agent for error fixing phase."""
    return Agent(
        model,
        deps_type=OperatorDeps,
        output_type=FixSummaryResponse,
        tools=tools,
    )


def build_health_agent(model: str, tools: list[Callable]) -> Agent[OperatorDeps, HealthVerdictResponse]:
    """Build agent for health check phase."""
    return Agent(
        model,
        deps_type=OperatorDeps,
        output_type=HealthVerdictResponse,
        tools=tools,
    )
