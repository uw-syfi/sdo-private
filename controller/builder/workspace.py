from __future__ import annotations

import json
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from controller.builder.manifest import DetectorManifest, duration_nanoseconds, load_manifest
from controller.builder.paths import find_diagnostics_dir

MODULE_RE = re.compile(r"^\s*module\s+(\S+)\s*$", re.MULTILINE)
DEFAULT_MODULE_PATH = "app-diagnostics"


@dataclass(frozen=True)
class BuildWorkspaceConfig:
    app_root: Path
    sdk_dir: Path
    core_dir: Path
    runtime_dir: Path | None = None
    keep: bool = False
    detector_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class BuildWorkspace:
    path: Path
    app_root: Path
    manifest: DetectorManifest
    module_path: str
    _temp_dir: tempfile.TemporaryDirectory[str] | None = None

    @classmethod
    def create(cls, config: BuildWorkspaceConfig) -> BuildWorkspace:
        app_root = config.app_root.resolve()
        diagnostics_dir = find_diagnostics_dir(app_root)
        manifest = load_manifest(diagnostics_dir / "manifest.yaml", app_root=app_root)
        if config.detector_ids:
            requested = set(config.detector_ids)
            selected = [detector for detector in manifest.detectors if detector.id in requested]
            missing = sorted(requested - {detector.id for detector in selected})
            if missing:
                raise ValueError(f"requested detector id(s) not found: {', '.join(missing)}")
            excluded_packages = {
                _clean_package_path(detector.package) for detector in manifest.detectors if detector.id not in requested
            } - {_clean_package_path(detector.package) for detector in selected}
            manifest = manifest.model_copy(update={"detectors": selected})
        else:
            excluded_packages = set()
        _reject_symlinks(diagnostics_dir)

        temp_dir: tempfile.TemporaryDirectory[str] | None = None
        if config.keep:
            workspace_path = Path(tempfile.mkdtemp(prefix="sdo-controller-build-")).resolve()
        else:
            temp_dir = tempfile.TemporaryDirectory(prefix="sdo-controller-build-")
            workspace_path = Path(temp_dir.name).resolve()

        shutil.copytree(diagnostics_dir, workspace_path, dirs_exist_ok=True)
        for package in excluded_packages:
            shutil.rmtree(workspace_path / package)
        module_path = _module_path(workspace_path / "go.mod")
        _write_go_mod(
            workspace_path / "go.mod",
            module_path=module_path,
            sdk_dir=config.sdk_dir.resolve(),
            core_dir=config.core_dir.resolve(),
            runtime_dir=(config.runtime_dir or config.core_dir.parent / "runtime").resolve(),
        )
        _write_generated_registration(workspace_path, module_path=module_path, manifest=manifest)
        _write_generated_main(workspace_path, module_path=module_path)

        return cls(
            path=workspace_path,
            app_root=app_root,
            manifest=manifest,
            module_path=module_path,
            _temp_dir=temp_dir,
        )

    def __enter__(self) -> BuildWorkspace:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if self._temp_dir is not None:
            self._temp_dir.cleanup()


def _reject_symlinks(root: Path) -> None:
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"diagnostics path must not contain symlinks: {path.relative_to(root)}")


def _module_path(go_mod: Path) -> str:
    if not go_mod.is_file():
        return DEFAULT_MODULE_PATH
    match = MODULE_RE.search(go_mod.read_text(encoding="utf-8"))
    if not match:
        return DEFAULT_MODULE_PATH
    module_path = match.group(1)
    if module_path.startswith((".", "/")):
        return DEFAULT_MODULE_PATH
    return module_path


def _write_go_mod(go_mod: Path, *, module_path: str, sdk_dir: Path, core_dir: Path, runtime_dir: Path) -> None:
    if go_mod.is_file():
        source = go_mod.read_text(encoding="utf-8").rstrip()
    else:
        source = f"module {module_path}\n\ngo 1.24"

    lines = [
        line
        for line in source.splitlines()
        if not line.strip().startswith("replace sdo.dev/controller/sdk =>")
        and not line.strip().startswith("replace sdo.dev/controller/core =>")
        and not line.strip().startswith("replace sdo.dev/controller/runtime =>")
    ]
    module_line_found = False
    for index, line in enumerate(lines):
        if MODULE_RE.match(line):
            lines[index] = f"module {module_path}"
            module_line_found = True
            break
    if not module_line_found:
        lines.insert(0, f"module {module_path}")
    content = "\n".join(lines).rstrip()
    if "sdo.dev/controller/sdk" not in content:
        content += "\n\nrequire sdo.dev/controller/sdk v0.0.0"
    if "sdo.dev/controller/core" not in content:
        content += "\nrequire sdo.dev/controller/core v0.0.0"
    if "sdo.dev/controller/runtime" not in content:
        content += "\nrequire sdo.dev/controller/runtime v0.0.0"
    content += (
        f"\n\nreplace sdo.dev/controller/sdk => {sdk_dir}"
        f"\nreplace sdo.dev/controller/core => {core_dir}"
        f"\nreplace sdo.dev/controller/runtime => {runtime_dir}\n"
    )
    go_mod.write_text(content, encoding="utf-8")


