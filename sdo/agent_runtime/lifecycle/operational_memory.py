from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, cast

import yaml

from sdo.agent_runtime.lifecycle.agents import (
    ActiveTopologyResourceDTO,
    AuthoredTrafficFile,
    CodexLifecycleBackend,
    DeployerAssessment,
    DeployerHandoff,
    HealthJudgeArtifact,
    HealthJudgeWorkspaceArtifact,
    LifecycleAgentBackend,
    LifecycleAgentError,
    TopologyResourceDTO,
    WorkspaceHealthJudgeBackend,
)
from sdo.operational_memory import (
    BROKER_AUTHOR_EMAIL,
    TRAFFIC_DIRECTORY,
    TRAFFIC_INCIDENT_WORKLOAD_PREFIX,
    VALIDATION_PASSED_TRAILER,
    ContainerSandboxRunner,
    SandboxResult,
    SandboxRunner,
    TrafficWorkload,
)

if TYPE_CHECKING:
    from sdo.agent_runtime.lifecycle.validation_cache import LifecycleValidationCache

logger = logging.getLogger(__name__)

_VALIDATION_ATTESTATION_SCHEMA = "sdo.lifecycle-validation/v1"


class LifecycleError(RuntimeError):
    """Raised when deployment-to-controller operational memory cannot be initialized."""


@dataclass(frozen=True)
class TopologyResource:
    kind: str
    name: str
    namespace: str
    source: str
    dependencies: tuple[str, ...]


def check_detector_workspace(app_root: Path, *, validator: SandboxRunner | None = None) -> SandboxResult:
    """Run the fixed isolated detector check used by authoring agents."""

    root = app_root.resolve()
    semantic_errors = _health_judge_authoring_errors(root)
    if semantic_errors:
        return SandboxResult(
            returncode=1,
            stderr="semantic detector checks failed:\n" + "\n".join(f"- {error}" for error in semantic_errors),
        )
    selected = validator or ContainerSandboxRunner(
        detector_ids=("health-objective",),
        authoring_check=True,
    )
    return selected.run(root)


def _health_judge_authoring_errors(root: Path) -> list[str]:
    context_path = root / ".sdo/session-scratch/health-judge-context.json"
    if not context_path.is_file():
        return []
    try:
        context = json.loads(context_path.read_text(encoding="utf-8"))
        deployer = DeployerAssessment.model_validate(context["deployer"])
        health_objective = str(context["health_objective"])
        round_index = int(context["round_index"])
        raw_active = context.get("active_resources")
        active_resources = (
            [ActiveTopologyResourceDTO.model_validate(resource) for resource in raw_active]
            if isinstance(raw_active, list)
            else None
        )
        detector = root / ".sdo/diagnostics/detectors/health/objective"
        artifact = HealthJudgeArtifact(
            session_id="authoring-self-check",
            round=round_index,
            objective_digest=hashlib.sha256(health_objective.strip().encode()).hexdigest(),
            source_commit=deployer.source_commit,
            covered_resources=deployer.resources,
            failure_patterns=["authoring draft"],
            detector_source=(detector / "detector.go").read_text(encoding="utf-8"),
            detector_test_source=(detector / "detector_test.go").read_text(encoding="utf-8"),
            traffic_files=_authored_traffic_files(root),
        )
        artifact = _canonicalize_active_coverage(
            artifact,
            deployer=deployer,
            health_objective=health_objective,
            active_resources=active_resources,
        )
        return _validate_health_judge_artifact(
            artifact,
            deployer=deployer,
            health_objective=health_objective,
            expected_round=round_index,
            active_resources=active_resources,
        )
    except (KeyError, OSError, TypeError, ValueError) as exc:
        return [f"invalid controller-authored health-judge context: {exc}"]


