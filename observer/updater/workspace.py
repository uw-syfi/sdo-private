from __future__ import annotations

import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from observer.updater.manifest import ObserverDiagnosticsManifest, load_manifest

MODULE_RE = re.compile(r"^\s*module\s+(\S+)\s*$", re.MULTILINE)


@dataclass(frozen=True)
class BuildWorkspaceConfig:
    app_root: Path
    sdk_dir: Path
    core_dir: Path
    keep: bool = False


@dataclass(frozen=True)
class BuildWorkspace:
    path: Path
    app_root: Path
    manifest: ObserverDiagnosticsManifest
    module_path: str
    _temp_dir: tempfile.TemporaryDirectory[str] | None = None

    @classmethod
    def create(cls, config: BuildWorkspaceConfig) -> BuildWorkspace:
        app_root = config.app_root.resolve()
        diagnostics_dir = app_root / ".sds" / "diagnostics"
        manifest = load_manifest(diagnostics_dir / "manifest.yaml", app_root=app_root)
        _reject_symlinks(diagnostics_dir)

        temp_dir: tempfile.TemporaryDirectory[str] | None = None
        if config.keep:
            workspace_path = Path(tempfile.mkdtemp(prefix="sds-observer-build-")).resolve()
        else:
            temp_dir = tempfile.TemporaryDirectory(prefix="sds-observer-build-")
            workspace_path = Path(temp_dir.name).resolve()

        shutil.copytree(diagnostics_dir, workspace_path, dirs_exist_ok=True)
        module_path = _module_path(workspace_path / "go.mod")
        _write_go_mod(
            workspace_path / "go.mod",
            module_path=module_path,
            sdk_dir=config.sdk_dir.resolve(),
            core_dir=config.core_dir.resolve(),
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
        return "app-diagnostics"
    match = MODULE_RE.search(go_mod.read_text(encoding="utf-8"))
    if not match:
        return "app-diagnostics"
    return match.group(1)


def _write_go_mod(go_mod: Path, *, module_path: str, sdk_dir: Path, core_dir: Path) -> None:
    if go_mod.is_file():
        source = go_mod.read_text(encoding="utf-8").rstrip()
    else:
        source = f"module {module_path}\n\ngo 1.24"

    lines = [
        line
        for line in source.splitlines()
        if not line.strip().startswith("replace sds.dev/observer/sdk =>")
        and not line.strip().startswith("replace sds.dev/observer/core =>")
    ]
    content = "\n".join(lines).rstrip()
    if "sds.dev/observer/sdk" not in content:
        content += "\n\nrequire sds.dev/observer/sdk v0.0.0"
    if "sds.dev/observer/core" not in content:
        content += "\nrequire sds.dev/observer/core v0.0.0"
    content += f"\n\nreplace sds.dev/observer/sdk => {sdk_dir}\nreplace sds.dev/observer/core => {core_dir}\n"
    go_mod.write_text(content, encoding="utf-8")


def _write_generated_registration(
    workspace_path: Path,
    *,
    module_path: str,
    manifest: ObserverDiagnosticsManifest,
) -> None:
    generated_dir = workspace_path / "generated"
    generated_dir.mkdir(parents=True, exist_ok=True)

    imports = ['\t"sds.dev/observer/sdk"']
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


def _write_generated_main(workspace_path: Path, *, module_path: str) -> None:
    main_dir = workspace_path / "cmd" / "observer"
    main_dir.mkdir(parents=True, exist_ok=True)
    source = f"""package main

import (
\t"sds.dev/observer/core"
\t"{module_path}/generated"
)

func main() {{
\tcore.Run(generated.All())
}}
"""
    (main_dir / "main.go").write_text(source, encoding="utf-8")


def _clean_package_path(package: str) -> str:
    parts = Path(package).parts
    if parts and parts[0] == ".":
        parts = parts[1:]
    return "/".join(parts)
