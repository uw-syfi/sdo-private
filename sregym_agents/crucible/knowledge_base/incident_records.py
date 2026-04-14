"""Rendering for incident record markdown files used by the KB pipeline."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


def _render_front_matter(meta: dict[str, str | bool]) -> str:
    return "\n".join(f"{k}: {json.dumps(v) if isinstance(v, bool) else v}" for k, v in meta.items())


@dataclass
class DiagnosisRunRecord:
    problem_id: str
    """Benchmark/problem identifier for the incident."""
    app_name: str
    """Application name associated with the incident."""
    namespace: str
    """Kubernetes namespace investigated during the original run."""
    diagnosis_succeeded: bool
    """Whether the benchmark accepted the original diagnosis submission."""
    agent_answer: str
    """Diagnosis answer submitted by the original workflow."""
    agent_justification: str
    """Justification produced by the original workflow for its diagnosis."""
    agent_causal_chain: str
    """Causal chain produced by the original workflow, if any."""
    benchmark_block: str
    """Raw benchmark/judge result block recorded for the diagnosis."""
    stage_outputs: str = ""
    """Rendered diagnosis-stage transcript captured during the run."""

    @classmethod
    def from_stage_outputs_file(
        cls,
        *,
        problem_id: str,
        app_name: str,
        namespace: str,
        diagnosis_succeeded: bool,
        agent_answer: str,
        agent_justification: str,
        agent_causal_chain: str,
        benchmark_block: str,
        stage_outputs_file: Path | None,
    ) -> DiagnosisRunRecord:
        stage_outputs = (
            stage_outputs_file.read_text().strip() if stage_outputs_file and stage_outputs_file.exists() else ""
        )
        return cls(
            problem_id=problem_id,
            app_name=app_name,
            namespace=namespace,
            diagnosis_succeeded=diagnosis_succeeded,
            agent_answer=agent_answer,
            agent_justification=agent_justification,
            agent_causal_chain=agent_causal_chain,
            benchmark_block=benchmark_block,
            stage_outputs=stage_outputs,
        )

    def to_markdown(self) -> str:
        meta = {
            "problem_id": self.problem_id,
            "app_name": self.app_name,
            "namespace": self.namespace,
            "diagnosis_succeeded": self.diagnosis_succeeded,
        }
        sections = [
            "## Diagnosis\n"
            + f"Answer: {self.agent_answer or '(none)'}\n\n"
            + f"Justification: {self.agent_justification or '(none)'}\n\n"
            + f"Causal Chain: {self.agent_causal_chain or '(none)'}",
            "## Benchmark Result\n" + (self.benchmark_block.strip() or "(none)"),
            "## Diagnosis Stage Outputs\n" + (self.stage_outputs or "(none)"),
        ]
        return f"---\n{_render_front_matter(meta)}\n---\n\n# Diagnosis Run\n\n" + "\n\n".join(sections) + "\n"


@dataclass
class RecoveryDiagnosisRunRecord:
    problem_id: str
    """Benchmark/problem identifier for the incident."""
    app_name: str
    """Application name associated with the incident."""
    namespace: str
    """Kubernetes namespace investigated during the recovery diagnosis run."""
    has_recovery_diagnosis: bool
    """Whether recovery rediagnosis produced a concrete corrected diagnosis."""
    agent_answer: str
    """Diagnosis answer produced by the recovery run."""
    agent_justification: str
    """Justification produced by the recovery run for the corrected diagnosis."""
    agent_causal_chain: str
    """Causal chain established by the recovery run."""
    benchmark_block: str
    """Raw benchmark/oracle block associated with the recovery run."""
    stage_outputs: str = ""
    """Rendered recovery-stage transcript captured during the recovery run."""

    @classmethod
    def from_stage_outputs_file(
        cls,
        *,
        problem_id: str,
        app_name: str,
        namespace: str,
        has_recovery_diagnosis: bool,
        agent_answer: str,
        agent_justification: str,
        agent_causal_chain: str,
        benchmark_block: str,
        stage_outputs_file: Path | None,
    ) -> RecoveryDiagnosisRunRecord:
        stage_outputs = (
            stage_outputs_file.read_text().strip() if stage_outputs_file and stage_outputs_file.exists() else ""
        )
        return cls(
            problem_id=problem_id,
            app_name=app_name,
            namespace=namespace,
            has_recovery_diagnosis=has_recovery_diagnosis,
            agent_answer=agent_answer,
            agent_justification=agent_justification,
            agent_causal_chain=agent_causal_chain,
            benchmark_block=benchmark_block,
            stage_outputs=stage_outputs,
        )

    def to_markdown(self) -> str:
        meta = {
            "problem_id": self.problem_id,
            "app_name": self.app_name,
            "namespace": self.namespace,
            "has_recovery_diagnosis": self.has_recovery_diagnosis,
        }
        sections = [
            "## Recovery Diagnosis\n"
            + f"Answer: {self.agent_answer or '(none)'}\n\n"
            + f"Justification: {self.agent_justification or '(none)'}\n\n"
            + f"Causal Chain: {self.agent_causal_chain or '(none)'}",
            "## Benchmark Result\n" + (self.benchmark_block.strip() or "(none)"),
            "## Recovery Diagnosis Stage Outputs\n" + (self.stage_outputs or "(none)"),
        ]
        return f"---\n{_render_front_matter(meta)}\n---\n\n# Recovery Diagnosis Run\n\n" + "\n\n".join(sections) + "\n"


@dataclass
class MitigationRunRecord:
    problem_id: str
    """Benchmark/problem identifier for the incident."""
    app_name: str
    """Application name associated with the incident."""
    namespace: str
    """Kubernetes namespace investigated during the original mitigation run."""
    mitigation_succeeded: bool
    """Whether the benchmark accepted the original mitigation submission."""
    agent_answer: str
    """Mitigation answer submitted by the original workflow."""
    agent_justification: str
    """Justification produced by the original workflow for its mitigation."""
    benchmark_block: str
    """Raw benchmark/oracle block recorded for the mitigation."""
    stage_outputs: str = ""
    """Rendered mitigation-stage transcript captured during the run."""

    @classmethod
    def from_stage_outputs_file(
        cls,
        *,
        problem_id: str,
        app_name: str,
        namespace: str,
        mitigation_succeeded: bool,
        agent_answer: str,
        agent_justification: str,
        benchmark_block: str,
        stage_outputs_file: Path | None,
    ) -> MitigationRunRecord:
        stage_outputs = (
            stage_outputs_file.read_text().strip() if stage_outputs_file and stage_outputs_file.exists() else ""
        )
        return cls(
            problem_id=problem_id,
            app_name=app_name,
            namespace=namespace,
            mitigation_succeeded=mitigation_succeeded,
            agent_answer=agent_answer,
            agent_justification=agent_justification,
            benchmark_block=benchmark_block,
            stage_outputs=stage_outputs,
        )

    def to_markdown(self) -> str:
        meta = {
            "problem_id": self.problem_id,
            "app_name": self.app_name,
            "namespace": self.namespace,
            "mitigation_succeeded": self.mitigation_succeeded,
        }
        sections = [
            "## Mitigation\n"
            + f"Mitigation: {self.agent_answer or '(none)'}\n\n"
            + f"Justification: {self.agent_justification or '(none)'}",
            "## Benchmark Result\n" + (self.benchmark_block.strip() or "(none)"),
            "## Mitigation Stage Outputs\n" + (self.stage_outputs or "(none)"),
        ]
        return f"---\n{_render_front_matter(meta)}\n---\n\n# Mitigation Run\n\n" + "\n\n".join(sections) + "\n"


@dataclass
class RecoveryMitigationRunRecord:
    problem_id: str
    """Benchmark/problem identifier for the incident."""
    app_name: str
    """Application name associated with the incident."""
    namespace: str
    """Kubernetes namespace investigated during the recovery mitigation run."""
    has_recovery_mitigation: bool
    """Whether recovery remediation produced a concrete corrected mitigation."""
    agent_answer: str
    """Mitigation answer produced by the recovery run."""
    agent_justification: str
    """Justification produced by the recovery run for the corrected mitigation."""
    benchmark_block: str
    """Raw benchmark/oracle block associated with the recovery run."""
    stage_outputs: str = ""
    """Rendered recovery-stage transcript captured during the recovery run."""

    @classmethod
    def from_stage_outputs_file(
        cls,
        *,
        problem_id: str,
        app_name: str,
        namespace: str,
        has_recovery_mitigation: bool,
        agent_answer: str,
        agent_justification: str,
        benchmark_block: str,
        stage_outputs_file: Path | None,
    ) -> RecoveryMitigationRunRecord:
        stage_outputs = (
            stage_outputs_file.read_text().strip() if stage_outputs_file and stage_outputs_file.exists() else ""
        )
        return cls(
            problem_id=problem_id,
            app_name=app_name,
            namespace=namespace,
            has_recovery_mitigation=has_recovery_mitigation,
            agent_answer=agent_answer,
            agent_justification=agent_justification,
            benchmark_block=benchmark_block,
            stage_outputs=stage_outputs,
        )

    def to_markdown(self) -> str:
        meta = {
            "problem_id": self.problem_id,
            "app_name": self.app_name,
            "namespace": self.namespace,
            "has_recovery_mitigation": self.has_recovery_mitigation,
        }
        sections = [
            "## Recovery Mitigation\n"
            + f"Mitigation: {self.agent_answer or '(none)'}\n\n"
            + f"Justification: {self.agent_justification or '(none)'}",
            "## Benchmark Result\n" + (self.benchmark_block.strip() or "(none)"),
            "## Recovery Mitigation Stage Outputs\n" + (self.stage_outputs or "(none)"),
        ]
        return f"---\n{_render_front_matter(meta)}\n---\n\n# Recovery Mitigation Run\n\n" + "\n\n".join(sections) + "\n"
