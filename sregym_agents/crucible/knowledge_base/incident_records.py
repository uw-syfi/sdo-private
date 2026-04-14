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
class OriginalRunRecord:
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
    """Raw benchmark/judge result block recorded for the original diagnosis."""
    stage_outputs: str = ""
    """Rendered diagnosis stage outputs captured during the original run."""

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
    ) -> OriginalRunRecord:
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
            "## Final Diagnosis\n"
            + f"Answer: {self.agent_answer or '(none)'}\n\n"
            + f"Justification: {self.agent_justification or '(none)'}\n\n"
            + f"Causal Chain: {self.agent_causal_chain or '(none)'}",
            "## Benchmark Result\n" + (self.benchmark_block.strip() or "(none)"),
            "## Diagnosis Stage Outputs\n" + (self.stage_outputs or "(none)"),
        ]
        return f"---\n{_render_front_matter(meta)}\n---\n\n# Original Diagnosis Run\n\n" + "\n\n".join(sections) + "\n"


@dataclass
class GroundedRunRecord:
    problem_id: str
    """Benchmark/problem identifier for the incident."""
    app_name: str
    """Application name associated with the incident."""
    namespace: str
    """Kubernetes namespace investigated during the grounded run."""
    has_grounded_diagnosis: bool
    """Whether grounded rediagnosis produced a concrete corrected diagnosis."""
    agent_answer: str
    """Diagnosis answer produced by the grounded run."""
    agent_justification: str
    """Justification produced by the grounded run for the corrected diagnosis."""
    agent_causal_chain: str
    """Causal chain established by the grounded run."""
    benchmark_block: str
    """Raw benchmark/oracle block associated with the grounded run."""

    def to_markdown(self) -> str:
        meta = {
            "problem_id": self.problem_id,
            "app_name": self.app_name,
            "namespace": self.namespace,
            "has_grounded_diagnosis": self.has_grounded_diagnosis,
        }
        sections = [
            "## Grounded Diagnosis\n"
            + f"Answer: {self.agent_answer or '(none)'}\n\n"
            + f"Justification: {self.agent_justification or '(none)'}\n\n"
            + f"Causal Chain: {self.agent_causal_chain or '(none)'}",
            "## Benchmark Result\n" + (self.benchmark_block.strip() or "(none)"),
        ]
        return f"---\n{_render_front_matter(meta)}\n---\n\n# Grounded Diagnosis Run\n\n" + "\n\n".join(sections) + "\n"
