"""Crucible agent tools — public re-export surface.

Import from ``sregym_agents.crucible.tools`` as before; the submodule split
is an implementation detail.
"""

from sregym_agents.crucible.tools._bash_tools import (
    APPLY_READ_CHAR_LIMIT,
    BASH_TIMEOUT,
    MAX_GREP_RESULTS,
    MAX_OUTPUT_CHARS,
    MAX_READ_CHARS,
    MUTATING_KUBECTL_VERBS,
    _check_mutating_kubectl,
    _exec_bash_readonly_impl,
    _run_bash_sync,
    exec_bash,
    exec_bash_any,
    exec_bash_readonly,
    grep,
    read_file,
    str_replace_file,
    write_file,
)
from sregym_agents.crucible.tools._deps import (
    JudgeDeps,
    SharedFile,
    SharedState,
    SREDeps,
    SRESubmission,
    TriageDeps,
)
from sregym_agents.crucible.tools._judge_tools import (
    _submit_to_benchmark,
    reveal_agent_hypothesis,
    submit_independent_findings,
    submit_verdict,
)
from sregym_agents.crucible.tools._kb_tools import (
    COVERAGE_THINKING_BUDGET,
    MAX_OUTPUT_TOKENS,
    THINKING_BUDGET,
    VERIFICATION_THINKING_BUDGET,
    CandidateRootCause,
    CandidateVerification,
    DifferentialDiagnosis,
    HypothesisCoverageVerdict,
    MitigationSearchResult,
    MitigationStrategy,
    TriageAnomaly,
    TriageReport,
    VerifiedDifferentialDiagnosis,
    check_hypothesis_coverage,
    format_triage_report,
    search_prior_incidents,
    search_prior_mitigations,
    triage_cluster,
)

__all__ = [
    # _bash_tools
    "APPLY_READ_CHAR_LIMIT",
    "BASH_TIMEOUT",
    "MAX_GREP_RESULTS",
    "MAX_OUTPUT_CHARS",
    "MAX_READ_CHARS",
    "MUTATING_KUBECTL_VERBS",
    "exec_bash",
    "exec_bash_any",
    "exec_bash_readonly",
    "grep",
    "read_file",
    "str_replace_file",
    "write_file",
    # _deps
    "JudgeDeps",
    "SharedFile",
    "SharedState",
    "SREDeps",
    "SRESubmission",
    "TriageDeps",
    # _judge_tools
    "reveal_agent_hypothesis",
    "submit_independent_findings",
    "submit_verdict",
    # _kb_tools
    "COVERAGE_THINKING_BUDGET",
    "MAX_OUTPUT_TOKENS",
    "THINKING_BUDGET",
    "VERIFICATION_THINKING_BUDGET",
    "CandidateRootCause",
    "CandidateVerification",
    "DifferentialDiagnosis",
    "HypothesisCoverageVerdict",
    "MitigationSearchResult",
    "MitigationStrategy",
    "TriageAnomaly",
    "TriageReport",
    "VerifiedDifferentialDiagnosis",
    "check_hypothesis_coverage",
    "format_triage_report",
    "search_prior_incidents",
    "search_prior_mitigations",
    "triage_cluster",
]
