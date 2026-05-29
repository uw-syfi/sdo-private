from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest

from observer.updater.check_cli import main as check_main
from observer.updater.manifest import ManifestError, load_manifest
from observer.updater.workspace import BuildWorkspace, BuildWorkspaceConfig

if TYPE_CHECKING:
    from pathlib import Path


def _write_app_diagnostics(app_root: Path, *, package_path: str = "./detectors/missing_endpoints") -> None:
    diagnostics = app_root / ".sds" / "diagnostics"
    detector_dir = diagnostics / "detectors" / "missing_endpoints"
    playbook_dir = app_root / ".sds" / "playbooks"
    detector_dir.mkdir(parents=True)
    playbook_dir.mkdir(parents=True)

    (playbook_dir / "service-endpoints.md").write_text("# Service endpoints\n", encoding="utf-8")
    (diagnostics / "go.mod").write_text(
        """module app-diagnostics

go 1.24

require sds.dev/observer/sdk v0.0.0
""",
        encoding="utf-8",
    )
    (diagnostics / "manifest.yaml").write_text(
        f"""apiVersion: sds.dev/v1alpha1
kind: ObserverDiagnostics
sdkVersion: v0.1
detectors:
  - id: missing-endpoints
    package: {package_path}
    constructor: New
    playbooks:
      - .sds/playbooks/service-endpoints.md
""",
        encoding="utf-8",
    )
    (detector_dir / "detector.go").write_text(
        """package missing_endpoints

import (
    "context"

    "sds.dev/observer/sdk"
)

func New() sdk.Detector {
    return Detector{}
}

type Detector struct{}

func (Detector) Spec() sdk.DetectorSpec {
    return sdk.DetectorSpec{ID: "missing-endpoints"}
}

func (Detector) Detect(context.Context, sdk.DetectionContext) ([]sdk.Finding, error) {
    return nil, nil
}
""",
        encoding="utf-8",
    )


def _write_tool_root(tool_root: Path) -> None:
    (tool_root / "observer" / "sdk").mkdir(parents=True)
    (tool_root / "observer" / "core").mkdir(parents=True)


def test_manifest_rejects_detector_package_escape(tmp_path: Path) -> None:
    app_root = tmp_path / "app"
    _write_app_diagnostics(app_root, package_path="../outside")

    with pytest.raises(ManifestError, match="must stay inside"):
        load_manifest(app_root / ".sds" / "diagnostics" / "manifest.yaml", app_root=app_root)


def test_build_workspace_generates_registration_without_mutating_app_go_mod(tmp_path: Path) -> None:
    app_root = tmp_path / "app"
    tool_root = tmp_path / "sds"
    _write_app_diagnostics(app_root)
    _write_tool_root(tool_root)

    app_go_mod = app_root / ".sds" / "diagnostics" / "go.mod"
    original_go_mod = app_go_mod.read_text(encoding="utf-8")

    config = BuildWorkspaceConfig(
        app_root=app_root,
        sdk_dir=tool_root / "observer" / "sdk",
        core_dir=tool_root / "observer" / "core",
    )
    with BuildWorkspace.create(config) as workspace:
        generated = workspace.path / "generated" / "detectors.go"
        generated_source = generated.read_text(encoding="utf-8")
        workspace_go_mod = (workspace.path / "go.mod").read_text(encoding="utf-8")

        assert 'd0 "app-diagnostics/detectors/missing_endpoints"' in generated_source
        assert "return []sdk.Detector{" in generated_source
        assert "d0.New()," in generated_source
        assert f"replace sds.dev/observer/sdk => {tool_root / 'observer' / 'sdk'}" in workspace_go_mod
        assert f"replace sds.dev/observer/core => {tool_root / 'observer' / 'core'}" in workspace_go_mod

    assert app_go_mod.read_text(encoding="utf-8") == original_go_mod


def test_check_cli_test_uses_internal_tool_root_and_runs_go_in_temp_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_root = tmp_path / "app"
    tool_root = tmp_path / "sds"
    _write_app_diagnostics(app_root)
    _write_tool_root(tool_root)

    calls_log = tmp_path / "go-calls.log"
    fake_go = tmp_path / "go"
    fake_go.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
printf '%s|%s\\n' "$PWD" "$*" >> "$SDS_GO_CALLS_LOG"
""",
        encoding="utf-8",
    )
    fake_go.chmod(fake_go.stat().st_mode | 0o111)

    monkeypatch.setenv("SDS_OBSERVER_TOOL_ROOT", str(tool_root))
    monkeypatch.setenv("SDS_OBSERVER_GO", str(fake_go))
    monkeypatch.setenv("SDS_GO_CALLS_LOG", str(calls_log))

    exit_code = check_main(["test", "--app", str(app_root)])

    assert exit_code == 0
    calls = calls_log.read_text(encoding="utf-8").splitlines()
    assert [line.split("|", maxsplit=1)[1] for line in calls] == [
        "mod tidy",
        "test ./...",
        "build -buildvcs=false ./cmd/observer",
    ]
    assert all(not line.startswith(str(app_root)) for line in calls)
    assert os.environ["SDS_OBSERVER_TOOL_ROOT"] == str(tool_root)


def test_check_cli_run_once_runs_generated_observer_against_namespace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_root = tmp_path / "app"
    tool_root = tmp_path / "sds"
    _write_app_diagnostics(app_root)
    _write_tool_root(tool_root)

    calls_log = tmp_path / "go-calls.log"
    fake_go = tmp_path / "go"
    fake_go.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
printf '%s|%s\\n' "$PWD" "$*" >> "$SDS_GO_CALLS_LOG"
""",
        encoding="utf-8",
    )
    fake_go.chmod(fake_go.stat().st_mode | 0o111)

    monkeypatch.setenv("SDS_OBSERVER_TOOL_ROOT", str(tool_root))
    monkeypatch.setenv("SDS_OBSERVER_GO", str(fake_go))
    monkeypatch.setenv("SDS_GO_CALLS_LOG", str(calls_log))

    exit_code = check_main(["run-once", "--app", str(app_root), "--namespace", "demo"])

    assert exit_code == 0
    calls = calls_log.read_text(encoding="utf-8").splitlines()
    assert [line.split("|", maxsplit=1)[1] for line in calls] == [
        "mod tidy",
        "run -buildvcs=false ./cmd/observer --namespace demo",
    ]
    assert all(not line.startswith(str(app_root)) for line in calls)
