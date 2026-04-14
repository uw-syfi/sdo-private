"""Structured models for incident review and KB curation."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

FailureMode = Literal[
    "triage_failure",
    "missing_playbook",
    "retrieval_failure",
    "playbook_validation_failure",
]
RecommendedAction = Literal["create_playbook", "refine_playbook", "merge_playbooks", "no_change"]


class ReviewDecision(BaseModel):
    primary_failure_mode: FailureMode
    failure_modes: list[FailureMode] = []
    relevant_root_cause_slug: str | None = None
    relevant_existing_playbooks: list[str] = Field(default_factory=list)
    recommended_action: RecommendedAction
    target_slug: str | None = None
    merge_slugs: list[str] = Field(default_factory=list)
    reasoning: str
    supporting_evidence: list[str] = Field(default_factory=list)


class DiagnosisPlaybookDraft(BaseModel):
    slug: str
    root_cause: str
    when_to_consider: list[str]
    disambiguators: list[str]
    summary: str
    triage_checks: list[str]
    verification_checks: list[str]
    required_evidence: list[str]
    known_confounders: list[str] = Field(default_factory=list)


class MitigationPlaybookDraft(BaseModel):
    slug: str
    root_cause: str
    summary: str
    mitigation_procedure: list[str]
    verification_checks: list[str]
    rollback_stop_conditions: list[str] = Field(default_factory=list)
