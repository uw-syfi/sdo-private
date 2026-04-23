"""Agent-based fault-injection verifier.

Entry point: `run_fault_verifier(...)` from verifier.py. Subprocess CLI
for invocation from outside this Python process: `__main__.py`.
"""

from libs.sregym_lib import NOOP_EXP_STAGE_LIFECYCLE, ExpStageLifecycle

from .verifier import FaultVerification, parse_agent_output, run_fault_verifier


def get_exp_stage_lifecycle() -> ExpStageLifecycle:
    return NOOP_EXP_STAGE_LIFECYCLE


__all__ = ["FaultVerification", "get_exp_stage_lifecycle", "parse_agent_output", "run_fault_verifier"]
