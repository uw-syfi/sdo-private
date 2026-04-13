"""Structured output models for pydantic_ai operator."""

from pydantic import BaseModel, Field


class FixSummaryResponse(BaseModel):
    summary: str = Field(description="Brief summary of issues found and fixes applied")


class HealthVerdictResponse(BaseModel):
    healthy: bool = Field(description="Whether the application is healthy")
    assessment: str = Field(description="What the script reported vs what was independently observed")
    diagnosis: str = Field(description="If unhealthy: symptoms and root causes. If healthy: empty string")
    script_was_fixed: bool = Field(description="Whether the health_check.sh script was modified")
    false_negative_suspected: bool = Field(
        default=False,
        description="Whether a false-negative health verdict is suspected",
    )
