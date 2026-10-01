from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

from sdo.operational_memory.models import (
    INCIDENT_DETECTOR_MAX_FIRING,
    TRAFFIC_DIRECTORY,
    TRAFFIC_INCIDENT_GENERATORS_DIRECTORY,
    TRAFFIC_INCIDENT_WORKLOAD_PREFIX,
    TRAFFIC_WORKLOAD_DIRECTORY,
    ArtifactOwner,
    ValidatorNetworkPolicyCanary,
)
from sdo.operational_memory.repository import MemoryRepository, MemoryRepositoryError
from sdo.operational_memory.sandbox import ContainerSandboxRunner, healthy_baseline_argument

if TYPE_CHECKING:
    from sdo.operational_memory.sandbox import SandboxRunner

#: Every playbook body must contain at least one role placeholder matching this pattern.
PLACEHOLDER_RE = re.compile(r"<[A-Z][A-Z0-9_]+>")
#: Index that must link every ``.sdo/playbooks/<fault-class>/README.md``.
PLAYBOOK_INDEX_PATH = PurePosixPath(".sdo/playbooks/README.md")
#: Required extension of files under ``.sdo/playbooks/<fault-class>/scripts/``.
PLAYBOOK_SCRIPT_SUFFIX = ".sh"
MARKDOWN_LINK_RE = re.compile(r"\[[^]]+\]\(([^)]+)\)")
SPEC_PROVENANCE_RE = re.compile(r'\b(OriginatingIncident|OriginatingCommit)\s*:\s*("(?:[^"\\]|\\.)*"|`[^`]*`)')


class MemoryValidationError(ValueError):
    """Raised when a proposed operational-memory tree violates a trust gate."""


