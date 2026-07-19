"""Production SDO lifecycle handoff primitives."""

from app_operator.lifecycle.agents import CodexLifecycleBackend, LifecycleAgentBackend
from app_operator.lifecycle.deployment import (
    CodexDeploymentBackend,
    DeploymentAttempt,
    DeploymentBackend,
    DeploymentError,
    DeploymentVerification,
    DeploymentVerifier,
    deploy_from_source,
)
from app_operator.lifecycle.operational_memory import (
    LifecycleError,
    ensure_operational_memory,
    reuse_initial_lifecycle_if_valid,
    run_initial_lifecycle,
)

__all__ = [
    "CodexDeploymentBackend",
    "CodexLifecycleBackend",
    "DeploymentAttempt",
    "DeploymentBackend",
    "DeploymentError",
    "DeploymentVerification",
    "DeploymentVerifier",
    "LifecycleAgentBackend",
    "LifecycleError",
    "deploy_from_source",
    "ensure_operational_memory",
    "reuse_initial_lifecycle_if_valid",
    "run_initial_lifecycle",
]
