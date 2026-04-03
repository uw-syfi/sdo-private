"""Adapter that maps DSPy signature instructions to GEPA candidates.

Reads and writes the instruction text on DSPy Signature classes,
which is the primary optimization target for GEPA.
"""

import dspy

from app_operator_dspy.optimize.gepa.candidate import PromptCandidate
from app_operator_dspy.signatures import GenerateDeployScript, RepairDeploymentError

# Signatures eligible for optimization.
OPTIMIZABLE_SIGNATURES = {
    "GenerateDeployScript": GenerateDeployScript,
    "RepairDeploymentError": RepairDeploymentError,
}


def get_instruction(signature_name: str) -> str:
    """Read the current instruction text from a signature class."""
    sig_cls = OPTIMIZABLE_SIGNATURES.get(signature_name)
    if sig_cls is None:
        raise ValueError(f"Unknown signature: {signature_name}")
    return sig_cls.__doc__ or ""


def set_instruction(signature_name: str, instruction: str) -> None:
    """Replace the instruction text on a signature class.

    This modifies the class-level docstring, which DSPy uses to
    generate the system prompt for the signature.
    """
    sig_cls = OPTIMIZABLE_SIGNATURES.get(signature_name)
    if sig_cls is None:
        raise ValueError(f"Unknown signature: {signature_name}")
    sig_cls.__doc__ = instruction


def make_initial_candidate(signature_name: str) -> PromptCandidate:
    """Create a candidate from the current signature instruction."""
    return PromptCandidate(
        signature_name=signature_name,
        instruction_text=get_instruction(signature_name),
        generation=0,
        mutation_type="initial",
    )


def apply_candidate(candidate: PromptCandidate) -> None:
    """Apply a candidate's instruction to its signature class."""
    set_instruction(candidate.signature_name, candidate.instruction_text)


def validate_candidate(candidate: PromptCandidate) -> bool:
    """Check that a mutated instruction is plausible.

    Ensures the instruction is non-empty and not too short to be useful.
    """
    text = candidate.instruction_text.strip()
    if not text:
        return False
    if len(text) < 20:
        return False
    return True


def get_output_field_descs(signature_name: str) -> dict[str, str]:
    """Return output field descriptions for a signature.

    These are NOT optimized by GEPA (they define the output schema)
    but are useful context for the reflector.
    """
    sig_cls = OPTIMIZABLE_SIGNATURES[signature_name]
    return {
        name: field.json_schema_extra.get("desc", "")
        for name, field in sig_cls.output_fields.items()
        if hasattr(field, "json_schema_extra") and field.json_schema_extra
    }