def _write_generated_registration(
    workspace_path: Path,
    *,
    module_path: str,
    manifest: DetectorManifest,
) -> None:
    generated_dir = workspace_path / "generated"
    generated_dir.mkdir(parents=True, exist_ok=True)

    imports = ['\t"sdo.dev/controller/sdk"']
    constructors = []
    for index, detector in enumerate(manifest.detectors):
        alias = f"d{index}"
        detector_import = f"{module_path}/{_clean_package_path(detector.package)}"
        imports.append(f'\t{alias} "{detector_import}"')
        constructors.append(f"\t\t{alias}.{detector.constructor}(),")

    source = (
        "package generated\n\n"
        "import (\n" + "\n".join(imports) + "\n)\n\n"
        "func All() []sdk.Detector {\n"
        "\treturn []sdk.Detector{\n" + "\n".join(constructors) + "\n\t}\n"
        "}\n"
    )
    (generated_dir / "detectors.go").write_text(source, encoding="utf-8")
    _write_generated_contract_test(generated_dir, manifest)


def _write_generated_contract_test(generated_dir: Path, manifest: DetectorManifest) -> None:
    registrations = []
    for index, detector in enumerate(manifest.detectors):
        watches = ", ".join(
            "{APIVersion: "
            + json.dumps(watch.api_version)
            + ", Kind: "
            + json.dumps(watch.kind)
            + ", Namespace: "
            + json.dumps(watch.namespace)
            + "}"
            for watch in detector.watches
        )
        playbooks = ", ".join(json.dumps(path) for path in detector.possible_playbooks)
        registrations.append(
            "\tassertRegistration(t, detectors["
            + str(index)
            + "].Spec(), registration{\n"
            + f"\t\tid: {json.dumps(detector.id)}, class: {json.dumps(detector.detector_class)}, "
            + f"owner: {json.dumps(detector.owner)},\n"
            + f"\t\twatches: []sdk.WatchKind{{{watches}}}, "
            + f"interval: time.Duration({duration_nanoseconds(detector.interval)}),\n"
            + f"\t\tpersistence: sdk.PersistencePolicy{{Firing: {detector.persistence.firing}, "
            + f"Clearing: {detector.persistence.clearing}}},\n"
            + "\t\tbatching: sdk.BatchingPolicy{Severity: sdk.FindingSeverity("
            + json.dumps(detector.batching.severity)
            + "), "
            + f"Debounce: time.Duration({duration_nanoseconds(detector.batching.debounce, allow_zero=True)})}},\n"
            + f"\t\tplaybooks: []string{{{playbooks}}}, "
            + f"originatingIncident: {json.dumps(detector.originating_incident or '')}, "
            + f"originatingCommit: {json.dumps(detector.originating_commit)},\n"
            + "\t})"
        )
    source = (
        """package generated

import (
    "reflect"
    "testing"
    "time"

    "sdo.dev/controller/sdk"
)

type registration struct {
    id string
    class string
    owner string
    watches []sdk.WatchKind
    interval time.Duration
    persistence sdk.PersistencePolicy
    batching sdk.BatchingPolicy
    playbooks []string
    originatingIncident string
    originatingCommit string
}

func assertRegistration(t *testing.T, got sdk.DetectorSpec, want registration) {
    t.Helper()
    if got.ID != want.id || string(got.Class) != want.class || string(got.Owner) != want.owner ||
        got.Interval != want.interval || got.Persistence != want.persistence || got.Batching != want.batching ||
        got.OriginatingIncident != want.originatingIncident || got.OriginatingCommit != want.originatingCommit ||
        !reflect.DeepEqual(got.Watches, want.watches) || !reflect.DeepEqual(got.Playbooks, want.playbooks) {
        t.Fatalf("detector registration mismatch:\\n got: %#v\\nwant: %#v", got, want)
    }
}

func TestRegistrationContracts(t *testing.T) {
    detectors := All()
"""
        + "\n".join(registrations)
        + "\n}\n"
    )
    (generated_dir / "registrations_test.go").write_text(source, encoding="utf-8")


def _write_generated_main(workspace_path: Path, *, module_path: str) -> None:
    main_dir = workspace_path / "cmd" / "controller"
    main_dir.mkdir(parents=True, exist_ok=True)
    source = f"""package main

import (
\t"sdo.dev/controller/runtime"
\t"{module_path}/generated"
)

func main() {{
\truntime.Run(generated.All())
}}
"""
    (main_dir / "main.go").write_text(source, encoding="utf-8")


def _clean_package_path(package: str) -> str:
    parts = Path(package).parts
    if parts and parts[0] == ".":
        parts = parts[1:]
    return "/".join(parts)