def _write_health_judge_authoring_context(
    root: Path,
    *,
    deployer: DeployerAssessment,
    health_objective: str,
    round_index: int,
    active_resources: list[ActiveTopologyResourceDTO] | None,
) -> None:
    scratch = root / ".sdo/session-scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    (scratch / "health-judge-context.json").write_text(
        json.dumps(
            {
                "deployer": deployer.model_dump(mode="json"),
                "health_objective": health_objective,
                "round_index": round_index,
                "active_resources": (
                    [resource.model_dump(mode="json") for resource in active_resources]
                    if active_resources is not None
                    else None
                ),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def reuse_initial_lifecycle_if_valid(
    app_root: Path,
    *,
    application: str,
    health_objective: str,
    active_resources: list[ActiveTopologyResourceDTO] | None = None,
    validator: SandboxRunner | None = None,
    validation_cache: LifecycleValidationCache | None = None,
) -> bool:
    """Reuse a real lifecycle handoff only while its source and contracts remain valid.

    An opt-in ``validation_cache`` shares passing validator verdicts, under the
    attestation's own key, across workspace copies and records in ``source``
    how this check was satisfied.
    """

    root = app_root.resolve()
    provenance_path = root / ".sdo" / "lifecycle-provenance.yaml"
    required_paths = (
        ".sdo/schema-version",
        ".sdo/goal.md",
        ".sdo/arch.md",
        ".sdo/playbooks/README.md",
        ".sdo/diagnostics/manifest.yaml",
        ".sdo/diagnostics/detectors/health/objective/detector.go",
        ".sdo/diagnostics/detectors/health/objective/detector_test.go",
        ".sdo/outcomes.jsonl",
    )
    if not provenance_path.is_file() or any(not (root / relative).is_file() for relative in required_paths):
        return False
    try:
        provenance = yaml.safe_load(provenance_path.read_text(encoding="utf-8"))
        if not isinstance(provenance, dict):
            return False
        deployer = DeployerAssessment.model_validate(provenance.get("deployer"))
        final_artifact = HealthJudgeArtifact.model_validate(provenance.get("health_judge"))
        raw_attempts = provenance.get("health_judge_rounds")
        if not isinstance(raw_attempts, list):
            return False
        attempts = [HealthJudgeArtifact.model_validate(raw) for raw in raw_attempts]
        if active_resources is not None:
            recorded_active = [
                ActiveTopologyResourceDTO.model_validate(resource) for resource in provenance.get("active_topology", [])
            ]
            if _active_resource_keys(recorded_active) != _active_resource_keys(active_resources):
                return False
        expected = _deployer_assessment({"repository": str(root), "application": application})
        current_deployer = deployer.model_copy(update={"source_commit": str(expected["source_commit"])})
        if _validate_deployer_assessment(root, current_deployer) and not _deployer_survives_validated_sdo_changes(
            root, deployer, health_objective=health_objective
        ):
            return False
        session_ids = [deployer.session_id, *(attempt.session_id for attempt in attempts)]
        if len(session_ids) != len(set(session_ids)):
            return False
        if not {1, 2, 3}.issubset({attempt.round for attempt in attempts}):
            return False
        if not attempts or final_artifact.session_id != attempts[-1].session_id or final_artifact.round != 3:
            return False
        if _validate_health_judge_artifact(
            final_artifact,
            deployer=deployer,
            health_objective=health_objective,
            expected_round=3,
            active_resources=active_resources,
        ):
            return False
        selected_validator = validator or ContainerSandboxRunner()
        validation_identity = _validator_identity(selected_validator)
        diagnostics_digest = _diagnostics_digest(root)
        validation = provenance.get("validation")
        if (
            validation_identity is not None
            and isinstance(validation, dict)
            and validation.get("schema_version") == _VALIDATION_ATTESTATION_SCHEMA
            and validation.get("validator_identity") == validation_identity
            and validation.get("diagnostics_digest") == diagnostics_digest
        ):
            if validation_cache is not None:
                validation_cache.source = "workspace-attestation"
            return True
        cached = (
            validation_cache is not None
            and validation_identity is not None
            and validation_cache.contains(validation_identity, diagnostics_digest)
        )
        if not cached:
            result = selected_validator.run(root)
            if result.returncode != 0:
                return False
            if validation_cache is not None and validation_identity is not None:
                validation_cache.record(validation_identity, diagnostics_digest)
        if validation_cache is not None:
            validation_cache.source = "validation-cache" if cached else "validator"
        if validation_identity is not None:
            provenance["validation"] = {
                "schema_version": _VALIDATION_ATTESTATION_SCHEMA,
                "diagnostics_digest": diagnostics_digest,
                "validator_identity": validation_identity,
                "attested_by": "validation-cache" if cached else "validator",
            }
            provenance_path.write_text(yaml.safe_dump(provenance, sort_keys=True), encoding="utf-8")
            _commit(
                root,
                "sdo: attest lifecycle memory from the shared validation cache"
                if cached
                else "sdo: attest independently validated lifecycle memory",
            )
        return True
    except (LifecycleError, OSError, TypeError, ValueError, yaml.YAMLError):
        return False


def _deployer_survives_validated_sdo_changes(
    root: Path,
    deployer: DeployerAssessment,
    *,
    health_objective: str,
) -> bool:
    """Keep a lifecycle handoff across source changes SDO itself validated.

    An incident outcome may commit a source repair, such as restoring a missing
    manifest. That drift does not invalidate the deployer and health judge when
    every source-changing commit since the handoff is a broker-validated SDO
    commit, the deployer assessment still holds at its recorded commit, and the
    health judge's derived input is unchanged at HEAD.
    """

    base = deployer.source_commit
    try:
        _git(root, "merge-base", "--is-ancestor", base, "HEAD")
        source_commits = _git(root, "rev-list", f"{base}..HEAD", "--", ".", ":(exclude).sdo").split()
        for commit in source_commits:
            author = _git(root, "log", "-1", "--format=%ae", commit)
            message = _git(root, "log", "-1", "--format=%B", commit)
            if author != BROKER_AUTHOR_EMAIL or VALIDATION_PASSED_TRAILER not in message.splitlines():
                return False
        with tempfile.TemporaryDirectory(prefix="sdo-lifecycle-handoff-") as scratch:
            checkout = Path(scratch) / "source"
            _git(root, "worktree", "add", "--detach", "--quiet", str(checkout), base)
            try:
                if _validate_deployer_assessment(checkout, deployer):
                    return False
            finally:
                _git(root, "worktree", "remove", "--force", str(checkout))
        current = _deployer_assessment({"repository": str(root), "application": root.name})
    except LifecycleError:
        return False

    def judged(resources: object) -> dict[str, object]:
        plan = _judge_assessment(
            {
                "health_objective": health_objective,
                "deployer_assessment": {"resources": resources, "source_commit": ""},
            }
        )
        plan.pop("source_commit", None)
        return plan

    recorded = [resource.model_dump(mode="json") for resource in deployer.resources]
    if judged(recorded) != judged(current["resources"]):
        return False
    logger.info(
        "reusing lifecycle handoff across %d validated SDO source commit(s) since %s",
        len(source_commits),
        base[:12],
    )
    return True


def run_initial_lifecycle(
    app_root: Path,
    *,
    application: str,
    health_objective: str,
    active_resources: list[ActiveTopologyResourceDTO] | None = None,
    backend: LifecycleAgentBackend | None = None,
    validator: SandboxRunner | None = None,
    judge_rounds: int = 3,
    judge_corrections_per_round: int = 3,
    deployer_attempts: int = 3,
) -> str:
    """Bootstrap memory with fresh model-backed deployer and health-judge sessions."""

    if judge_rounds < 1 or judge_corrections_per_round < 1 or deployer_attempts < 1:
        raise ValueError("lifecycle rounds, corrections, and deployer attempts must be positive")
    root = app_root.resolve()
    selected_backend = backend or CodexLifecycleBackend(model=os.getenv("SDO_LIFECYCLE_MODEL") or None)
    selected_validator = validator or ContainerSandboxRunner()

    deployer: DeployerAssessment | None = None
    trusted_source_facts = _deployer_assessment({"repository": str(root), "application": application})
    trusted_resources = cast("list[dict[str, object]]", trusted_source_facts["resources"])
    required_resource_names = ", ".join(sorted(repr(str(resource["name"])) for resource in trusted_resources))
    trusted_inventory = [TopologyResourceDTO.model_validate(resource) for resource in trusted_resources]
    trusted_source_feedback = (
        "Trusted controller-derived source facts. Copy source_commit and topology_fingerprint exactly. The "
        "controller attaches the resource inventory below to your handoff; do not return it. "
        "The architecture_summary_markdown must mention every resource name verbatim, including low-level data "
        f"stores and observability resources. Required names: {required_resource_names}. Use repository inspection "
        "to describe their relationships:\n" + json.dumps(trusted_source_facts, indent=2, sort_keys=True)
    )
    deployer_feedback: str | None = trusted_source_feedback
    deployer_errors: list[str] = []
    for attempt_index in range(1, deployer_attempts + 1):
        try:
            handoff = selected_backend.run_deployer(
                repository=root,
                application=application,
                correction_feedback=deployer_feedback,
            )
            candidate = _attach_trusted_inventory(handoff, trusted_inventory)
            validation_errors = _validate_deployer_assessment(root, candidate)
        except (LifecycleAgentError, ValueError) as exc:
            validation_errors = [str(exc)]
        else:
            if not validation_errors:
                deployer = candidate
                break
        deployer_errors = validation_errors
        logger.warning(
            "deployer attempt %d failed validation: %s",
            attempt_index,
            "; ".join(validation_errors),
        )
        deployer_feedback = (
            trusted_source_feedback
            + "\n\nPrior validation errors:\n"
            + "\n".join(f"- {error}" for error in validation_errors)
        )
    if deployer is None:
        raise LifecycleError("deployer exhausted its bounded correction attempts: " + "; ".join(deployer_errors))

    judge_attempts: list[HealthJudgeArtifact] = []
    judge_feedback: str | None = None
    previous: HealthJudgeArtifact | None = None
    used_sessions = {deployer.session_id}
    final_artifact: HealthJudgeArtifact | None = None
    final_validation_digest: str | None = None
    last_errors: list[str] = []
    for round_index in range(1, judge_rounds + 1):
        round_passed = False
        for attempt_index in range(1, judge_corrections_per_round + 1):
            try:
                workspace_author = getattr(selected_backend, "run_health_judge_workspace", None)
                if callable(workspace_author):
                    artifact, workspace_validation, candidate_digest = _run_workspace_authored_candidate(
                        root,
                        application=application,
                        health_objective=health_objective,
                        deployer=deployer,
                        active_resources=active_resources,
                        round_index=round_index,
                        previous=previous,
                        correction_feedback=judge_feedback,
                        backend=cast("WorkspaceHealthJudgeBackend", selected_backend),
                        validator=selected_validator,
                    )
                else:
                    artifact = selected_backend.run_health_judge(
                        repository=root,
                        application=application,
                        health_objective=health_objective,
                        deployer=deployer,
                        active_resources=active_resources,
                        round_index=round_index,
                        previous=previous,
                        correction_feedback=judge_feedback,
                    )
                    workspace_validation = None
                    candidate_digest = None
            except (LifecycleAgentError, LifecycleError, OSError, ValueError) as exc:
                last_errors = [str(exc)]
                logger.warning(
                    "health judge round %d attempt %d failed before validation: %s",
                    round_index,
                    attempt_index,
                    "; ".join(last_errors),
                )
                judge_feedback = "The prior fresh session failed before validation:\n" + "\n".join(
                    f"- {error}" for error in last_errors
                )
                continue
            artifact = _canonicalize_active_coverage(
                artifact,
                deployer=deployer,
                health_objective=health_objective,
                active_resources=active_resources,
            )
            judge_attempts.append(artifact)
            errors = _validate_health_judge_artifact(
                artifact,
                deployer=deployer,
                health_objective=health_objective,
                expected_round=round_index,
                active_resources=active_resources,
            )
            if artifact.session_id in used_sessions:
                errors.append(f"lifecycle session id {artifact.session_id!r} was reused instead of starting fresh")
            used_sessions.add(artifact.session_id)
            if not errors:
                validation = workspace_validation
                if validation is None:
                    validation, candidate_digest = _validate_authored_candidate(
                        root,
                        application=application,
                        health_objective=health_objective,
                        deployer=deployer,
                        artifact=artifact,
                        validator=selected_validator,
                    )
                if validation.returncode != 0:
                    # Go reports test failures on stdout; stderr may hold only toolchain noise.
                    details = (
                        "\n".join(stream.strip() for stream in (validation.stderr, validation.stdout) if stream.strip())
                        or "detector validation failed"
                    )
                    errors.append(details)
            previous = artifact
            last_errors = errors
            if errors:
                logger.warning(
                    "health judge round %d attempt %d failed validation: %s",
                    round_index,
                    attempt_index,
                    "; ".join(errors),
                )
                judge_feedback = "The prior artifact failed independent validation:\n" + "\n".join(
                    f"- {error}" for error in errors
                )
                continue
            final_artifact = artifact
            final_validation_digest = candidate_digest
            round_passed = True
            judge_feedback = (
                "The prior artifact passed compilation and tests. Adversarially identify another objective-specific "
                "failure pattern it could miss, strengthen the detector without adding broad false positives, and "
                "retain matching plus near-miss tests."
            )
            break
        if not round_passed:
            raise LifecycleError(
                f"health judge round {round_index} exhausted bounded correction attempts: " + "; ".join(last_errors)
            )
    if final_artifact is None:
        raise LifecycleError("health judge produced no validated final artifact")

    provenance = {
        "deployer": deployer.model_dump(mode="json"),
        "health_judge": final_artifact.model_dump(mode="json"),
        "health_judge_rounds": [artifact.model_dump(mode="json") for artifact in judge_attempts],
    }
    validation_identity = _validator_identity(selected_validator)
    if validation_identity is not None and final_validation_digest is not None:
        provenance["validation"] = {
            "schema_version": _VALIDATION_ATTESTATION_SCHEMA,
            "diagnostics_digest": final_validation_digest,
            "validator_identity": validation_identity,
        }
    if active_resources is not None:
        provenance["active_topology"] = [
            resource.model_dump(mode="json")
            for resource in sorted(active_resources, key=lambda resource: (resource.kind, resource.name))
        ]
    return ensure_operational_memory(
        root,
        application=application,
        health_objective=health_objective,
        health_judge_artifact=final_artifact,
        architecture_summary_markdown=deployer.architecture_summary_markdown,
        lifecycle_provenance=provenance,
    )


def _attach_trusted_inventory(handoff: DeployerHandoff, inventory: list[TopologyResourceDTO]) -> DeployerAssessment:
    """Make the source resource inventory a controller fact, not model output."""

    fields = handoff.model_dump(include=set(DeployerHandoff.model_fields))
    return DeployerAssessment(**fields, resources=inventory)


def _deployer_assessment(payload: dict[str, object]) -> dict[str, object]:
    root = Path(str(payload["repository"])).resolve()
    tracked = [path for path in _git(root, "ls-files").splitlines() if path and not path.startswith(".sdo/")]
    resources = _topology_resources(root, tracked)
    return {
        "application": str(payload["application"]),
        "source_commit": _git(root, "rev-parse", "HEAD"),
        "topology_fingerprint": _topology_fingerprint(root),
        "resources": [
            {
                "kind": resource.kind,
                "name": resource.name,
                "namespace": resource.namespace,
                "source": resource.source,
                "dependencies": list(resource.dependencies),
            }
            for resource in resources
        ],
    }


def _judge_assessment(payload: dict[str, object]) -> dict[str, object]:
    objective = str(payload["health_objective"]).strip()
    deployer = payload.get("deployer_assessment")
    if not isinstance(deployer, dict) or not isinstance(deployer.get("resources"), list):
        raise LifecycleError("health judge requires a published deployer assessment")
    resources = [resource for resource in deployer["resources"] if isinstance(resource, dict)]
    objective_lower = objective.lower()
    selects_all_source_backed = "all source-backed" in objective_lower

    def targets(kind: str) -> list[str]:
        names = sorted(
            {
                str(resource["name"])
                for resource in resources
                if resource.get("kind") == kind and isinstance(resource.get("name"), str)
            }
        )
        mentioned = [] if selects_all_source_backed else [name for name in names if name.lower() in objective_lower]
        return mentioned or names

    deployment_names = targets("Deployment")
    workload_label_sets: list[dict[str, str]] = []
    for resource in resources:
        if resource.get("kind") != "Deployment" or resource.get("name") not in deployment_names:
            continue
        dependencies = resource.get("dependencies")
        if not isinstance(dependencies, list):
            continue
        labels = {}
        for dependency in dependencies:
            if not isinstance(dependency, str) or not dependency.startswith("pod-label:"):
                continue
            key, separator, value = dependency.removeprefix("pod-label:").partition("=")
            if separator and key and value:
                labels[key] = value
        if labels and labels not in workload_label_sets:
            workload_label_sets.append(labels)
    return {
        "input_context": "published-deployer-assessment",
        "objective_digest": hashlib.sha256(objective.encode()).hexdigest(),
        "deployment_names": deployment_names,
        "service_names": targets("Service"),
        "network_policy_names": targets("NetworkPolicy"),
        "workload_label_sets": workload_label_sets,
        "source_commit": deployer.get("source_commit"),
    }


def _validate_deployer_assessment(root: Path, assessment: DeployerAssessment) -> list[str]:
    expected = _deployer_assessment({"repository": str(root), "application": root.name})
    errors: list[str] = []
    if assessment.source_commit != expected["source_commit"]:
        errors.append("deployer source_commit does not match repository HEAD")
    if assessment.topology_fingerprint != expected["topology_fingerprint"]:
        errors.append("deployer topology_fingerprint does not match tracked source")

    def canonical(resources: object) -> list[tuple[str, str, str, str, tuple[str, ...]]]:
        if not isinstance(resources, list):
            return []
        result = []
        for resource in resources:
            if hasattr(resource, "model_dump"):
                resource = resource.model_dump(mode="json")
            if not isinstance(resource, dict):
                continue
            result.append(
                (
                    str(resource.get("kind", "")),
                    str(resource.get("name", "")),
                    str(resource.get("namespace", "")),
                    str(resource.get("source", "")),
                    tuple(sorted(str(item) for item in resource.get("dependencies", []))),
                )
            )
        return sorted(result)

    if canonical(assessment.resources) != canonical(expected["resources"]):
        errors.append("deployer resource inventory is incomplete or differs from tracked manifests")
    summary = assessment.architecture_summary_markdown.lower()
    missing_names = sorted({resource.name for resource in assessment.resources if resource.name.lower() not in summary})
    if missing_names:
        errors.append("architecture summary omits source-backed resources: " + ", ".join(missing_names))
    return errors


def _validate_health_judge_artifact(
    artifact: HealthJudgeArtifact,
    *,
    deployer: DeployerAssessment,
    health_objective: str,
    expected_round: int,
    active_resources: list[ActiveTopologyResourceDTO] | None = None,
) -> list[str]:
    errors: list[str] = []
    expected_digest = hashlib.sha256(health_objective.strip().encode()).hexdigest()
    if artifact.round != expected_round:
        errors.append(f"health judge returned round {artifact.round}, expected {expected_round}")
    if artifact.objective_digest != expected_digest:
        errors.append("health judge objective digest does not match the human-owned objective")
    if artifact.source_commit != deployer.source_commit:
        errors.append("health judge source commit does not match the published deployer assessment")
    known_resources = {
        (resource.kind, resource.name, resource.namespace, resource.source, tuple(sorted(resource.dependencies)))
        for resource in deployer.resources
    }
    covered_resources = {
        (resource.kind, resource.name, resource.namespace, resource.source, tuple(sorted(resource.dependencies)))
        for resource in artifact.covered_resources
    }
    active_keys = _active_resource_keys(active_resources) if active_resources is not None else None
    inactive_covered = (
        sorted(resource for resource in covered_resources if resource[:2] not in active_keys)
        if active_keys is not None
        else []
    )
    if inactive_covered:
        rendered = ", ".join(f"{kind}/{name}" for kind, name, *_rest in inactive_covered)
        errors.append("health detector covers inactive source variants; remove: " + rendered)
    if known_resources and not covered_resources:
        errors.append("health detector does not cover any published source-backed resource")
    unexpected_resources = sorted(covered_resources - known_resources)
    if unexpected_resources:
        rendered = "; ".join(
            f"kind={kind!r}, name={name!r}, namespace={namespace!r}, source={source!r}, dependencies={dependencies!r}"
            for kind, name, namespace, source, dependencies in unexpected_resources
        )
        errors.append(
            "health detector claims resources absent from the deployer handoff: "
            + rendered
            + ". covered_resources must copy complete objects exactly from the published deployer handoff, "
            "including the source-manifest namespace and dependencies"
        )
    objective_lower = health_objective.lower()
    global_objective = "all source-backed deployments" in objective_lower or "all selected services" in objective_lower
    required_kinds: set[str] = set()
    if global_objective:
        required_kinds.update({"Deployment", "Service", "ConfigMap", "NetworkPolicy"})
    required_resources = {
        (resource.kind, resource.name, resource.namespace, resource.source, tuple(sorted(resource.dependencies)))
        for resource in deployer.resources
        if resource.kind in required_kinds and (active_keys is None or (resource.kind, resource.name) in active_keys)
    }
    missing_required = sorted(required_resources - covered_resources)
    if missing_required:
        rendered = ", ".join(f"{kind}/{name}" for kind, name, *_rest in missing_required)
        errors.append("health detector omits required source-backed resources: " + rendered)
    extra_covered = sorted(covered_resources - required_resources) if global_objective else []
    if extra_covered:
        rendered = ", ".join(f"{kind}/{name}" for kind, name, *_rest in extra_covered)
        errors.append(
            "global health detector covered_resources must cover exactly the required Deployment, Service, "
            "ConfigMap, and NetworkPolicy inventory; remove: " + rendered
        )
    if not artifact.failure_patterns or any(not pattern.strip() for pattern in artifact.failure_patterns):
        errors.append("health judge must enumerate concrete failure patterns covered by the detector")

    source = artifact.detector_source
    tests = artifact.detector_test_source
    referenced_config_maps = sorted(
        {
            dependency.removeprefix("ConfigMap/")
            for resource in deployer.resources
            if resource.kind == "Deployment"
            for dependency in resource.dependencies
            if dependency.startswith("ConfigMap/") and dependency.removeprefix("ConfigMap/")
        }
    )
    if global_objective and referenced_config_maps:
        reads_config_maps = "ConfigMaps()" in source
        uses_sdk_reference_helper = "sdk.ConfigMapReferencesForDeployment(" in source
        traverses_pod_spec = (
            reads_config_maps
            and re.search(r"\.Spec\s*\.\s*Template\s*\.\s*Spec", source) is not None
            and re.search(r"\.\s*Volumes\b", source) is not None
            and re.search(r"\.\s*ConfigMap\b", source) is not None
        )
        derives_config_maps = reads_config_maps and (uses_sdk_reference_helper or traverses_pod_spec)
        if not derives_config_maps:
            rendered = ", ".join(f"ConfigMap/{name}" for name in referenced_config_maps)
            errors.append(
                "health detector must derive missing ConfigMap dependencies from Deployment pod specs/volume "
                f"references and compare them with DetectionContext.ConfigMaps(): {rendered}"
            )
    required_source = {
        "package objective": "detector must use package objective",
        "func New() sdk.Detector": "detector must export New() sdk.Detector",
        "Spec() sdk.DetectorSpec": "detector must expose a registration method for lifecycle enforcement",
        "Detect(": "detector must implement deterministic Detect",
        "healthObjectiveDigest": "detector must bind itself to the objective digest",
        expected_digest: "detector source does not embed the exact objective digest",
    }
    for marker, message in required_source.items():
        if marker not in source:
            errors.append(message)
    if "package objective" not in tests or "func Test" not in tests:
        errors.append("health judge must author deterministic Go matching and near-miss tests")
    forbidden_oracles = (
        "sregym",
        "verdict_path",
        "os.getenv",
        "os.environ",
        "benchmark result",
        "hidden fault",
    )
    combined = f"{source}\n{tests}".lower()
    if any(token in combined for token in forbidden_oracles):
        errors.append("health detector contains a benchmark or environment oracle")
    if any(resource.namespace == "default" for resource in deployer.resources) and '"default"' in source:
        errors.append(
            "health detector hard-codes source namespace default; use DetectionContext.Namespace() at runtime"
        )
    try:
        _canonicalize_health_registration(source)
    except LifecycleError as exc:
        errors.append(str(exc))
    errors.extend(_traffic_errors(artifact.traffic_files, deployer))
    return errors


def _active_resource_keys(
    resources: list[ActiveTopologyResourceDTO] | None,
) -> set[tuple[str, str]]:
    if resources is None:
        return set()
    return {(resource.kind, resource.name) for resource in resources}


def _canonicalize_active_coverage(
    artifact: HealthJudgeArtifact,
    *,
    deployer: DeployerAssessment,
    health_objective: str,
    active_resources: list[ActiveTopologyResourceDTO] | None,
) -> HealthJudgeArtifact:
    """Make deployed-resource provenance a controller fact, not model output."""

    objective_lower = health_objective.lower()
    global_objective = "all source-backed deployments" in objective_lower or "all selected services" in objective_lower
    if active_resources is None and not global_objective:
        return artifact
    active_keys = _active_resource_keys(active_resources) if active_resources is not None else None
    covered_resources = [
        resource
        for resource in deployer.resources
        if resource.kind in {"ConfigMap", "Deployment", "NetworkPolicy", "Service"}
        and (active_keys is None or (resource.kind, resource.name) in active_keys)
    ]
    return artifact.model_copy(update={"covered_resources": covered_resources})


def _validate_authored_candidate(
    root: Path,
    *,
    application: str,
    health_objective: str,
    deployer: DeployerAssessment,
    artifact: HealthJudgeArtifact,
    validator: SandboxRunner,
) -> tuple[SandboxResult, str]:
    with tempfile.TemporaryDirectory(prefix="sdo-lifecycle-candidate-") as temp_dir:
        candidate = Path(temp_dir) / "application"
        cloned = subprocess.run(
            ["git", "clone", "--quiet", "--no-hardlinks", str(root), str(candidate)],
            check=False,
            capture_output=True,
            text=True,
        )
        if cloned.returncode != 0:
            details = cloned.stderr.strip() or cloned.stdout.strip()
            raise LifecycleError(f"create lifecycle validation candidate failed: {details}")
        ensure_operational_memory(
            candidate,
            application=application,
            health_objective=health_objective,
            health_judge_artifact=artifact,
            architecture_summary_markdown=deployer.architecture_summary_markdown,
        )
        return validator.run(candidate), _diagnostics_digest(candidate)


def _run_workspace_authored_candidate(
    root: Path,
    *,
    application: str,
    health_objective: str,
    deployer: DeployerAssessment,
    active_resources: list[ActiveTopologyResourceDTO] | None,
    round_index: int,
    previous: HealthJudgeArtifact | None,
    correction_feedback: str | None,
    backend: WorkspaceHealthJudgeBackend,
    validator: SandboxRunner,
) -> tuple[HealthJudgeArtifact, SandboxResult, str]:
    """Let a judge edit a disposable checkout, then validate it independently."""

    with tempfile.TemporaryDirectory(prefix="sdo-lifecycle-authoring-") as temp_dir:
        candidate = Path(temp_dir) / "application"
        cloned = subprocess.run(
            ["git", "clone", "--quiet", "--no-hardlinks", str(root), str(candidate)],
            check=False,
            capture_output=True,
            text=True,
        )
        if cloned.returncode != 0:
            details = cloned.stderr.strip() or cloned.stdout.strip()
            raise LifecycleError(f"create lifecycle authoring candidate failed: {details}")
        ensure_operational_memory(
            candidate,
            application=application,
            health_objective=health_objective,
            health_judge_artifact=previous,
            architecture_summary_markdown=deployer.architecture_summary_markdown,
        )
        _write_health_judge_authoring_context(
            candidate,
            deployer=deployer,
            health_objective=health_objective,
            round_index=round_index,
            active_resources=active_resources,
        )
        _commit(candidate, "sdo: add controller-authored detector context")
        baseline_commit = _git(candidate, "rev-parse", "HEAD")
        metadata: HealthJudgeWorkspaceArtifact = backend.run_health_judge_workspace(
            repository=candidate,
            application=application,
            health_objective=health_objective,
            deployer=deployer,
            active_resources=active_resources,
            round_index=round_index,
            previous=previous,
            correction_feedback=correction_feedback,
        )
        allowed = {
            ".sdo/diagnostics/detectors/health/objective/detector.go",
            ".sdo/diagnostics/detectors/health/objective/detector_test.go",
        }
        changed = set(_git(candidate, "diff", "--name-only", baseline_commit).splitlines())
        changed.update(_git(candidate, "ls-files", "--others", "--exclude-standard").splitlines())
        unexpected = sorted(
            path for path in changed if path and path not in allowed and not _is_judge_traffic_path(path)
        )
        if unexpected:
            raise LifecycleError("health judge edited files outside its ownership: " + ", ".join(unexpected))
        detector = candidate / ".sdo/diagnostics/detectors/health/objective/detector.go"
        detector_test = candidate / ".sdo/diagnostics/detectors/health/objective/detector_test.go"
        source = _canonicalize_health_registration(detector.read_text(encoding="utf-8"))
        detector.write_text(source.rstrip() + "\n", encoding="utf-8")
        artifact = HealthJudgeArtifact(
            **metadata.model_dump(exclude={"session_id"}),
            session_id=metadata.session_id,
            detector_source=source,
            detector_test_source=detector_test.read_text(encoding="utf-8"),
            traffic_files=_authored_traffic_files(candidate),
        )
        # The judge authors traffic; the lifecycle installs the detectors that judge it.
        _ensure_generic_health_detectors(candidate)
        return artifact, validator.run(candidate), _diagnostics_digest(candidate)


def _validator_identity(validator: SandboxRunner) -> str | None:
    identity_method = getattr(validator, "validation_identity", None)
    if not callable(identity_method):
        return None
    identity = identity_method()
    return identity if isinstance(identity, str) and identity.strip() else None


def _diagnostics_digest(root: Path) -> str:
    diagnostics = root / ".sdo" / "diagnostics"
    digest = hashlib.sha256()
    for path in sorted(diagnostics.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(diagnostics).as_posix()
        digest.update(relative.encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def ensure_operational_memory(
    app_root: Path,
    *,
    application: str,
    health_objective: str,
    health_judge_plan: dict[str, object] | None = None,
    health_judge_artifact: HealthJudgeArtifact | None = None,
    architecture_summary_markdown: str | None = None,
    lifecycle_provenance: dict[str, object] | None = None,
) -> str:
    root = app_root.resolve()
    if not (root / ".git").exists():
        raise LifecycleError(f"application workspace is not a Git repository: {root}")
    memory = root / ".sdo"
    fingerprint = _topology_fingerprint(root)
    source_commit = _git(root, "rev-parse", "HEAD")
    plan = health_judge_plan or _judge_assessment(
        {
            "health_objective": health_objective,
            "deployer_assessment": _deployer_assessment({"repository": str(root), "application": application}),
        }
    )
    if memory.exists():
        if health_judge_artifact is not None or lifecycle_provenance is not None:
            _refresh_model_backed_operational_memory(
                root,
                application=application,
                fingerprint=fingerprint,
                source_commit=source_commit,
                health_judge_artifact=health_judge_artifact,
                architecture_summary_markdown=architecture_summary_markdown,
                lifecycle_provenance=lifecycle_provenance,
            )
            return _git(root, "rev-parse", "HEAD")
        _refresh_architecture_if_needed(root, application, fingerprint, source_commit)
        _upgrade_health_detector_if_needed(root, plan)
        _ensure_health_configmap_watch(root)
        _ensure_generic_health_detectors(root)
        if _git(root, "status", "--porcelain", "--", ".sdo"):
            _commit(root, "sdo: install generic symptom health detectors")
        return _git(root, "rev-parse", "HEAD")

    detector = memory / "diagnostics" / "detectors" / "health" / "objective"
    playbook = memory / "playbooks" / "health-objective"
    detector.mkdir(parents=True)
    playbook.mkdir(parents=True)
    (memory / "schema-version").write_text("1\n", encoding="utf-8")
    (memory / "goal.md").write_text(
        f"""---
schema_version: 1
owner: human
application: {application}
---
# Health objective

{health_objective.strip()}
""",
        encoding="utf-8",
    )
    (memory / "arch.md").write_text(
        _architecture_document(
            root,
            application,
            fingerprint,
            source_commit,
            authored_summary=architecture_summary_markdown,
        ),
        encoding="utf-8",
    )
    (memory / "outcomes.jsonl").write_text("", encoding="utf-8")
    if lifecycle_provenance is not None:
        (memory / "lifecycle-provenance.yaml").write_text(
            yaml.safe_dump(lifecycle_provenance, sort_keys=True),
            encoding="utf-8",
        )
    (memory / "playbooks" / "README.md").write_text(
        "# Playbooks\n\n- [Health objective](health-objective/README.md)\n",
        encoding="utf-8",
    )
    (playbook / "README.md").write_text(
        """---
schema_version: 1
owner: responder
fault_class: health-objective-failure
originating_incident: lifecycle-bootstrap
---
# Health objective failure

Inspect `<AFFECTED_RESOURCE>`, repair the source of truth, redeploy, and verify the human-owned objective.
""",
        encoding="utf-8",
    )
    (memory / "diagnostics" / "manifest.yaml").write_text(
        """apiVersion: sdo.dev/v1alpha1
kind: DetectorManifest
sdkVersion: v0.1
detectors:
  - id: health-objective
    package: ./detectors/health/objective
    constructor: New
    class: health
    owner: health_judge
    watches:
      - apiVersion: v1
        kind: Pod
      - apiVersion: v1
        kind: ConfigMap
      - apiVersion: v1
        kind: Service
      - apiVersion: apps/v1
        kind: Deployment
      - apiVersion: networking.k8s.io/v1
        kind: NetworkPolicy
      - apiVersion: v1
        kind: Endpoints
      - apiVersion: discovery.k8s.io/v1
        kind: EndpointSlice
    interval: 30s
    persistence:
      firing: 2
      clearing: 2
    batching:
      severity: critical
      debounce: 500ms
    possiblePlaybooks:
      - .sdo/playbooks/health-objective/README.md
    originatingCommit: lifecycle-bootstrap
""",
        encoding="utf-8",
    )
    (memory / "diagnostics" / "go.mod").write_text(
        "module app-diagnostics\n\ngo 1.24\n\nrequire sdo.dev/controller/sdk v0.0.0\n",
        encoding="utf-8",
    )
    if health_judge_artifact is None:
        _write_health_detector(detector, plan)
    else:
        _write_authored_health_detector(detector, health_judge_artifact)
        _write_traffic_files(root, health_judge_artifact.traffic_files)
    _ensure_generic_health_detectors(root)
    _commit(root, "sdo: capture goal, architecture, and independent health judge")
    return _git(root, "rev-parse", "HEAD")


def _refresh_model_backed_operational_memory(
    root: Path,
    *,
    application: str,
    fingerprint: str,
    source_commit: str,
    health_judge_artifact: HealthJudgeArtifact | None,
    architecture_summary_markdown: str | None,
    lifecycle_provenance: dict[str, object] | None,
) -> None:
    memory = root / ".sdo"
    architecture = memory / "arch.md"
    architecture.write_text(
        _architecture_document(
            root,
            application,
            fingerprint,
            source_commit,
            authored_summary=architecture_summary_markdown,
        ),
        encoding="utf-8",
    )
    if health_judge_artifact is not None:
        detector = memory / "diagnostics" / "detectors" / "health" / "objective"
        _write_authored_health_detector(detector, health_judge_artifact)
        _write_traffic_files(root, health_judge_artifact.traffic_files)
    _ensure_health_configmap_watch(root)
    _ensure_generic_health_detectors(root)
    if lifecycle_provenance is not None:
        (memory / "lifecycle-provenance.yaml").write_text(
            yaml.safe_dump(lifecycle_provenance, sort_keys=True),
            encoding="utf-8",
        )
    _commit(root, "sdo: refresh model-backed operational memory")


def _refresh_architecture_if_needed(root: Path, application: str, fingerprint: str, source_commit: str) -> None:
    architecture = root / ".sdo" / "arch.md"
    if not architecture.is_file():
        raise LifecycleError("existing .sdo memory has no arch.md")
    if f"topology_fingerprint: {fingerprint}" in architecture.read_text(encoding="utf-8"):
        return
    architecture.write_text(
        _architecture_document(root, application, fingerprint, source_commit),
        encoding="utf-8",
    )
    _commit(root, "sdo: refresh commit-aware architecture summary")


#: Lifecycle-installed, app-agnostic health detectors built on controller/sdk.
ENDPOINT_DETECTOR_ID = "service-endpoints"
TRAFFIC_DETECTOR_PREFIX = "traffic-"
_HEALTH_PLAYBOOK = ".sdo/playbooks/health-objective/README.md"
_GENERIC_BLOCK_RE = re.compile(
    r"^  # BEGIN lifecycle-installed health detector (?P<id>\S+)\n.*?"
    r"^  # END lifecycle-installed health detector (?P=id)\n",
    re.MULTILINE | re.DOTALL,
)


#: Judge-authored traffic files, relative to ``.sdo/diagnostics/traffic/``;
#: the responder-owned ``generators/incident/`` and ``incident-*`` workloads are excluded.
_JUDGE_TRAFFIC_FILE = re.compile(
    rf"generators/[a-z0-9_]+\.go|workloads/(?!{TRAFFIC_INCIDENT_WORKLOAD_PREFIX})[a-z0-9]([-a-z0-9]{{0,61}}[a-z0-9])?\.yaml"
)


def _is_judge_traffic_path(path: str) -> bool:
    prefix = f"{TRAFFIC_DIRECTORY}/"
    return path.startswith(prefix) and bool(_JUDGE_TRAFFIC_FILE.fullmatch(path.removeprefix(prefix)))


def traffic_detector_id(workload: str) -> str:
    return f"{TRAFFIC_DETECTOR_PREFIX}{workload}"


def _judge_traffic_files(root: Path) -> list[Path]:
    directory = root / TRAFFIC_DIRECTORY
    if not directory.is_dir():
        return []
    return sorted(
        path
        for path in directory.rglob("*")
        if path.is_file() and _JUDGE_TRAFFIC_FILE.fullmatch(path.relative_to(directory).as_posix())
    )


def _write_traffic_files(root: Path, files: list[AuthoredTrafficFile]) -> None:
    """Make the judge's files the complete judge-owned set under ``.sdo/diagnostics/traffic/``."""

    for stale in _judge_traffic_files(root):
        stale.unlink()
    directory = root / TRAFFIC_DIRECTORY
    for authored in files:
        path = directory / authored.path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(authored.content.rstrip() + "\n", encoding="utf-8")


def _authored_traffic_files(root: Path) -> list[AuthoredTrafficFile]:
    directory = root / TRAFFIC_DIRECTORY
    return [
        AuthoredTrafficFile(path=path.relative_to(directory).as_posix(), content=path.read_text(encoding="utf-8"))
        for path in _judge_traffic_files(root)
    ]


def _health_probe_workloads(root: Path) -> list[str]:
    """Judge-owned health-probe workloads; invalid ones are reported by validation, not here."""

    names = []
    for path in _judge_traffic_files(root):
        if path.suffix != ".yaml":
            continue
        try:
            workload = TrafficWorkload.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
        except (yaml.YAMLError, ValueError):
            continue
        if workload.purpose == "health-probe" and workload.name == path.stem:
            names.append(workload.name)
    return sorted(names)


def _generic_registration(detector_id: str, package: str, watches: list[tuple[str, str]], interval: str) -> str:
    rendered_watches = "".join(
        f"      - apiVersion: {api_version}\n        kind: {kind}\n" for api_version, kind in watches
    )
    return (
        f"  # BEGIN lifecycle-installed health detector {detector_id}\n"
        f"  - id: {detector_id}\n"
        f"    package: ./detectors/health/{package}\n"
        "    constructor: New\n"
        "    class: health\n"
        "    owner: health_judge\n"
        "    watches:\n"
        f"{rendered_watches}"
        f"    interval: {interval}\n"
        "    persistence:\n"
        "      firing: 2\n"
        "      clearing: 2\n"
        "    batching:\n"
        "      severity: critical\n"
        "      debounce: 500ms\n"
        "    possiblePlaybooks:\n"
        f"      - {_HEALTH_PLAYBOOK}\n"
        "    originatingCommit: lifecycle-bootstrap\n"
        f"  # END lifecycle-installed health detector {detector_id}\n"
    )


_GENERIC_SPEC_FIELDS = """		Class:       sdk.DetectorClassHealth,
		Owner:       sdk.DetectorOwnerHealthJudge,
		Persistence: sdk.PersistencePolicy{Firing: 2, Clearing: 2},
		Batching:    sdk.BatchingPolicy{Severity: sdk.SeverityCritical, Debounce: 500 * time.Millisecond},
		Playbooks:   []string{".sdo/playbooks/health-objective/README.md"},
		OriginatingCommit: "lifecycle-bootstrap","""


def _endpoint_detector_source() -> str:
    return f"""// Code generated by the SDO lifecycle. DO NOT EDIT.

// Package serviceendpoints installs SDO's generic ready-endpoint health check
// (controller/sdk/servicehealth) for this application.
package serviceendpoints

import (
	"time"

	"sdo.dev/controller/sdk"
	"sdo.dev/controller/sdk/servicehealth"
)

func New() sdk.Detector {{
	return servicehealth.NewReadyEndpointsDetector(sdk.DetectorSpec{{
		ID:          "{ENDPOINT_DETECTOR_ID}",
{_GENERIC_SPEC_FIELDS}
		Watches:     servicehealth.EndpointWatches(),
		Interval:    15 * time.Second,
	}})
}}
"""


def _endpoint_detector_test_source() -> str:
    return f"""// Code generated by the SDO lifecycle. DO NOT EDIT.

package serviceendpoints

import "testing"

func TestRegistration(t *testing.T) {{
	if New().Spec().ID != "{ENDPOINT_DETECTOR_ID}" {{
		t.Fatalf("unexpected detector id %q", New().Spec().ID)
	}}
}}
"""


def _traffic_detector_source(workload: str) -> str:
    return f"""// Code generated by the SDO lifecycle. DO NOT EDIT.

// Package synthetic judges the health judge's health-probe workload
// .sdo/diagnostics/traffic/workloads/{workload}.yaml with controller/sdk/traffic.
package synthetic

import (
	"time"

	"sdo.dev/controller/sdk"
	"sdo.dev/controller/sdk/traffic"
)

func New() sdk.Detector {{
	return traffic.NewDetector(sdk.DetectorSpec{{
		ID:          "{traffic_detector_id(workload)}",
{_GENERIC_SPEC_FIELDS}
		Watches:     []sdk.WatchKind{{traffic.Watch}},
		Interval:    10 * time.Second,
	}}, "{workload}")
}}
"""


def _traffic_detector_test_source(workload: str) -> str:
    return f"""// Code generated by the SDO lifecycle. DO NOT EDIT.

package synthetic

import (
	"testing"

	"sdo.dev/controller/sdk/traffic"
)

func TestRegistration(t *testing.T) {{
	consumer, ok := New().(traffic.Consumer)
	if !ok || len(consumer.TrafficWorkloads()) != 1 || consumer.TrafficWorkloads()[0] != "{workload}" {{
		t.Fatalf("detector must judge traffic workload {workload}")
	}}
}}
"""


def _ensure_generic_health_detectors(root: Path) -> None:
    """Install the endpoint check and one traffic detector per health-probe workload, idempotently.

    Their registrations sit in marked blocks at the end of the detector list so a
    refresh replaces exactly them and never touches responder-owned entries.
    """

    diagnostics = root / ".sdo" / "diagnostics"
    health = diagnostics / "detectors" / "health"
    workloads = _health_probe_workloads(root)
    packages = {
        ENDPOINT_DETECTOR_ID: (_endpoint_detector_source(), _endpoint_detector_test_source()),
        **{
            traffic_detector_id(name): (_traffic_detector_source(name), _traffic_detector_test_source(name))
            for name in workloads
        },
    }
    for stale in health.glob(f"{TRAFFIC_DETECTOR_PREFIX}*"):
        if stale.is_dir() and stale.name not in packages:
            shutil.rmtree(stale)
    for package, (source, test_source) in packages.items():
        directory = health / package
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "detector.go").write_text(source, encoding="utf-8")
        (directory / "detector_test.go").write_text(test_source, encoding="utf-8")

    manifest = diagnostics / "manifest.yaml"
    text = _GENERIC_BLOCK_RE.sub("", manifest.read_text(encoding="utf-8"))
    unmarked = {
        str(detector.get("id"))
        for detector in (yaml.safe_load(text) or {}).get("detectors", [])
        if isinstance(detector, dict)
    }
    blocks = []
    if ENDPOINT_DETECTOR_ID not in unmarked:
        endpoint_watches = [("v1", "Service"), ("v1", "Endpoints"), ("v1", "Pod"), ("apps/v1", "Deployment")]
        blocks.append(_generic_registration(ENDPOINT_DETECTOR_ID, ENDPOINT_DETECTOR_ID, endpoint_watches, "15s"))
    for name in workloads:
        detector_id = traffic_detector_id(name)
        if detector_id not in unmarked:
            blocks.append(
                _generic_registration(detector_id, detector_id, [("sdo.dev/v1alpha1", "SyntheticTraffic")], "10s")
            )
    head, separator, tail = text.partition("detectors:\n")
    if not separator:
        raise LifecycleError("diagnostics manifest has no detectors list")
    lines = tail.splitlines(keepends=True)
    end = next(
        (index for index, line in enumerate(lines) if line.strip() and not line[0].isspace() and line[0] != "-"),
        len(lines),
    )
    list_body, rest = "".join(lines[:end]), "".join(lines[end:])
    if list_body and not list_body.endswith("\n"):
        list_body += "\n"
    manifest.write_text(head + separator + list_body + "".join(blocks) + rest, encoding="utf-8")


_GENERATOR_TARGET_RE = re.compile(r'Service:\s*"([^"]+)"')


def _traffic_errors(files: list[AuthoredTrafficFile], deployer: DeployerAssessment) -> list[str]:
    """Check judge-authored traffic before the validator compiles and tests it."""

    services = {resource.name for resource in deployer.resources if resource.kind == "Service"}
    paths = [authored.path for authored in files]
    errors = [
        f"traffic file {duplicate!r} is authored more than once"
        for duplicate in sorted({path for path in paths if paths.count(path) > 1})
    ]
    generators = [authored for authored in files if authored.path.startswith("generators/")]
    workloads = [authored for authored in files if authored.path.startswith("workloads/")]
    errors.extend(
        f"traffic file {authored.path!r} must be generators/<file>.go or workloads/<name>.yaml "
        f"(not an {TRAFFIC_INCIDENT_WORKLOAD_PREFIX}* workload)"
        for authored in files
        if not _JUDGE_TRAFFIC_FILE.fullmatch(authored.path)
    )
    if workloads and not generators:
        errors.append("traffic workloads need a generators/ Go package providing their scenarios")
    for authored in generators:
        if not re.search(r"^package generators\s*$", authored.content, re.MULTILINE):
            errors.append(f"traffic file {authored.path!r} must be in package generators")
        errors.extend(
            f"traffic file {authored.path!r} targets Service/{service}, which is not a source-backed Service "
            "in the deployer handoff"
            for service in sorted(set(_GENERATOR_TARGET_RE.findall(authored.content)) - services)
        )
    health_probes = 0
    for authored in workloads:
        name = Path(authored.path).stem
        try:
            workload = TrafficWorkload.model_validate(yaml.safe_load(authored.content))
        except (yaml.YAMLError, ValueError) as exc:
            errors.append(f"traffic workload {authored.path!r} is invalid: {exc}")
            continue
        if workload.name != name:
            errors.append(f"traffic workload {authored.path!r} declares name {workload.name!r}; it must match its file")
        health_probes += workload.purpose == "health-probe"
    if generators and not health_probes:
        errors.append("traffic generators need a health-probe workload that runs them continuously")
    return errors


def _ensure_health_configmap_watch(root: Path) -> None:
    manifest = root / ".sdo" / "diagnostics" / "manifest.yaml"
    manifest_text = manifest.read_text(encoding="utf-8")
    if "kind: ConfigMap" in manifest_text:
        return
    updated = manifest_text.replace(
        "      - apiVersion: v1\n        kind: Pod\n",
        "      - apiVersion: v1\n        kind: Pod\n      - apiVersion: v1\n        kind: ConfigMap\n",
    )
    if updated == manifest_text:
        raise LifecycleError("health detector manifest has no Pod watch anchor for ConfigMap watch")
    manifest.write_text(updated, encoding="utf-8")


def _upgrade_health_detector_if_needed(root: Path, plan: dict[str, object]) -> None:
    detector = root / ".sdo" / "diagnostics" / "detectors" / "health" / "objective"
    source = detector / "detector.go"
    if not source.is_file():
        return
    source_text = source.read_text(encoding="utf-8")
    desired_source = _render_health_detector(plan)
    if source_text == desired_source:
        return
    _write_health_detector(detector, plan)
    _ensure_health_configmap_watch(root)
    manifest = root / ".sdo" / "diagnostics" / "manifest.yaml"
    manifest_text = manifest.read_text(encoding="utf-8")
    if "kind: NetworkPolicy" not in manifest_text:
        manifest_text = manifest_text.replace(
            "      - apiVersion: apps/v1\n        kind: Deployment\n",
            "      - apiVersion: apps/v1\n        kind: Deployment\n"
            "      - apiVersion: networking.k8s.io/v1\n        kind: NetworkPolicy\n",
        )
    if manifest_text != manifest.read_text(encoding="utf-8"):
        manifest.write_text(manifest_text, encoding="utf-8")
    _commit(root, "sdo: upgrade independent health judge")


def _write_health_detector(detector: Path, plan: dict[str, object]) -> None:
    detector.mkdir(parents=True, exist_ok=True)
    (detector / "detector.go").write_text(_render_health_detector(plan), encoding="utf-8")
    (detector / "detector_test.go").write_text(_HEALTH_DETECTOR_TEST_SOURCE, encoding="utf-8")


def _write_authored_health_detector(detector: Path, artifact: HealthJudgeArtifact) -> None:
    detector.mkdir(parents=True, exist_ok=True)
    source = _canonicalize_health_registration(artifact.detector_source)
    (detector / "detector.go").write_text(source.rstrip() + "\n", encoding="utf-8")
    (detector / "detector_test.go").write_text(artifact.detector_test_source.rstrip() + "\n", encoding="utf-8")


_SPEC_SIGNATURE = re.compile(
    r"func\s*\((?P<receiver>[^)]*)\)\s*Spec\s*\(\s*\)\s*sdk\.DetectorSpec\s*\{",
)


def _canonicalize_health_registration(source: str) -> str:
    """Enforce controller-owned registration around judge-authored checks."""

    match = _SPEC_SIGNATURE.search(source)
    if match is None:
        raise LifecycleError("health detector has no replaceable Spec() sdk.DetectorSpec method")
    opening_brace = match.end() - 1
    closing_brace = _matching_go_brace(source, opening_brace)
    receiver = match.group("receiver").strip()
    canonical = f"""func ({receiver}) Spec() sdk.DetectorSpec {{
	return sdk.DetectorSpec{{
		ID: "health-objective", Class: sdk.DetectorClassHealth, Owner: sdk.DetectorOwnerHealthJudge,
		Interval: 30 * time.Second,
		Watches: []sdk.WatchKind{{
			{{APIVersion: "v1", Kind: "Pod"}},
			{{APIVersion: "v1", Kind: "ConfigMap"}},
			{{APIVersion: "v1", Kind: "Service"}},
			{{APIVersion: "apps/v1", Kind: "Deployment"}},
			{{APIVersion: "networking.k8s.io/v1", Kind: "NetworkPolicy"}},
			{{APIVersion: "v1", Kind: "Endpoints"}},
			{{APIVersion: "discovery.k8s.io/v1", Kind: "EndpointSlice"}},
		}},
		Persistence: sdk.PersistencePolicy{{Firing: 2, Clearing: 2}},
		Batching: sdk.BatchingPolicy{{Severity: sdk.SeverityCritical, Debounce: 500 * time.Millisecond}},
		Playbooks: []string{{".sdo/playbooks/health-objective/README.md"}},
		OriginatingCommit: "lifecycle-bootstrap",
	}}
}}"""
    replaced_spec = source[match.start() : closing_brace + 1]
    replaced = source[: match.start()] + canonical + source[closing_brace + 1 :]
    replaced_qualifiers = set(re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\.", replaced_spec))
    return _prune_unused_aliased_go_imports(replaced, replaced_qualifiers=replaced_qualifiers)


_ALIASED_GO_IMPORT = re.compile(
    r'^(?P<indent>[ \t]*)(?P<alias>[A-Za-z_][A-Za-z0-9_]*)[ \t]+"(?P<path>[^"]+)"[ \t]*$',
    re.MULTILINE,
)
_DEFAULT_GO_IMPORT = re.compile(
    r'^(?P<indent>[ \t]*)"(?P<path>[^"]+)"[ \t]*$',
    re.MULTILINE,
)
_GO_IMPORT_BLOCK = re.compile(r"^import\s*\((?P<body>.*?)^\)", re.MULTILINE | re.DOTALL)


def _prune_unused_aliased_go_imports(source: str, *, replaced_qualifiers: set[str]) -> str:
    """Remove imports made unused when the controller replaces the authored Spec."""

    aliased_imports: list[tuple[int, int, str]] = []
    default_imports: list[tuple[int, int, str]] = []
    for block in _GO_IMPORT_BLOCK.finditer(source):
        body_start = block.start("body")
        aliased_imports.extend(
            (body_start + match.start(), body_start + match.end(), match.group("alias"))
            for match in _ALIASED_GO_IMPORT.finditer(block.group("body"))
        )
        default_imports.extend(
            (
                body_start + match.start(),
                body_start + match.end(),
                _default_go_package_name(match.group("path")),
            )
            for match in _DEFAULT_GO_IMPORT.finditer(block.group("body"))
        )
    import_ranges = [(start, end) for start, end, _name in [*aliased_imports, *default_imports]]
    body = source
    for start, end in sorted(import_ranges, reverse=True):
        body = body[:start] + (" " * (end - start)) + body[end:]

    unused_ranges: list[tuple[int, int]] = [
        (start, end)
        for start, end, alias in aliased_imports
        if alias not in {"_", "."} and re.search(rf"\b{re.escape(alias)}\s*\.", body) is None
    ]
    unused_ranges.extend(
        (start, end)
        for start, end, package_name in default_imports
        if package_name in replaced_qualifiers and re.search(rf"\b{re.escape(package_name)}\s*\.", body) is None
    )
    for start, end in sorted(unused_ranges, reverse=True):
        source = source[:start] + source[end:]
    return source


def _default_go_package_name(import_path: str) -> str:
    segment = import_path.rsplit("/", 1)[-1]
    match = re.match(r"[A-Za-z_][A-Za-z0-9_]*", segment)
    return match.group(0) if match is not None else segment


def _matching_go_brace(source: str, opening_brace: int) -> int:
    depth = 0
    quote = ""
    escaped = False
    line_comment = False
    block_comment = False
    index = opening_brace
    while index < len(source):
        current = source[index]
        following = source[index + 1] if index + 1 < len(source) else ""
        if line_comment:
            if current == "\n":
                line_comment = False
        elif block_comment:
            if current == "*" and following == "/":
                block_comment = False
                index += 1
        elif quote:
            if escaped:
                escaped = False
            elif current == "\\" and quote != "`":
                escaped = True
            elif current == quote:
                quote = ""
        elif current == "/" and following == "/":
            line_comment = True
            index += 1
        elif current == "/" and following == "*":
            block_comment = True
            index += 1
        elif current in {'"', "'", "`"}:
            quote = current
        elif current == "{":
            depth += 1
        elif current == "}":
            depth -= 1
            if depth == 0:
                return index
        index += 1
    raise LifecycleError("health detector Spec() method has unbalanced braces")


def _render_health_detector(plan: dict[str, object]) -> str:
    def map_entries(field: str) -> str:
        values = plan.get(field, [])
        if not isinstance(values, list):
            raise LifecycleError(f"health judge plan field {field!r} must be a list")
        return "\n".join(f"\t{json.dumps(str(value))}: {{}}," for value in values)

    digest = str(plan.get("objective_digest", ""))
    if len(digest) != 64:
        raise LifecycleError("health judge plan requires a SHA-256 objective digest")
    raw_label_sets = plan.get("workload_label_sets", [])
    if not isinstance(raw_label_sets, list):
        raise LifecycleError("health judge plan field 'workload_label_sets' must be a list")
    label_sets = []
    for raw_labels in raw_label_sets:
        if not isinstance(raw_labels, dict):
            raise LifecycleError("health judge workload labels must be mappings")
        entries = ", ".join(
            f"{json.dumps(str(key))}: {json.dumps(str(value))}" for key, value in sorted(raw_labels.items())
        )
        label_sets.append(f"\t{{{entries}}},")
    return (
        _HEALTH_DETECTOR_SOURCE.replace("__OBJECTIVE_DIGEST__", digest)
        .replace("__REQUIRED_DEPLOYMENTS__", map_entries("deployment_names"))
        .replace("__REQUIRED_SERVICES__", map_entries("service_names"))
        .replace("__REQUIRED_WORKLOAD_LABELS__", "\n".join(label_sets))
    )


def _architecture_document(
    root: Path,
    application: str,
    fingerprint: str,
    source_commit: str,
    *,
    authored_summary: str | None = None,
) -> str:
    tracked = [path for path in _git(root, "ls-files").splitlines() if path and not path.startswith(".sdo/")]
    resources = _topology_resources(root, tracked)
    if resources:
        resource_rows = "\n".join(
            f"| {resource.kind}/{resource.name} | {resource.namespace} | {resource.source} | "
            f"{', '.join(resource.dependencies) or 'none declared'} |"
            for resource in resources
        )
        topology = (
            "## Source-backed Kubernetes topology\n\n"
            "| Resource | Namespace | Source | Declared dependencies/selectors |\n"
            "| --- | --- | --- | --- |\n"
            f"{resource_rows}\n"
        )
    else:
        topology = "## Source-backed Kubernetes topology\n\nNo Kubernetes resources were found in tracked YAML files.\n"
    generated_summary = f"""# Architecture

The application source currently contains {len(tracked)} tracked deployment and implementation files.
The table below is derived from source manifests; confirm it against the live namespace before acting.

{topology}"""
    body = authored_summary.strip() if authored_summary else generated_summary.strip()
    return f"""---
schema_version: 1
generated_at_commit: {source_commit}
generated_at: {datetime.now(timezone.utc).isoformat()}
application: {application}
topology_fingerprint: {fingerprint}
---
{body}
"""


def _topology_resources(root: Path, tracked: list[str]) -> list[TopologyResource]:
    resources: list[TopologyResource] = []
    for relative in tracked:
        if Path(relative).suffix.lower() not in {".yaml", ".yml"}:
            continue
        try:
            documents = list(yaml.safe_load_all((root / relative).read_text(encoding="utf-8")))
        except (OSError, UnicodeDecodeError, yaml.YAMLError):
            continue
        for document in documents:
            if not isinstance(document, dict):
                continue
            metadata = document.get("metadata")
            if not isinstance(metadata, dict):
                continue
            kind = document.get("kind")
            name = metadata.get("name")
            if not isinstance(kind, str) or not isinstance(name, str) or not kind or not name:
                continue
            namespace = metadata.get("namespace", "default")
            resources.append(
                TopologyResource(
                    kind=kind,
                    name=name,
                    namespace=namespace if isinstance(namespace, str) and namespace else "default",
                    source=relative,
                    dependencies=_resource_dependencies(document),
                )
            )
    return sorted(resources, key=lambda item: (item.namespace, item.kind, item.name, item.source))


def _resource_dependencies(document: dict[object, object]) -> tuple[str, ...]:
    dependencies: set[str] = set()
    spec = document.get("spec")
    if not isinstance(spec, dict):
        return ()
    selector = spec.get("selector")
    if isinstance(selector, dict):
        for key, value in selector.items():
            if isinstance(key, str) and isinstance(value, str):
                dependencies.add(f"selector:{key}={value}")
    template = spec.get("template")
    template_metadata = template.get("metadata") if isinstance(template, dict) else None
    template_labels = template_metadata.get("labels") if isinstance(template_metadata, dict) else None
    if isinstance(template_labels, dict):
        for key, value in template_labels.items():
            if isinstance(key, str) and isinstance(value, str):
                dependencies.add(f"pod-label:{key}={value}")
    pod_spec = template.get("spec") if isinstance(template, dict) else None
    if not isinstance(pod_spec, dict):
        return tuple(sorted(dependencies))
    service_account = pod_spec.get("serviceAccountName")
    if isinstance(service_account, str) and service_account:
        dependencies.add(f"ServiceAccount/{service_account}")
    for volume in pod_spec.get("volumes", []):
        if not isinstance(volume, dict):
            continue
        config_map = volume.get("configMap")
        if isinstance(config_map, dict) and isinstance(config_map.get("name"), str):
            dependencies.add(f"ConfigMap/{config_map['name']}")
    for container_group in ("initContainers", "containers"):
        for container in pod_spec.get(container_group, []):
            if not isinstance(container, dict):
                continue
            image = container.get("image")
            if isinstance(image, str) and image:
                dependencies.add(f"image:{image}")
            for source in container.get("envFrom", []):
                if not isinstance(source, dict):
                    continue
                config_map = source.get("configMapRef")
                if isinstance(config_map, dict) and isinstance(config_map.get("name"), str):
                    dependencies.add(f"ConfigMap/{config_map['name']}")
    return tuple(sorted(dependencies))


def _topology_fingerprint(root: Path) -> str:
    digest = hashlib.sha256()
    for relative in sorted(_git(root, "ls-files").splitlines()):
        if not relative or relative.startswith(".sdo/"):
            continue
        path = root / relative
        if not path.is_file():
            continue
        digest.update(relative.encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _commit(root: Path, message: str) -> None:
    _git(root, "add", "--all", "--", ".sdo")
    _git(
        root,
        "-c",
        "user.name=SDO Lifecycle",
        "-c",
        "user.email=sdo-lifecycle@localhost",
        "commit",
        "-m",
        message,
    )


def _git(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *args],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        details = completed.stderr.strip() or completed.stdout.strip()
        raise LifecycleError(f"git {' '.join(args)} failed: {details}")
    return completed.stdout.strip()


_HEALTH_DETECTOR_SOURCE = r"""package objective

import (
	"context"
	"fmt"
	"time"

	corev1 "k8s.io/api/core/v1"
	networkingv1 "k8s.io/api/networking/v1"
	"sdo.dev/controller/sdk"
)

const deterministicHealthDetectorVersion = "v3"
const healthObjectiveDigest = "__OBJECTIVE_DIGEST__"

var requiredDeployments = map[string]struct{}{
__REQUIRED_DEPLOYMENTS__
}

var requiredServices = map[string]struct{}{
__REQUIRED_SERVICES__
}

var requiredWorkloadLabels = []map[string]string{
__REQUIRED_WORKLOAD_LABELS__
}

func isRequiredDeployment(name string) bool {
	_, required := requiredDeployments[name]
	return required
}

func isRequiredService(name string) bool {
	_, required := requiredServices[name]
	return required
}

func policySelectsRequiredWorkload(selector map[string]string) bool {
	for _, labels := range requiredWorkloadLabels {
		matches := true
		for key, value := range selector {
			if labels[key] != value {
				matches = false
				break
			}
		}
		if matches {
			return true
		}
	}
	return false
}

type Detector struct{}

func New() sdk.Detector { return Detector{} }

func (Detector) Spec() sdk.DetectorSpec {
	return sdk.DetectorSpec{
		ID: "health-objective", Class: sdk.DetectorClassHealth, Owner: sdk.DetectorOwnerHealthJudge,
		Interval: 30 * time.Second,
		Watches: []sdk.WatchKind{
			{APIVersion: "v1", Kind: "Pod"}, {APIVersion: "v1", Kind: "ConfigMap"},
			{APIVersion: "v1", Kind: "Service"},
			{APIVersion: "apps/v1", Kind: "Deployment"},
			{APIVersion: "networking.k8s.io/v1", Kind: "NetworkPolicy"},
			{APIVersion: "v1", Kind: "Endpoints"},
			{APIVersion: "discovery.k8s.io/v1", Kind: "EndpointSlice"},
		},
		Persistence: sdk.PersistencePolicy{Firing: 2, Clearing: 2},
		Batching: sdk.BatchingPolicy{Severity: sdk.SeverityCritical, Debounce: 500 * time.Millisecond},
		Playbooks: []string{".sdo/playbooks/health-objective/README.md"},
		OriginatingCommit: "lifecycle-bootstrap",
	}
}

func (Detector) Detect(_ context.Context, snapshot sdk.DetectionContext) ([]sdk.Finding, error) {
	findings := make([]sdk.Finding, 0)
	for _, deployment := range snapshot.Deployments() {
		if !isRequiredDeployment(deployment.Name) {
			continue
		}
		desired := int32(1)
		if deployment.Spec.Replicas != nil {
			desired = *deployment.Spec.Replicas
		}
		if deployment.Status.AvailableReplicas < desired {
			findings = append(findings, healthFinding(
				"deployment-unavailable",
				"A required deployment has unavailable replicas",
				fmt.Sprintf(
					"deployment %s/%s has %d/%d available replicas",
					deployment.Namespace,
					deployment.Name,
					deployment.Status.AvailableReplicas,
					desired,
				),
				sdk.ObjectRefFrom("Deployment", "apps/v1", &deployment),
			))
		}
	}
	for _, service := range snapshot.Services() {
		if !isRequiredService(service.Name) {
			continue
		}
		if service.Spec.Type == corev1.ServiceTypeExternalName || len(service.Spec.Selector) == 0 {
			continue
		}
		if snapshot.ReadyEndpointCountForService(service.Namespace, service.Name) == 0 {
			findings = append(findings, healthFinding(
				"service-without-ready-endpoints",
				"A selected service has no ready endpoints",
				fmt.Sprintf("service %s/%s has zero ready endpoints", service.Namespace, service.Name),
				sdk.ObjectRefFrom("Service", "v1", &service),
			))
		}
	}
	for _, policy := range snapshot.NetworkPolicies() {
		if !policySelectsRequiredWorkload(policy.Spec.PodSelector.MatchLabels) {
			continue
		}
		isolatesIngress := false
		isolatesEgress := false
		for _, policyType := range policy.Spec.PolicyTypes {
			isolatesIngress = isolatesIngress || policyType == networkingv1.PolicyTypeIngress
			isolatesEgress = isolatesEgress || policyType == networkingv1.PolicyTypeEgress
		}
		if isolatesIngress && len(policy.Spec.Ingress) == 0 && isolatesEgress && len(policy.Spec.Egress) == 0 {
			findings = append(findings, healthFinding(
				"network-policy-total-isolation",
				"A network policy completely isolates selected application pods",
				fmt.Sprintf("networkpolicy %s/%s denies all ingress and egress", policy.Namespace, policy.Name),
				sdk.ObjectRefFrom("NetworkPolicy", "networking.k8s.io/v1", &policy),
			))
		}
	}
	return findings, nil
}

func healthFinding(ruleID string, summary string, evidence string, resource sdk.ObjectRef) sdk.Finding {
	return sdk.Finding{
		DetectorID: "health-objective", RuleID: ruleID, Status: sdk.FindingActive,
		Severity: sdk.SeverityCritical, Summary: summary, Evidence: evidence,
		PrimaryResource: resource,
		Playbooks: []string{".sdo/playbooks/health-objective/README.md"},
		Fingerprint: "health-objective/" + ruleID + "/" + resource.Namespace + "/" + resource.Name,
	}
}
"""


_HEALTH_DETECTOR_TEST_SOURCE = r"""package objective

import (
	"context"
	"testing"

	appsv1 "k8s.io/api/apps/v1"
	corev1 "k8s.io/api/core/v1"
	networkingv1 "k8s.io/api/networking/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"sdo.dev/controller/sdk/sdktest"
)

func TestDetectUsesOnlyObjectiveSpecificDeploymentTargets(t *testing.T) {
	target := ""
	for name := range requiredDeployments {
		target = name
		break
	}
	if target == "" {
		t.Skip("objective has no required Deployment")
	}
	replicas := int32(1)
	findings, err := (Detector{}).Detect(context.Background(), sdktest.Snapshot{
		NamespaceName: "demo",
		DeploymentList: []appsv1.Deployment{
			{
				ObjectMeta: metav1.ObjectMeta{Name: target, Namespace: "demo"},
				Spec: appsv1.DeploymentSpec{Replicas: &replicas},
				Status: appsv1.DeploymentStatus{AvailableReplicas: 0},
			},
			{
				ObjectMeta: metav1.ObjectMeta{Name: "unrelated-workload", Namespace: "demo"},
				Spec: appsv1.DeploymentSpec{Replicas: &replicas},
				Status: appsv1.DeploymentStatus{AvailableReplicas: 0},
			},
		},
	})
	if err != nil {
		t.Fatalf("detect live health: %v", err)
	}
	if len(findings) != 1 || findings[0].PrimaryResource.Name != target {
		t.Fatalf("expected only the objective-specific deployment finding, got %#v", findings)
	}
}

func TestDetectUsesOnlyObjectiveSpecificServiceTargets(t *testing.T) {
	target := ""
	for name := range requiredServices {
		target = name
		break
	}
	if target == "" {
		t.Skip("objective has no required Service")
	}
	findings, err := (Detector{}).Detect(context.Background(), sdktest.Snapshot{
		NamespaceName: "demo",
		ServiceList: []corev1.Service{
			{
				ObjectMeta: metav1.ObjectMeta{Name: target, Namespace: "demo"},
				Spec: corev1.ServiceSpec{Selector: map[string]string{"app": target}},
			},
			{
				ObjectMeta: metav1.ObjectMeta{Name: "unrelated-service", Namespace: "demo"},
				Spec: corev1.ServiceSpec{Selector: map[string]string{"app": "other"}},
			},
		},
	})
	if err != nil {
		t.Fatalf("detect live service health: %v", err)
	}
	if len(findings) != 1 || findings[0].PrimaryResource.Name != target {
		t.Fatalf("expected only the objective-specific service finding, got %#v", findings)
	}
}

func TestDetectRejectsTotalNetworkIsolationWithoutBenchmarkVerdict(t *testing.T) {
	if len(requiredWorkloadLabels) == 0 {
		t.Skip("objective has no source-backed workload labels")
	}
	findings, err := (Detector{}).Detect(context.Background(), sdktest.Snapshot{
		NamespaceName: "demo",
		NetworkPolicyList: []networkingv1.NetworkPolicy{{
			ObjectMeta: metav1.ObjectMeta{Name: "deny-all-payment", Namespace: "demo"},
			Spec: networkingv1.NetworkPolicySpec{
				PodSelector: metav1.LabelSelector{MatchLabels: requiredWorkloadLabels[0]},
				PolicyTypes: []networkingv1.PolicyType{networkingv1.PolicyTypeIngress, networkingv1.PolicyTypeEgress},
				Ingress: []networkingv1.NetworkPolicyIngressRule{},
				Egress: []networkingv1.NetworkPolicyEgressRule{},
			},
		}},
	})
	if err != nil {
		t.Fatalf("detect isolated workload: %v", err)
	}
	if len(findings) != 1 || findings[0].RuleID != "network-policy-total-isolation" {
		t.Fatalf("unexpected isolation findings: %#v", findings)
	}
}
"""
