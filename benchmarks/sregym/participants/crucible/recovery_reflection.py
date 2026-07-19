"""Structured grounded recovery reflection shared by orchestrator and KB worker."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class RecoveryStageFailure(BaseModel):
    """One grounded failure mode identified from the original run."""

    stage: Literal["triage", "retrieval", "verification"]
    description: str = Field(description="What the original agent got wrong at this stage.")
    evidence: str = Field(description="Grounded evidence from the recovery investigation supporting this failure.")
    lesson: str = Field(description="Generic lesson that should improve priors for future runs.")


class RecoveryReflection(BaseModel):
    """Grounded reflection produced after recovery diagnosis completes."""

    summary: str = Field(description="2-4 sentence narrative of why the original diagnosis failed.")
    stage_failures: list[RecoveryStageFailure] = Field(  # pyright: ignore[reportUnknownVariableType]
        default_factory=list,
        description="Failed stages in the original diagnosis flow.",
    )
    investigation_observations: list[str] = Field(
        default_factory=list,
        description="Concrete observations gathered during recovery that ground the reflection.",
    )
