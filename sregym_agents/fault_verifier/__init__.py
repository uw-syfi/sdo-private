"""Agent-based fault-injection verifier.

Entry point: `run_fault_verifier(...)` from verifier.py. Subprocess CLI
for invocation from outside this Python process: `__main__.py`.
"""

from .verifier import FaultVerification, parse_agent_output, run_fault_verifier

__all__ = ["FaultVerification", "parse_agent_output", "run_fault_verifier"]
