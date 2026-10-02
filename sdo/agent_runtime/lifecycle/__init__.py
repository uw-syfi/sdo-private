"""Production SDO lifecycle handoff primitives."""

from sdo.agent_runtime.lifecycle.agents import (
    ActiveTopologyResourceDTO,
    ClaudeLifecycleBackend,
    CodexLifecycleBackend,
    LifecycleAgentBackend,
)
from sdo.agent_runtime.lifecycle.deployment import (
    ClaudeDeploymentBackend,
    CodexDeploymentBackend,
    DeploymentAttempt,
    DeploymentBackend,
    DeploymentError,
    DeploymentVerification,
    DeploymentVerifier,
    deploy_from_source,
)
from sdo.agent_runtime.lifecycle.operational_memory import (
    LifecycleError,
    check_detector_workspace,
    ensure_operational_memory,
    reuse_initial_lifecycle_if_valid,
    run_initial_lifecycle,
    validator_identity,
)
from sdo.agent_runtime.lifecycle.seed_cache import (
    LifecycleSeedCache,
    SeedInputs,
    lifecycle_code_digest,
)
from sdo.agent_runtime.lifecycle.validation_cache import LifecycleValidationCache, validation_report
from sdo.operational_memory import SandboxResult, SandboxRunner

__all__ = [
    "CodexDeploymentBackend",
    "CodexLifecycleBackend",
    "ClaudeDeploymentBackend",
    "ClaudeLifecycleBackend",
    "ActiveTopologyResourceDTO",
    "DeploymentAttempt",
    "DeploymentBackend",
    "DeploymentError",
    "DeploymentVerification",
    "DeploymentVerifier",
    "LifecycleAgentBackend",
    "LifecycleError",
    "LifecycleSeedCache",
    "LifecycleValidationCache",
    "SandboxResult",
    "SeedInputs",
    "SandboxRunner",
    "check_detector_workspace",
    "deploy_from_source",
    "lifecycle_code_digest",
    "ensure_operational_memory",
    "reuse_initial_lifecycle_if_valid",
    "run_initial_lifecycle",
    "validation_report",
    "validator_identity",
]
