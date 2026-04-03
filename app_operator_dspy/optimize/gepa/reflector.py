"""LLM-based reflection and mutation of DSPy signature instructions.

Uses a separate LLM call to analyze execution traces from failed
deployments and propose targeted instruction improvements.
"""

from dataclasses import dataclass

import dspy

from app_operator_dspy.logger import get_logger
from app_operator_dspy.optimize.gepa.candidate import PromptCandidate

log = get_logger("gepa.reflector")


@dataclass
class ExecutionTrace:
    """Captured result from running the operator on one app."""

    app_name: str
    success: bool
    phase: str
    attempts: int
    error: str | None
    score: float


class SignatureReflector:
    """Uses an LLM to propose mutations to DSPy signature instructions."""

    def mutate(
        self,
        candidate: PromptCandidate,
        traces: list[ExecutionTrace],
    ) -> PromptCandidate:
        """Analyze failures and propose an improved instruction.

        Args:
            candidate: Current prompt candidate to improve.
            traces: Execution results from running with this candidate.

        Returns:
            New PromptCandidate with mutated instruction.
        """
        trace_summary = self._format_traces(traces)
        failures = [t for t in traces if not t.success]

        prompt = (
            "You are an expert prompt engineer optimizing a DSPy signature instruction "
            "for a deployment automation agent.\n\n"
            f"## Current Instruction\n```\n{candidate.instruction_text}\n```\n\n"
            f"## Signature: {candidate.signature_name}\n\n"
            f"## Execution Results ({len(traces)} runs, {len(failures)} failures)\n"
            f"{trace_summary}\n\n"
            "## Task\n"
            "Analyze the failure patterns and improve the instruction to increase "
            "deployment success rate. Focus on:\n"
            "- Common error patterns across failures\n"
            "- What the instruction should emphasize more strongly\n"
            "- Missing guidance that would prevent these specific failures\n"
            "- Keeping the instruction concise — don't bloat it\n\n"
            "Output ONLY the improved instruction text. No explanations, no markdown fences, "
            "no XML tags. Just the instruction."
        )

        try:
            response = dspy.settings.lm(prompt)
            mutated_text = response[0].strip() if response else candidate.instruction_text
        except Exception as e:
            log.warning("mutation LLM call failed: {}, returning parent unchanged", e)
            mutated_text = candidate.instruction_text

        rationale = self._summarize_failures(failures)

        return PromptCandidate(
            signature_name=candidate.signature_name,
            instruction_text=mutated_text,
            parent_id=candidate.id,
            generation=candidate.generation + 1,
            mutation_type="mutate",
            mutation_rationale=rationale,
        )

    def crossover(
        self,
        candidate_a: PromptCandidate,
        candidate_b: PromptCandidate,
        traces_a: list[ExecutionTrace],
        traces_b: list[ExecutionTrace],
    ) -> PromptCandidate:
        """Merge the best aspects of two candidates."""
        prompt = (
            "You are an expert prompt engineer. Merge the best parts of two DSPy "
            "signature instructions into one improved version.\n\n"
            f"## Instruction A (score: {candidate_a.overall_score:.3f})\n"
            f"```\n{candidate_a.instruction_text}\n```\n\n"
            f"## Instruction B (score: {candidate_b.overall_score:.3f})\n"
            f"```\n{candidate_b.instruction_text}\n```\n\n"
            f"## A's Results\n{self._format_traces(traces_a)}\n\n"
            f"## B's Results\n{self._format_traces(traces_b)}\n\n"
            "Combine the strengths of both instructions. Output ONLY the merged "
            "instruction text. No explanations, no markdown fences."
        )

        try:
            response = dspy.settings.lm(prompt)
            merged_text = response[0].strip() if response else candidate_a.instruction_text
        except Exception as e:
            log.warning("crossover LLM call failed: {}, returning parent A unchanged", e)
            merged_text = candidate_a.instruction_text

        return PromptCandidate(
            signature_name=candidate_a.signature_name,
            instruction_text=merged_text,
            parent_id=candidate_a.id,
            generation=max(candidate_a.generation, candidate_b.generation) + 1,
            mutation_type="crossover",
            mutation_rationale=f"Merged {candidate_a.id} and {candidate_b.id}",
        )

    def _format_traces(self, traces: list[ExecutionTrace]) -> str:
        lines = []
        for t in traces:
            status = "PASS" if t.success else "FAIL"
            line = f"- {t.app_name}: {status} (phase={t.phase}, attempts={t.attempts}, score={t.score:.3f})"
            if t.error:
                # Keep error summary short
                error_short = t.error[:200].replace("\n", " ")
                line += f"\n  Error: {error_short}"
            lines.append(line)
        return "\n".join(lines)

    def _summarize_failures(self, failures: list[ExecutionTrace]) -> str:
        if not failures:
            return "No failures"
        patterns = set()
        for f in failures:
            if f.error:
                first_line = f.error.split("\n")[0][:100]
                patterns.add(first_line)
        return f"{len(failures)} failures: " + "; ".join(patterns)
