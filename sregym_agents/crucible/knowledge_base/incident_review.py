"""Structured models for incident review and KB curation."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

FailureMode = Literal[
    "triage_failure",
    "missing_playbook",
    "retrieval_failure",
    "playbook_validation_failure",
]
RecommendedAction = Literal["add_playbook", "merge_playbooks", "reject_playbook"]


class ReviewDecision(BaseModel):
    primary_failure_mode: FailureMode
    failure_modes: list[FailureMode] = []
    relevant_root_cause_slug: str | None = None
    relevant_existing_playbooks: list[str] = Field(default_factory=list)
    recommended_action: RecommendedAction
    target_slugs: list[str] = Field(default_factory=list)
    rejection_reason: str | None = None
    reasoning: str
    supporting_evidence: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_action_consistency(self) -> ReviewDecision:
        if (
            self.primary_failure_mode in {"retrieval_failure", "playbook_validation_failure"}
            and not self.relevant_existing_playbooks
        ):
            raise ValueError(f"{self.primary_failure_mode} requires at least one relevant existing playbook")

        if self.recommended_action == "add_playbook":
            if self.target_slugs:
                raise ValueError("add_playbook cannot specify target_slugs")
            if self.rejection_reason:
                raise ValueError("add_playbook cannot specify rejection_reason")

        if self.recommended_action == "merge_playbooks":
            if not self.target_slugs:
                raise ValueError("merge_playbooks requires at least one target slug")
            invalid = sorted(set(self.target_slugs) - set(self.relevant_existing_playbooks))
            if invalid:
                raise ValueError(f"target_slugs must be drawn from relevant_existing_playbooks: {invalid}")
            if self.rejection_reason:
                raise ValueError("merge_playbooks cannot specify rejection_reason")

        if self.recommended_action == "reject_playbook":
            if self.target_slugs:
                raise ValueError("reject_playbook cannot specify target_slugs")
            if not self.rejection_reason:
                raise ValueError("reject_playbook requires rejection_reason")

        return self


class DiagnosisPlaybookDraft(BaseModel):
    slug: str
    root_cause: str
    when_to_consider: list[str]
    disambiguators: list[str]
    summary: str
    triage_checks: list[str]
    fault_localization_checks: list[str]
    verification_checks: list[str]
    required_evidence: list[str]
    known_confounders: list[str] = Field(default_factory=list)


class TriageAreaCandidate(BaseModel):
    area_name: str
    hints: list[str]
    grounding: list[str] = Field(default_factory=list)


class PlaceholderResolutionRule(BaseModel):
    symbol: str
    resolution_guidance: str


def _empty_placeholder_resolution() -> list[PlaceholderResolutionRule]:
    return []


class MitigationPlaybookDraft(BaseModel):
    slug: str
    root_cause: str
    summary: str
    mitigation_procedure: list[str]
    placeholder_resolution: list[PlaceholderResolutionRule] = Field(default_factory=_empty_placeholder_resolution)
    verification_checks: list[str]
    rollback_stop_conditions: list[str] = Field(default_factory=list)