class MemoryValidator:
    def __init__(
        self,
        *,
        run_diagnostics: bool = True,
        sandbox_runner: SandboxRunner | None = None,
        healthy_baseline_source: Path | None = None,
        healthy_baseline_dir: str | None = None,
    ) -> None:
        self.run_diagnostics = run_diagnostics
        self.sandbox_runner = sandbox_runner or ContainerSandboxRunner()
        # Opt-in: copy recorded healthy-cluster snapshots from this directory into the validated tree
        # at ``healthy_baseline_dir`` for the executable gate only (the sandbox runner passes the flag).
        self.healthy_baseline_source = healthy_baseline_source
        if healthy_baseline_source is not None and healthy_baseline_dir is None:
            raise ValueError("healthy_baseline_dir is required with healthy_baseline_source")
        self.healthy_baseline_dir = (
            healthy_baseline_argument(healthy_baseline_dir) if healthy_baseline_source is not None else None
        )

    def validate(
        self,
        app_root: Path,
        *,
        actor: ArtifactOwner,
        changed_paths: list[str],
        baseline_root: Path | None = None,
    ) -> tuple[ValidatorNetworkPolicyCanary, ...]:
        resolved_root = app_root.resolve()
        normalized = [self._normalize_changed_path(path) for path in changed_paths]
        if not normalized:
            raise MemoryValidationError("proposal has no changed operational-memory files")
        for path in normalized:
            if not self._actor_owns(actor, path):
                raise MemoryValidationError(f"{actor.value} does not own {path.as_posix()}")
        self._reject_symlinks(resolved_root)

        try:
            repository = MemoryRepository(resolved_root)
            repository.schema_version()
            goal = repository.goal()
            architecture = repository.architecture()
            playbooks = repository.playbooks()
            repository.diagnostics()
            repository.traffic_workloads()
            repository.outcomes()
        except MemoryRepositoryError as exc:
            raise MemoryValidationError(str(exc)) from exc
        except ValueError as exc:
            raise MemoryValidationError(f"invalid diagnostics manifest: {exc}") from exc

        if not goal.body.strip():
            raise MemoryValidationError("goal.md body must not be empty")
        if not architecture.body.strip():
            raise MemoryValidationError("arch.md body must not be empty")
        if not playbooks:
            raise MemoryValidationError("at least one playbook is required")
        self._validate_playbooks(repository)
        self._validate_detector_classes(repository)
        self._validate_detector_ownership(
            repository,
            actor=actor,
            changed_paths=normalized,
            baseline_root=baseline_root,
        )
        self._validate_incident_provenance(
            repository,
            actor=actor,
            changed_paths=normalized,
            baseline_root=baseline_root,
        )
        self._validate_incident_persistence(
            repository,
            actor=actor,
            changed_paths=normalized,
            baseline_root=baseline_root,
        )
        self._validate_outcomes_append_only(repository, actor=actor, baseline_root=baseline_root)
        diagnostics_changed = any(path.as_posix().startswith(".sdo/diagnostics/") for path in normalized)
        if self.run_diagnostics and diagnostics_changed:
            return self._run_diagnostic_checks(resolved_root)
        return ()

    @staticmethod
    def _normalize_changed_path(raw_path: str) -> PurePosixPath:
        path = PurePosixPath(raw_path)
        if path.is_absolute() or ".." in path.parts or not path.parts or path.parts[0] != ".sdo":
            raise MemoryValidationError(f"changed path must stay inside canonical .sdo memory: {raw_path}")
        return path

    @staticmethod
    def _actor_owns(actor: ArtifactOwner, path: PurePosixPath) -> bool:
        value = path.as_posix()
        if actor == ArtifactOwner.HUMAN:
            return value == ".sdo/goal.md"
        if actor == ArtifactOwner.DEPLOYER:
            return value == ".sdo/arch.md"
        if actor == ArtifactOwner.UPKEEP:
            return value in {
                ".sdo/arch.md",
                ".sdo/schema-version",
                ".sdo/diagnostics/go.mod",
                ".sdo/diagnostics/go.sum",
            }
        if actor == ArtifactOwner.CONTROLLER:
            return value == ".sdo/outcomes.jsonl"
        incident_traffic = value.startswith(
            (
                f"{TRAFFIC_INCIDENT_GENERATORS_DIRECTORY}/",
                f"{TRAFFIC_WORKLOAD_DIRECTORY}/{TRAFFIC_INCIDENT_WORKLOAD_PREFIX}",
            )
        )
        if actor == ArtifactOwner.HEALTH_JUDGE:
            return value == ".sdo/diagnostics/manifest.yaml" or (
                value.startswith((".sdo/diagnostics/detectors/health/", f"{TRAFFIC_DIRECTORY}/"))
                and not incident_traffic
            )
        if actor == ArtifactOwner.RESPONDER:
            return (
                value.startswith((".sdo/playbooks/", ".sdo/diagnostics/detectors/incidents/"))
                or value == ".sdo/diagnostics/manifest.yaml"
                or incident_traffic
            )
        return False

    @staticmethod
    def _reject_symlinks(app_root: Path) -> None:
        memory_root = app_root / ".sdo"
        if memory_root.is_symlink():
            raise MemoryValidationError(".sdo memory root must not be a symlink")
        if not memory_root.is_dir():
            raise MemoryValidationError("canonical .sdo memory directory is required")
        for path in memory_root.rglob("*"):
            if path.is_symlink():
                raise MemoryValidationError(f"symlink is forbidden in operational memory: {path}")

    @staticmethod
    def _validate_playbooks(repository: MemoryRepository) -> None:
        index_path = repository.app_root / PLAYBOOK_INDEX_PATH
        playbook_root = index_path.parent
        if not index_path.is_file():
            raise MemoryValidationError(f"playbook index {PLAYBOOK_INDEX_PATH} is required")
        index = index_path.read_text(encoding="utf-8")
        linked: set[Path] = set()
        for raw_target in MARKDOWN_LINK_RE.findall(index):
            target_without_fragment = raw_target.split("#", maxsplit=1)[0].strip()
            if not target_without_fragment or "://" in target_without_fragment:
                continue
            relative = PurePosixPath(target_without_fragment)
            if relative.is_absolute() or ".." in relative.parts:
                raise MemoryValidationError(f"playbook index link escapes playbook root: {raw_target}")
            target = (playbook_root / relative).resolve()
            try:
                target.relative_to(playbook_root.resolve())
            except ValueError as exc:
                raise MemoryValidationError(f"playbook index link escapes playbook root: {raw_target}") from exc
            if target.is_dir():
                target = target / "README.md"
            if not target.is_file():
                raise MemoryValidationError(f"playbook index link does not exist: {raw_target}")
            linked.add(target)

        typed_playbooks = repository.playbooks()
        missing = [artifact.path for artifact in typed_playbooks if artifact.path not in linked]
        if missing:
            raise MemoryValidationError(f"playbook is missing from index: {missing[0]}")
        for artifact in typed_playbooks:
            if not artifact.body.strip():
                raise MemoryValidationError(f"playbook body must not be empty: {artifact.path}")
            if not PLACEHOLDER_RE.search(artifact.body):
                raise MemoryValidationError(f"playbook must use a role placeholder: {artifact.path}")

        for script in playbook_root.glob("*/scripts/*"):
            if not script.is_file():
                continue
            if script.suffix != PLAYBOOK_SCRIPT_SUFFIX:
                raise MemoryValidationError(
                    f"playbook scripts must use the {PLAYBOOK_SCRIPT_SUFFIX} extension: {script}"
                )
            completed = subprocess.run(
                ["bash", "-n", str(script)],
                check=False,
                capture_output=True,
                text=True,
            )
            if completed.returncode != 0:
                details = completed.stderr.strip() or "bash -n failed"
                raise MemoryValidationError(f"invalid shell syntax in {script}: {details}")

    @staticmethod
    def _validate_detector_classes(repository: MemoryRepository) -> None:
        manifest = repository.diagnostics()
        for detector in manifest.detectors:
            package = PurePosixPath(detector.package.removeprefix("./"))
            expected_directory = "health" if detector.detector_class == "health" else "incidents"
            expected_owner = (
                ArtifactOwner.HEALTH_JUDGE if detector.detector_class == "health" else ArtifactOwner.RESPONDER
            )
            if len(package.parts) < 3 or package.parts[:2] != ("detectors", expected_directory):
                raise MemoryValidationError(
                    f"{detector.detector_class} detector {detector.id!r} must be under detectors/{expected_directory}"
                )
            if detector.owner != expected_owner:
                raise MemoryValidationError(
                    f"{detector.detector_class} detector {detector.id!r} must be owned by {expected_owner.value}"
                )
            for playbook in detector.possible_playbooks:
                path = PurePosixPath(playbook)
                if path.is_absolute() or ".." in path.parts or path.parts[:2] != (".sdo", "playbooks"):
                    raise MemoryValidationError(f"detector {detector.id!r} has invalid possible playbook {playbook!r}")
                if not (repository.app_root / path).is_file():
                    raise MemoryValidationError(
                        f"detector {detector.id!r} possible playbook does not exist: {playbook}"
                    )

    @staticmethod
    def _validate_detector_ownership(
        repository: MemoryRepository,
        *,
        actor: ArtifactOwner,
        changed_paths: list[PurePosixPath],
        baseline_root: Path | None,
    ) -> None:
        manifest_changed = PurePosixPath(".sdo/diagnostics/manifest.yaml") in changed_paths
        if not manifest_changed or actor not in {ArtifactOwner.RESPONDER, ArtifactOwner.HEALTH_JUDGE}:
            return
        if baseline_root is None:
            raise MemoryValidationError("agent-authored manifest edits require a baseline repository")
        baseline = MemoryRepository(baseline_root).diagnostics()
        candidate = repository.diagnostics()
        if (
            candidate.api_version != baseline.api_version
            or candidate.kind != baseline.kind
            or candidate.sdk_version != baseline.sdk_version
        ):
            raise MemoryValidationError("agents may not alter shared diagnostics manifest metadata")

        protected_class = "health" if actor == ArtifactOwner.RESPONDER else "incident"
        actor_class = "incident" if actor == ArtifactOwner.RESPONDER else "health"
        baseline_protected = {
            detector.id: detector for detector in baseline.detectors if detector.detector_class == protected_class
        }
        candidate_protected = {
            detector.id: detector for detector in candidate.detectors if detector.detector_class == protected_class
        }
        if candidate_protected != baseline_protected:
            raise MemoryValidationError(f"{actor.value} may not alter any {protected_class} detector registration")
        baseline_ids = {detector.id for detector in baseline.detectors}
        unauthorized_additions = [
            detector.id
            for detector in candidate.detectors
            if detector.id not in baseline_ids and detector.detector_class != actor_class
        ]
        if unauthorized_additions:
            raise MemoryValidationError(
                f"{actor.value} may not add {protected_class} detector {unauthorized_additions[0]!r}"
            )

    @staticmethod
    def _validate_incident_provenance(
        repository: MemoryRepository,
        *,
        actor: ArtifactOwner,
        changed_paths: list[PurePosixPath],
        baseline_root: Path | None,
    ) -> None:
        """Reject responder edits that rewrite an existing incident detector's provenance.

        ``originatingIncident`` and ``originatingCommit`` record the incident and
        outcome that first taught a detector. Refinements keep them; rewriting
        them erases the true origin and needlessly changes the detector tree.
        """

        if actor != ArtifactOwner.RESPONDER or baseline_root is None:
            return
        if not any(path.as_posix().startswith(".sdo/diagnostics/") for path in changed_paths):
            return
        baseline_repository = MemoryRepository(baseline_root)
        try:
            baseline = baseline_repository.diagnostics()
        except MemoryRepositoryError:
            return
        candidate = {detector.id: detector for detector in repository.diagnostics().detectors}
        for detector in baseline.detectors:
            if detector.detector_class != "incident":
                continue
            current = candidate.get(detector.id)
            if current is not None and (
                current.originating_incident != detector.originating_incident
                or current.originating_commit != detector.originating_commit
            ):
                raise MemoryValidationError(
                    f"responder may not rewrite provenance (originatingIncident/originatingCommit) of existing "
                    f"incident detector {detector.id!r}"
                )
            package = detector.package.removeprefix("./")
            before = _spec_provenance(baseline_repository.memory_root / "diagnostics" / package)
            after = _spec_provenance(repository.memory_root / "diagnostics" / package)
            if before and after and before != after:
                raise MemoryValidationError(
                    f"responder may not rewrite provenance (OriginatingIncident/OriginatingCommit) in the Spec() of "
                    f"existing incident detector {detector.id!r}"
                )

    @staticmethod
    def _validate_incident_persistence(
        repository: MemoryRepository,
        *,
        actor: ArtifactOwner,
        changed_paths: list[PurePosixPath],
        baseline_root: Path | None,
    ) -> None:
        """Require a new or changed incident detector registration to fire on its first match.

        A delayed incident detector lags the health detectors that trigger
        dispatch, so its playbook is not surfaced for the incident it encodes.
        Untouched legacy registrations are left alone so an unrelated
        playbook-only proposal is never rejected for memory it did not change.
        """

        if actor != ArtifactOwner.RESPONDER or baseline_root is None:
            return
        if PurePosixPath(".sdo/diagnostics/manifest.yaml") not in changed_paths:
            return
        try:
            baseline = {detector.id: detector for detector in MemoryRepository(baseline_root).diagnostics().detectors}
        except MemoryRepositoryError:
            baseline = {}
        for detector in repository.diagnostics().detectors:
            if detector.detector_class != "incident" or detector.persistence.firing <= INCIDENT_DETECTOR_MAX_FIRING:
                continue
            if baseline.get(detector.id) == detector:
                continue
            raise MemoryValidationError(
                f"incident detector {detector.id!r}: persistence.firing must be {INCIDENT_DETECTOR_MAX_FIRING} "
                f"(got {detector.persistence.firing}) for a new or changed incident detector, in the manifest and "
                "its Spec(); a learned fault signature must fire on its first match"
            )

    @staticmethod
    def _validate_outcomes_append_only(
        repository: MemoryRepository,
        *,
        actor: ArtifactOwner,
        baseline_root: Path | None,
    ) -> None:
        if baseline_root is None or actor != ArtifactOwner.CONTROLLER:
            return
        candidate = repository.memory_root / "outcomes.jsonl"
        baseline_repository = MemoryRepository(baseline_root)
        baseline = baseline_repository.memory_root / "outcomes.jsonl"
        baseline_bytes = baseline.read_bytes()
        candidate_bytes = candidate.read_bytes()
        if not candidate_bytes.startswith(baseline_bytes) or candidate_bytes == baseline_bytes:
            raise MemoryValidationError("outcomes.jsonl must be a non-empty append-only update")

    def _run_diagnostic_checks(self, app_root: Path) -> tuple[ValidatorNetworkPolicyCanary, ...]:
        staged = self._stage_healthy_baseline(app_root)
        try:
            completed = self.sandbox_runner.run(app_root)
        finally:
            if staged is not None:
                shutil.rmtree(staged, ignore_errors=True)
                # Drop the scratch parent too when staging created it (e.g. ``.sdo-baseline``).
                for parent in staged.parents:
                    if parent == app_root.resolve():
                        break
                    try:
                        parent.rmdir()
                    except OSError:
                        break
        if completed.returncode != 0:
            details = completed.stderr.strip() or completed.stdout.strip() or "diagnostic checks failed"
            raise MemoryValidationError(details)
        return completed.network_policy_canaries

    def _stage_healthy_baseline(self, app_root: Path) -> Path | None:
        """Copy the recorded healthy snapshots into the tree the sandbox validates, or None when not gated."""

        if self.healthy_baseline_source is None or self.healthy_baseline_dir is None:
            return None
        snapshots = sorted(self.healthy_baseline_source.glob("*.json")) if self.healthy_baseline_source.is_dir() else []
        if not snapshots:
            raise MemoryValidationError(
                f"healthy baseline gate is enabled but {self.healthy_baseline_source} holds no *.json snapshots; "
                "the harness must record the healthy application before the first incident"
            )
        target = app_root.resolve() / self.healthy_baseline_dir
        target.mkdir(parents=True, exist_ok=True)
        for snapshot in snapshots:
            shutil.copyfile(snapshot, target / snapshot.name)
        return target


def _spec_provenance(package: Path) -> dict[str, str]:
    """Originating* string literals declared in a detector package's Go sources."""

    values: dict[str, str] = {}
    if not package.is_dir():
        return values
    for source in sorted(package.glob("*.go")):
        if source.name.endswith("_test.go"):
            continue
        for field, literal in SPEC_PROVENANCE_RE.findall(source.read_text(encoding="utf-8", errors="replace")):
            values.setdefault(field, literal)
    return values
