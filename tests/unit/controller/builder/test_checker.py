from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from controller.builder.check_cli import _diagnostics_fingerprint
from controller.builder.check_cli import main as check_main
from controller.builder.go_runner import GoRunner, seed_go_cache_from_environment
from controller.builder.manifest import ManifestError, load_manifest
from controller.builder.paths import find_app_root
from controller.builder.workspace import BuildWorkspace, BuildWorkspaceConfig


def _write_app_diagnostics(
    app_root: Path,
    *,
    package_path: str = "./detectors/missing_endpoints",
    memory_dir: str = ".sdo",
) -> None:
    diagnostics = app_root / memory_dir / "diagnostics"
    detector_dir = diagnostics / "detectors" / "missing_endpoints"
    playbook_dir = app_root / memory_dir / "playbooks"
    detector_dir.mkdir(parents=True)
    playbook_dir.mkdir(parents=True)

    (playbook_dir / "service-endpoints.md").write_text("# Service endpoints\n", encoding="utf-8")
    (diagnostics / "go.mod").write_text(
        """module app-diagnostics

go 1.24

require sdo.dev/controller/sdk v0.0.0
""",
        encoding="utf-8",
    )
    (diagnostics / "manifest.yaml").write_text(
        f"""apiVersion: sdo.dev/v1alpha1
kind: DetectorManifest
sdkVersion: v0.1
detectors:
  - id: missing-endpoints
    package: {package_path}
    constructor: New
    class: incident
    owner: responder
    watches: []
    interval: 1m
    persistence:
      firing: 2
      clearing: 2
    batching:
      severity: critical
      debounce: 500ms
    possiblePlaybooks: []
    originatingIncident: incident-seed
    originatingCommit: abc123
""",
        encoding="utf-8",
    )
    (detector_dir / "detector.go").write_text(
        """package missing_endpoints

import (
    "context"
    "time"

    "sdo.dev/controller/sdk"
)

func New() sdk.Detector {
    return Detector{}
}

type Detector struct{}

func (Detector) Spec() sdk.DetectorSpec {
    return sdk.DetectorSpec{
        ID: "missing-endpoints",
        Class: sdk.DetectorClassIncident,
        Owner: sdk.DetectorOwnerResponder,
        Interval: time.Minute,
        Persistence: sdk.PersistencePolicy{Firing: 2, Clearing: 2},
        Batching: sdk.BatchingPolicy{Severity: sdk.SeverityCritical, Debounce: 500 * time.Millisecond},
        OriginatingIncident: "incident-seed",
        OriginatingCommit: "abc123",
        Watches: []sdk.WatchKind{},
        Playbooks: []string{},
    }
}

func (Detector) Detect(context.Context, sdk.DetectionContext) ([]sdk.Finding, error) {
    return nil, nil
}
""",
        encoding="utf-8",
    )


def _write_tool_root(tool_root: Path) -> None:
    (tool_root / "controller" / "sdk").mkdir(parents=True)
    (tool_root / "controller" / "core").mkdir(parents=True)
    (tool_root / "controller" / "runtime").mkdir(parents=True)


def test_cli_uses_detector_check_product_name(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit, match="0"):
        check_main(["--help"])

    assert capsys.readouterr().out.startswith("usage: sdo-detector-check")


def test_manifest_rejects_detector_package_escape(tmp_path: Path) -> None:
    app_root = tmp_path / "app"
    _write_app_diagnostics(app_root, package_path="../outside")

    with pytest.raises(ManifestError, match="must stay inside"):
        load_manifest(app_root / ".sdo" / "diagnostics" / "manifest.yaml", app_root=app_root)


def test_manifest_rejects_playbook_routing(tmp_path: Path) -> None:
    app_root = tmp_path / "app"
    _write_app_diagnostics(app_root)
    manifest_path = app_root / ".sdo" / "diagnostics" / "manifest.yaml"
    manifest_path.write_text(
        manifest_path.read_text(encoding="utf-8").replace(
            "    constructor: New\n",
            "    constructor: New\n    playbooks:\n      - .sdo/playbooks/service-endpoints.md\n",
        ),
        encoding="utf-8",
    )

    with pytest.raises(ManifestError, match="Extra inputs are not permitted"):
        load_manifest(manifest_path, app_root=app_root)


def test_build_workspace_generates_registration_without_mutating_app_go_mod(tmp_path: Path) -> None:
    app_root = tmp_path / "app"
    tool_root = tmp_path / "sdo"
    _write_app_diagnostics(app_root)
    _write_tool_root(tool_root)

    app_go_mod = app_root / ".sdo" / "diagnostics" / "go.mod"
    original_go_mod = app_go_mod.read_text(encoding="utf-8")

    config = BuildWorkspaceConfig(
        app_root=app_root,
        sdk_dir=tool_root / "controller" / "sdk",
        core_dir=tool_root / "controller" / "core",
    )
    with BuildWorkspace.create(config) as workspace:
        generated = workspace.path / "generated" / "detectors.go"
        generated_source = generated.read_text(encoding="utf-8")
        workspace_go_mod = (workspace.path / "go.mod").read_text(encoding="utf-8")

        assert 'd0 "app-diagnostics/detectors/missing_endpoints"' in generated_source
        assert "return []sdk.Detector{" in generated_source
        assert "d0.New()," in generated_source
        generated_contract = (workspace.path / "generated" / "registrations_test.go").read_text(encoding="utf-8")
        assert "assertRegistration(t, detectors[0].Spec(), registration{" in generated_contract
        assert 'class: "incident"' in generated_contract
        assert f"replace sdo.dev/controller/sdk => {tool_root / 'controller' / 'sdk'}" in workspace_go_mod
        assert f"replace sdo.dev/controller/core => {tool_root / 'controller' / 'core'}" in workspace_go_mod
        assert f"replace sdo.dev/controller/runtime => {tool_root / 'controller' / 'runtime'}" in workspace_go_mod
        generated_main = (workspace.path / "cmd" / "controller" / "main.go").read_text(encoding="utf-8")
        assert '"sdo.dev/controller/runtime"' in generated_main
        assert "runtime.Run(generated.All())" in generated_main

    assert app_go_mod.read_text(encoding="utf-8") == original_go_mod


def test_build_workspace_replaces_the_contracts_module_when_present(tmp_path: Path) -> None:
    # Go does not honor a dependency's own replace directives, so the runtime
    # module's `replace sdo.dev/controller/contracts => ../contracts` is ignored
    # when runtime is a workspace dependency. The synthesized workspace (the main
    # module) must replace contracts itself or `go mod tidy` tries to fetch
    # sdo.dev/controller/contracts and the whole validation fails.
    app_root = tmp_path / "app"
    tool_root = tmp_path / "sdo"
    _write_app_diagnostics(app_root)
    _write_tool_root(tool_root)
    contracts_dir = tool_root / "controller" / "contracts"
    contracts_dir.mkdir(parents=True)

    config = BuildWorkspaceConfig(
        app_root=app_root,
        sdk_dir=tool_root / "controller" / "sdk",
        core_dir=tool_root / "controller" / "core",
    )
    with BuildWorkspace.create(config) as workspace:
        workspace_go_mod = (workspace.path / "go.mod").read_text(encoding="utf-8")

    assert "require sdo.dev/controller/contracts v0.0.0" in workspace_go_mod
    assert f"replace sdo.dev/controller/contracts => {contracts_dir}" in workspace_go_mod


def test_build_workspace_omits_contracts_replace_when_absent(tmp_path: Path) -> None:
    app_root = tmp_path / "app"
    tool_root = tmp_path / "sdo"
    _write_app_diagnostics(app_root)
    _write_tool_root(tool_root)

    config = BuildWorkspaceConfig(
        app_root=app_root,
        sdk_dir=tool_root / "controller" / "sdk",
        core_dir=tool_root / "controller" / "core",
    )
    with BuildWorkspace.create(config) as workspace:
        workspace_go_mod = (workspace.path / "go.mod").read_text(encoding="utf-8")

    assert "sdo.dev/controller/contracts" not in workspace_go_mod


def test_build_workspace_can_limit_an_authoring_check_to_one_detector(tmp_path: Path) -> None:
    app_root = tmp_path / "app"
    tool_root = tmp_path / "sdo"
    _write_app_diagnostics(app_root)
    _write_tool_root(tool_root)
    diagnostics = app_root / ".sdo/diagnostics"
    second = diagnostics / "detectors/second"
    second.mkdir()
    (second / "detector.go").write_text("package second\n", encoding="utf-8")
    manifest = diagnostics / "manifest.yaml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8")
        + """  - id: second
    package: ./detectors/second
    constructor: New
    class: incident
    owner: responder
    watches: []
    interval: 1m
    persistence: {firing: 1, clearing: 1}
    batching: {severity: critical, debounce: "0"}
    possiblePlaybooks: []
    originatingIncident: second
    originatingCommit: abc123
""",
        encoding="utf-8",
    )

    config = BuildWorkspaceConfig(
        app_root=app_root,
        sdk_dir=tool_root / "controller/sdk",
        core_dir=tool_root / "controller/core",
        detector_ids=("missing-endpoints",),
    )
    with BuildWorkspace.create(config) as workspace:
        assert [detector.id for detector in workspace.manifest.detectors] == ["missing-endpoints"]
        assert not (workspace.path / "detectors/second").exists()
        assert "detectors/second" not in (workspace.path / "generated/detectors.go").read_text(encoding="utf-8")


def test_build_workspace_prefers_canonical_sdo_diagnostics(tmp_path: Path) -> None:
    app_root = tmp_path / "app"
    tool_root = tmp_path / "sdo"
    _write_app_diagnostics(app_root, memory_dir=".sdo")
    _write_tool_root(tool_root)

    config = BuildWorkspaceConfig(
        app_root=app_root,
        sdk_dir=tool_root / "controller" / "sdk",
        core_dir=tool_root / "controller" / "core",
    )
    with BuildWorkspace.create(config) as workspace:
        assert [detector.id for detector in workspace.manifest.detectors] == ["missing-endpoints"]

    assert find_app_root(app_root / ".sdo" / "diagnostics" / "detectors") == app_root


def test_controller_update_rollout_triggers_only_for_accepted_diagnostics_changes(tmp_path: Path) -> None:
    app_root = tmp_path / "app"
    _write_app_diagnostics(app_root, memory_dir=".sdo")
    playbook = app_root / ".sdo" / "playbooks" / "service-endpoints.md"
    detector = app_root / ".sdo" / "diagnostics" / "detectors" / "missing_endpoints" / "detector.go"
    initial = _diagnostics_fingerprint(app_root)

    playbook.write_text(playbook.read_text(encoding="utf-8") + "\nplaybook refinement\n", encoding="utf-8")
    assert _diagnostics_fingerprint(app_root) == initial

    detector.write_text(detector.read_text(encoding="utf-8") + "\n// false-negative regression\n", encoding="utf-8")
    assert _diagnostics_fingerprint(app_root) != initial
    source = (Path(__file__).resolve().parents[4] / "controller" / "builder" / "check_cli.py").read_text(
        encoding="utf-8"
    )
    assert "controller_update_rollout" in source
    assert 'rollout_args.duration = "30s"' in source


def test_missing_configmap_sdo_fixture_compiles_in_clean_workspace() -> None:
    repository_root = Path(__file__).resolve().parents[4]
    fixture_root = repository_root / "tests" / "fixtures" / "sdo" / "missing_configmap_app"
    fixture_go_sum = fixture_root / ".sdo" / "diagnostics" / "go.sum"

    assert not fixture_go_sum.exists()
    assert check_main(["test", "--app", str(fixture_root)]) == 0
    assert not fixture_go_sum.exists()


def test_build_workspace_uses_default_module_for_invalid_local_module_path(tmp_path: Path) -> None:
    app_root = tmp_path / "app"
    tool_root = tmp_path / "sdo"
    _write_app_diagnostics(app_root)
    _write_tool_root(tool_root)
    app_go_mod = app_root / ".sdo" / "diagnostics" / "go.mod"
    app_go_mod.write_text("module .sdo/diagnostics\n\ngo 1.24\n", encoding="utf-8")

    config = BuildWorkspaceConfig(
        app_root=app_root,
        sdk_dir=tool_root / "controller" / "sdk",
        core_dir=tool_root / "controller" / "core",
    )
    with BuildWorkspace.create(config) as workspace:
        generated_source = (workspace.path / "generated" / "detectors.go").read_text(encoding="utf-8")
        workspace_go_mod = (workspace.path / "go.mod").read_text(encoding="utf-8")

    assert 'd0 "app-diagnostics/detectors/missing_endpoints"' in generated_source
    assert workspace_go_mod.startswith("module app-diagnostics\n")


def test_build_workspace_inserts_default_module_when_go_mod_has_no_module_line(tmp_path: Path) -> None:
    app_root = tmp_path / "app"
    tool_root = tmp_path / "sdo"
    _write_app_diagnostics(app_root)
    _write_tool_root(tool_root)
    app_go_mod = app_root / ".sdo" / "diagnostics" / "go.mod"
    app_go_mod.write_text("go 1.24\n", encoding="utf-8")

    config = BuildWorkspaceConfig(
        app_root=app_root,
        sdk_dir=tool_root / "controller" / "sdk",
        core_dir=tool_root / "controller" / "core",
    )
    with BuildWorkspace.create(config) as workspace:
        workspace_go_mod = (workspace.path / "go.mod").read_text(encoding="utf-8")

    assert workspace_go_mod.startswith("module app-diagnostics\ngo 1.24\n")


def test_check_cli_test_uses_internal_tool_root_and_runs_go_in_temp_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_root = tmp_path / "app"
    tool_root = tmp_path / "sdo"
    _write_app_diagnostics(app_root)
    _write_tool_root(tool_root)

    calls_log = tmp_path / "go-calls.log"
    fake_go = tmp_path / "go"
    fake_go.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
printf '%s|%s\\n' "$PWD" "$*" >> "$SDO_GO_CALLS_LOG"
""",
        encoding="utf-8",
    )
    fake_go.chmod(fake_go.stat().st_mode | 0o111)

    monkeypatch.setenv("SDO_CONTROLLER_TOOL_ROOT", str(tool_root))
    monkeypatch.setenv("SDO_CONTROLLER_GO", str(fake_go))
    monkeypatch.setenv("SDO_GO_CALLS_LOG", str(calls_log))

    exit_code = check_main(["test", "--app", str(app_root)])

    assert exit_code == 0
    calls = calls_log.read_text(encoding="utf-8").splitlines()
    assert [line.split("|", maxsplit=1)[1] for line in calls] == [
        "mod tidy",
        "test ./...",
        "build -buildvcs=false ./cmd/controller",
    ]
    assert all(not line.startswith(str(app_root)) for line in calls)
    assert os.environ["SDO_CONTROLLER_TOOL_ROOT"] == str(tool_root)


def test_go_runner_uses_common_local_go_install_when_go_not_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SDO_CONTROLLER_GO", raising=False)
    monkeypatch.setattr("controller.builder.go_runner.shutil.which", lambda _name: None)

    original_is_file = Path.is_file

    def _fake_is_file(path: Path) -> bool:
        if str(path) == "/usr/local/go/bin/go":
            return True
        return original_is_file(path)

    monkeypatch.setattr(Path, "is_file", _fake_is_file)

    assert GoRunner.from_environment().executable == "/usr/local/go/bin/go"


def test_check_cli_run_once_runs_generated_controller_against_namespace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_root = tmp_path / "app"
    tool_root = tmp_path / "sdo"
    _write_app_diagnostics(app_root)
    _write_tool_root(tool_root)

    calls_log = tmp_path / "go-calls.log"
    fake_go = tmp_path / "go"
    fake_go.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
printf '%s|%s\\n' "$PWD" "$*" >> "$SDO_GO_CALLS_LOG"
""",
        encoding="utf-8",
    )
    fake_go.chmod(fake_go.stat().st_mode | 0o111)

    monkeypatch.setenv("SDO_CONTROLLER_TOOL_ROOT", str(tool_root))
    monkeypatch.setenv("SDO_CONTROLLER_GO", str(fake_go))
    monkeypatch.setenv("SDO_GO_CALLS_LOG", str(calls_log))

    exit_code = check_main(["run-once", "--app", str(app_root), "--namespace", "demo"])

    assert exit_code == 0
    calls = calls_log.read_text(encoding="utf-8").splitlines()
    assert [line.split("|", maxsplit=1)[1] for line in calls] == [
        "mod tidy",
        f"run -buildvcs=false ./cmd/controller --run-once --namespace demo --app-root {app_root}",
    ]
    assert all(not line.startswith(str(app_root)) for line in calls)


def test_check_cli_watch_builds_once_and_samples_window(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import json

    app_root = tmp_path / "app"
    tool_root = tmp_path / "sdo"
    _write_app_diagnostics(app_root)
    _write_tool_root(tool_root)

    calls_log = tmp_path / "go-calls.log"
    run_log = tmp_path / "controller-runs.log"
    # Fake `go`: on the `build -o <bin>` step, materialize a stub detector
    # controller. The controller emits the full evaluation window from one
    # long-running process.
    fake_go = tmp_path / "go"
    fake_go.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
printf '%s|%s\\n' "$PWD" "$*" >> "$SDO_GO_CALLS_LOG"
prev=""
for a in "$@"; do
  if [ "$prev" = "-o" ]; then
    cat > "$a" <<'BIN'
#!/usr/bin/env bash
echo run >> "$SDO_RUN_LOG"
echo '{"controller_iteration":0,"returncode":0,"findings":[{"detector_id":"a","rule_id":"a","status":"active"}]}'
echo '{"controller_iteration":1,"returncode":0,"findings":[{"detector_id":"a","rule_id":"a","status":"active"}]}'
echo '{"controller_iteration":2,"returncode":0,"findings":['\
'{"detector_id":"a","rule_id":"a","status":"active"},'\
'{"detector_id":"b","rule_id":"b","status":"active"}]}'
echo '{"controller_iteration":3,"returncode":0,"findings":['\
'{"detector_id":"a","rule_id":"a","status":"active"},'\
'{"detector_id":"b","rule_id":"b","status":"active"}]}'
BIN
    chmod +x "$a"
  fi
  prev="$a"
done
""",
        encoding="utf-8",
    )
    fake_go.chmod(fake_go.stat().st_mode | 0o111)

    monkeypatch.setenv("SDO_CONTROLLER_TOOL_ROOT", str(tool_root))
    monkeypatch.setenv("SDO_CONTROLLER_GO", str(fake_go))
    monkeypatch.setenv("SDO_GO_CALLS_LOG", str(calls_log))
    monkeypatch.setenv("SDO_RUN_LOG", str(run_log))

    exit_code = check_main(
        [
            "watch",
            "--app",
            str(app_root),
            "--namespace",
            "demo",
            "--iterations",
            "4",
            "--interval-s",
            "0",
        ]
    )

    assert exit_code == 0
    # The detector controller is built and executed exactly once for the full
    # bounded observation window.
    go_invocations = [line.split("|", maxsplit=1)[1] for line in calls_log.read_text(encoding="utf-8").splitlines()]
    assert len(go_invocations) == 2, go_invocations
    assert go_invocations[0] == "mod tidy"
    assert go_invocations[1].startswith("build -buildvcs=false -o ")
    assert go_invocations[1].endswith("./cmd/controller")
    assert run_log.read_text(encoding="utf-8").splitlines() == ["run"]

    iterations = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith("{")]
    assert [it["controller_iteration"] for it in iterations] == [0, 1, 2, 3]
    fired = [sorted(f["detector_id"] for f in it["findings"]) for it in iterations]
    assert fired == [["a"], ["a"], ["a", "b"], ["a", "b"]]


def test_check_cli_controller_builds_once_and_runs_production_job_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_root = tmp_path / "workspace" / "application"
    tool_root = tmp_path / "sdo"
    _write_app_diagnostics(app_root)
    _write_tool_root(tool_root)

    calls_log = tmp_path / "go-calls.log"
    controller_log = tmp_path / "controller.log"
    fake_go = tmp_path / "go"
    fake_go.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
printf '%s|%s\\n' "$PWD" "$*" >> "$SDO_GO_CALLS_LOG"
prev=""
for a in "$@"; do
  if [ "$prev" = "-o" ]; then
    printf '#!/usr/bin/env bash\\nprintf "%%s\\n" "$*" > "$SDO_CONTROLLER_LOG"\\n' > "$a"
    printf 'if [ "$1" = "--config" ]; then cat "$2" >> "$SDO_CONTROLLER_LOG"; fi\\n' >> "$a"
    chmod +x "$a"
  fi
  prev="$a"
done
""",
        encoding="utf-8",
    )
    fake_go.chmod(fake_go.stat().st_mode | 0o111)
    monkeypatch.setenv("SDO_CONTROLLER_TOOL_ROOT", str(tool_root))
    monkeypatch.setenv("SDO_CONTROLLER_GO", str(fake_go))
    monkeypatch.setenv("SDO_GO_CALLS_LOG", str(calls_log))
    monkeypatch.setenv("SDO_CONTROLLER_LOG", str(controller_log))

    exit_code = check_main(
        [
            "controller",
            "--app",
            str(app_root),
            "--namespace",
            "demo",
            "--responder-image",
            "sdo-responder:v1",
            "--repository-pvc",
            "sdo-repository",
            "--credentials-secret",
            "sdo-codex-credentials",
            "--worktree-root",
            str(tmp_path / "workspace" / "worktrees"),
            "--verification-timeout",
            "90s",
            "--max-follow-ups",
            "3",
            "--follow-up-cooldown",
            "45s",
            "--repair-policy",
            "recorded-actions",
        ]
    )

    assert exit_code == 0
    calls = [line.split("|", maxsplit=1)[1] for line in calls_log.read_text(encoding="utf-8").splitlines()]
    assert calls[0] == "mod tidy"
    assert calls[1].startswith("build -buildvcs=false -o ")
    # Seam 3: the controller is launched with a single protojson ControllerConfig
    # via --config, not a flattened flag list. The fake controller echoes its argv
    # on the first line and the config file contents after it.
    launch = controller_log.read_text(encoding="utf-8")
    argv_line, _, config_text = launch.partition("\n")
    argv = argv_line.split()
    assert argv[0] == "--config"
    assert len(argv) == 2  # argv is exactly: --config <path>
    config = json.loads(config_text)
    assert config["dispatcher_mode"] == "job"
    assert config["dispatcher"] == os.sys.executable
    assert config["dispatcher_args"] == ["-m", "sdo.agent_runtime.responder.job"]
    assert config["broker"] == os.sys.executable
    assert config["broker_args"][:2] == ["-m", "sdo.agent_runtime.responder.broker_cli"]
    assert config["app_root"] == str(app_root)
    assert config["broker_worktree_root"] == str(tmp_path / "workspace" / "worktrees")
    assert config["verification_timeout"] == "90s"
    assert config["max_follow_ups"] == 3
    assert config["follow_up_cooldown"] == "45s"
    assert config["repair_policy"] == "recorded-actions"


def test_seed_go_cache_copies_trusted_image_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    seed = tmp_path / "seed"
    target = tmp_path / "target"
    (seed / "ab").mkdir(parents=True)
    (seed / "ab" / "entry").write_text("compiled", encoding="utf-8")
    monkeypatch.setenv("SDO_GO_CACHE_SEED", str(seed))
    monkeypatch.setenv("GOCACHE", str(target))

    assert seed_go_cache_from_environment() is True
    assert (target / "ab" / "entry").read_text(encoding="utf-8") == "compiled"
    assert "seeded GOCACHE" in capsys.readouterr().err


def test_missing_go_cache_seed_falls_back_to_a_cold_build_with_a_warning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Images built before the seed existed still get SDO_GO_CACHE_SEED from the controller Job.
    seed = tmp_path / "absent-seed"
    target = tmp_path / "target"
    monkeypatch.setenv("SDO_GO_CACHE_SEED", str(seed))
    monkeypatch.setenv("GOCACHE", str(target))

    assert seed_go_cache_from_environment() is False
    warning = capsys.readouterr().err
    assert "warning" in warning.lower()
    assert str(seed) in warning
    assert "cold" in warning


def test_go_cache_seed_that_is_a_file_is_still_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seed = tmp_path / "seed"
    seed.write_text("not a cache", encoding="utf-8")
    monkeypatch.setenv("SDO_GO_CACHE_SEED", str(seed))
    monkeypatch.setenv("GOCACHE", str(tmp_path / "target"))

    with pytest.raises(ValueError, match="not a directory"):
        seed_go_cache_from_environment()


_ENDPOINT_HEALTH_DETECTOR = """package objective

import (
    "context"
    "fmt"
    "time"

    "sdo.dev/controller/sdk"
)

var requiredServices = map[string]struct{}{"frontend": {}, "jaeger": {}}

type Detector struct{}

func New() sdk.Detector { return Detector{} }

func (Detector) Spec() sdk.DetectorSpec {
    return sdk.DetectorSpec{
        ID: "health-objective", Class: sdk.DetectorClassHealth, Owner: sdk.DetectorOwnerHealthJudge,
        Interval: 30 * time.Second,
        Watches: []sdk.WatchKind{{APIVersion: "v1", Kind: "Service"}},
        Persistence: sdk.PersistencePolicy{Firing: 2, Clearing: 2},
        Batching: sdk.BatchingPolicy{Severity: sdk.SeverityCritical, Debounce: 500 * time.Millisecond},
        Playbooks: []string{},
        OriginatingCommit: "lifecycle-bootstrap",
    }
}

func (Detector) Detect(_ context.Context, snapshot sdk.DetectionContext) ([]sdk.Finding, error) {
    findings := make([]sdk.Finding, 0)
    for _, service := range snapshot.Services() {
        if _, required := requiredServices[service.Name]; !required {
            continue
        }
        __GUARD__
        if snapshot.ReadyEndpointCountForService(service.Namespace, service.Name) == 0 {
            findings = append(findings, sdk.Finding{
                RuleID: "service-without-ready-endpoints", Status: sdk.FindingActive,
                Severity: sdk.SeverityCritical, Summary: "A selected service has no ready endpoints",
                Evidence: fmt.Sprintf("service %s/%s has zero ready endpoints", service.Namespace, service.Name),
                PrimaryResource: sdk.ObjectRefFrom("Service", "v1", &service),
            })
        }
    }
    return findings, nil
}
"""


def _write_health_app(app_root: Path, *, guard: str) -> None:
    diagnostics = app_root / ".sdo" / "diagnostics"
    detector_dir = diagnostics / "detectors" / "health" / "objective"
    detector_dir.mkdir(parents=True)
    (diagnostics / "go.mod").write_text(
        "module app-diagnostics\n\ngo 1.24\n\nrequire sdo.dev/controller/sdk v0.0.0\n",
        encoding="utf-8",
    )
    (diagnostics / "manifest.yaml").write_text(
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
        kind: Service
    interval: 30s
    persistence:
      firing: 2
      clearing: 2
    batching:
      severity: critical
      debounce: 500ms
    possiblePlaybooks: []
    originatingCommit: lifecycle-bootstrap
""",
        encoding="utf-8",
    )
    (detector_dir / "detector.go").write_text(_ENDPOINT_HEALTH_DETECTOR.replace("__GUARD__", guard), encoding="utf-8")
    # The source declares jaeger as an ordinary selected Service; the runtime
    # environment may still replace it with an ExternalName alias.
    source = app_root / "kubernetes" / "services.yaml"
    source.parent.mkdir(parents=True)
    source.write_text(
        """apiVersion: v1
kind: Service
metadata:
  name: frontend
spec:
  selector:
    app: frontend
---
apiVersion: v1
kind: Service
metadata:
  name: jaeger
spec:
  selector:
    app: jaeger
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: not-a-service
""",
        encoding="utf-8",
    )


def test_build_workspace_generates_externalname_invariant_for_source_services(tmp_path: Path) -> None:
    app_root = tmp_path / "app"
    tool_root = tmp_path / "sdo"
    _write_health_app(app_root, guard="")
    _write_tool_root(tool_root)
    hidden = app_root / ".sdo" / "scratch.yaml"
    hidden.write_text("kind: Service\nmetadata:\n  name: memory-only\n", encoding="utf-8")

    config = BuildWorkspaceConfig(
        app_root=app_root,
        sdk_dir=tool_root / "controller" / "sdk",
        core_dir=tool_root / "controller" / "core",
    )
    with BuildWorkspace.create(config) as workspace:
        invariant = (workspace.path / "generated" / "externalname_invariants_test.go").read_text(encoding="utf-8")

    assert "sdktest.ExternalNameEndpointViolations" in invariant
    assert "sdk.DetectorClassHealth" in invariant
    assert '"frontend"' in invariant
    assert '"jaeger"' in invariant
    assert '"not-a-service"' not in invariant
    assert '"memory-only"' not in invariant


@pytest.mark.parametrize(
    ("guard", "expected_exit"),
    [
        pytest.param("", 1, id="unguarded"),
        pytest.param("if !sdk.ServiceExpectsEndpoints(service) { continue }", 0, id="sdk-guard"),
        pytest.param('if service.Spec.Type == "ExternalName" { continue }', 0, id="type-guard"),
    ],
)
def test_check_cli_test_rejects_health_detector_that_flags_externalname_endpoints(
    tmp_path: Path,
    capfd: pytest.CaptureFixture[str],
    guard: str,
    expected_exit: int,
) -> None:
    app_root = tmp_path / "app"
    _write_health_app(app_root, guard=guard)

    exit_code = check_main(["test", "--app", str(app_root)])

    output = capfd.readouterr()
    assert exit_code == expected_exit, output.out + output.err
    if expected_exit:
        assert "ExternalName Service sdo-externalname-check/jaeger" in output.out
        assert "sdk.ServiceExpectsEndpoints" in output.out


def test_check_cli_supervised_controller_relaunches_after_each_closure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    app_root = tmp_path / "workspace" / "application"
    tool_root = tmp_path / "sdo"
    _write_app_diagnostics(app_root)
    _write_tool_root(tool_root)
    runs_log = tmp_path / "runs.log"
    configs_log = tmp_path / "configs.log"
    fake_go = tmp_path / "go"
    # The fake controller acknowledges one closure (exit 0), then fails (exit 3)
    # so the supervisor loop terminates and the test can inspect both launches.
    # runs_log keeps one argv line per launch (for the closure-count logic); the
    # --config payload of each launch is appended to configs_log, delimiter-framed.
    fake_go.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
prev=""
for a in "$@"; do
  if [ "$prev" = "-o" ]; then
    cat > "$a" <<'SCRIPT'
#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$SDO_RUNS_LOG"
if [ "$1" = "--config" ]; then printf '===CONFIG===\\n' >> "$SDO_CONFIGS_LOG"; cat "$2" >> "$SDO_CONFIGS_LOG"; fi
if [ "$(wc -l < "$SDO_RUNS_LOG")" -ge 2 ]; then exit 3; fi
exit 0
SCRIPT
    chmod +x "$a"
  fi
  prev="$a"
done
""",
        encoding="utf-8",
    )
    fake_go.chmod(fake_go.stat().st_mode | 0o111)
    monkeypatch.setenv("SDO_CONTROLLER_TOOL_ROOT", str(tool_root))
    monkeypatch.setenv("SDO_CONTROLLER_GO", str(fake_go))
    monkeypatch.setenv("SDO_RUNS_LOG", str(runs_log))
    monkeypatch.setenv("SDO_CONFIGS_LOG", str(configs_log))

    exit_code = check_main(
        [
            "controller",
            "--app",
            str(app_root),
            "--namespace",
            "demo",
            "--control-namespace",
            "demo-sdo",
            "--responder-image",
            "sdo-responder:v1",
            "--repository-pvc",
            "sdo-repository",
            "--credentials-secret",
            "sdo-codex-credentials",
            "--worktree-root",
            str(tmp_path / "workspace" / "worktrees"),
            "--supervise",
        ]
    )

    assert exit_code == 3
    runs = runs_log.read_text(encoding="utf-8").splitlines()
    assert len(runs) == 2
    for argv in runs:
        assert argv.split()[0] == "--config"
    blocks = [block for block in configs_log.read_text(encoding="utf-8").split("===CONFIG===\n") if block.strip()]
    assert len(blocks) == 2
    for block in blocks:
        config = json.loads(block)
        assert config["restart_after_closure"] is True
        assert "exit_after_closure" not in config  # omitted when false
        assert config["control_namespace"] == "demo-sdo"
        assert config["namespace"] == "demo"
    assert '"controller_supervisor": "relaunch"' in capsys.readouterr().out


def test_check_cli_rejects_supervising_a_bounded_controller(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        check_main(
            [
                "controller",
                "--app",
                str(tmp_path),
                "--namespace",
                "demo",
                "--responder-image",
                "r",
                "--repository-pvc",
                "p",
                "--credentials-secret",
                "s",
                "--worktree-root",
                str(tmp_path / "worktrees"),
                "--supervise",
                "--exit-after-closure",
            ]
        )


_INCIDENT_SELECTOR_DETECTOR = """package selector

import (
    "context"
    "fmt"
    "time"

    "sdo.dev/controller/sdk"
)

type Detector struct{}

func New() sdk.Detector { return Detector{} }

func (Detector) Spec() sdk.DetectorSpec {
    return sdk.DetectorSpec{
        ID: "service-selector-check", Class: sdk.DetectorClassIncident, Owner: sdk.DetectorOwnerResponder,
        Interval: 30 * time.Second,
        Watches: []sdk.WatchKind{{APIVersion: "v1", Kind: "Service"}, {APIVersion: "v1", Kind: "Pod"}},
        Persistence: sdk.PersistencePolicy{Firing: 2, Clearing: 2},
        Batching: sdk.BatchingPolicy{Severity: sdk.SeverityCritical, Debounce: 500 * time.Millisecond},
        Playbooks: []string{},
        OriginatingIncident: "incident-seed", OriginatingCommit: "abc123",
    }
}

func (Detector) Detect(_ context.Context, snapshot sdk.DetectionContext) ([]sdk.Finding, error) {
    findings := make([]sdk.Finding, 0)
    for _, service := range snapshot.Services() {
        if len(service.Spec.Selector) == 0 {
            continue
        }
        if __CONDITION__ {
            findings = append(findings, sdk.Finding{
                RuleID: "service-selector-check", Status: sdk.FindingActive,
                Severity: sdk.SeverityCritical, Summary: "Service selector problem",
                Evidence: fmt.Sprintf("service %s/%s", service.Namespace, service.Name),
                PrimaryResource: sdk.ObjectRefFrom("Service", "v1", &service),
            })
        }
    }
    return findings, nil
}
"""

_HEALTHY_BASELINE_SNAPSHOT = """{
  "namespace": "shop",
  "services": [
    {"metadata": {"name": "cart", "namespace": "shop"}, "spec": {"selector": {"app": "cart"}}}
  ],
  "pods": [
    {"metadata": {"name": "cart-0", "namespace": "shop", "labels": {"app": "cart"}}}
  ]
}
"""

# Fires on every selected Service, including healthy ones.
_NOISY_CONDITION = "true"
# Fires only when the selector matches no pod at all.
_QUIET_CONDITION = "len(snapshot.PodsForService(service.Namespace, service.Name)) == 0"


def _write_incident_app(app_root: Path, *, condition: str) -> None:
    diagnostics = app_root / ".sdo" / "diagnostics"
    detector_dir = diagnostics / "detectors" / "incident" / "selector"
    detector_dir.mkdir(parents=True)
    (diagnostics / "go.mod").write_text(
        "module app-diagnostics\n\ngo 1.24\n\nrequire sdo.dev/controller/sdk v0.0.0\n",
        encoding="utf-8",
    )
    (diagnostics / "manifest.yaml").write_text(
        """apiVersion: sdo.dev/v1alpha1
kind: DetectorManifest
sdkVersion: v0.1
detectors:
  - id: service-selector-check
    package: ./detectors/incident/selector
    constructor: New
    class: incident
    owner: responder
    watches:
      - apiVersion: v1
        kind: Service
      - apiVersion: v1
        kind: Pod
    interval: 30s
    persistence:
      firing: 2
      clearing: 2
    batching:
      severity: critical
      debounce: 500ms
    possiblePlaybooks: []
    originatingIncident: incident-seed
    originatingCommit: abc123
""",
        encoding="utf-8",
    )
    (detector_dir / "detector.go").write_text(
        _INCIDENT_SELECTOR_DETECTOR.replace("__CONDITION__", condition), encoding="utf-8"
    )


def _write_healthy_baseline(directory: Path) -> None:
    directory.mkdir(parents=True)
    (directory / "healthy.json").write_text(_HEALTHY_BASELINE_SNAPSHOT, encoding="utf-8")


def test_build_workspace_without_healthy_baseline_generates_no_baseline_test(tmp_path: Path) -> None:
    app_root = tmp_path / "app"
    tool_root = tmp_path / "sdo"
    _write_incident_app(app_root, condition=_NOISY_CONDITION)
    _write_tool_root(tool_root)

    config = BuildWorkspaceConfig(
        app_root=app_root,
        sdk_dir=tool_root / "controller" / "sdk",
        core_dir=tool_root / "controller" / "core",
    )
    with BuildWorkspace.create(config) as workspace:
        assert not (workspace.path / "generated" / "healthy_baseline_test.go").exists()


def test_build_workspace_with_healthy_baseline_copies_fixtures_and_generates_test(tmp_path: Path) -> None:
    app_root = tmp_path / "app"
    tool_root = tmp_path / "sdo"
    _write_incident_app(app_root, condition=_NOISY_CONDITION)
    _write_tool_root(tool_root)
    _write_healthy_baseline(app_root / "baseline")

    config = BuildWorkspaceConfig(
        app_root=app_root,
        sdk_dir=tool_root / "controller" / "sdk",
        core_dir=tool_root / "controller" / "core",
        healthy_baseline=app_root / "baseline",
    )
    with BuildWorkspace.create(config) as workspace:
        generated = (workspace.path / "generated" / "healthy_baseline_test.go").read_text(encoding="utf-8")
        copied = workspace.path / "generated" / "testdata" / "healthy-baseline" / "healthy.json"
        assert copied.read_text(encoding="utf-8") == _HEALTHY_BASELINE_SNAPSHOT

    assert "sdktest.HealthyBaselineViolations" in generated
    assert "sdk.DetectorClassIncident" in generated


def test_build_workspace_rejects_missing_or_empty_healthy_baseline(tmp_path: Path) -> None:
    app_root = tmp_path / "app"
    tool_root = tmp_path / "sdo"
    _write_incident_app(app_root, condition=_NOISY_CONDITION)
    _write_tool_root(tool_root)
    (tmp_path / "empty").mkdir()

    for baseline in (tmp_path / "missing", tmp_path / "empty"):
        config = BuildWorkspaceConfig(
            app_root=app_root,
            sdk_dir=tool_root / "controller" / "sdk",
            core_dir=tool_root / "controller" / "core",
            healthy_baseline=baseline,
        )
        with pytest.raises(ValueError, match="healthy baseline"):
            BuildWorkspace.create(config)


@pytest.mark.parametrize(
    ("condition", "expected_exit"),
    [
        pytest.param(_NOISY_CONDITION, 1, id="fires-on-healthy-service"),
        pytest.param(_QUIET_CONDITION, 0, id="quiet-on-healthy-service"),
    ],
)
def test_check_cli_test_rejects_incident_detector_that_fires_on_healthy_baseline(
    tmp_path: Path,
    capfd: pytest.CaptureFixture[str],
    condition: str,
    expected_exit: int,
) -> None:
    app_root = tmp_path / "app"
    _write_incident_app(app_root, condition=condition)
    _write_healthy_baseline(app_root / "baseline")

    exit_code = check_main(["test", "--app", str(app_root), "--healthy-baseline", "baseline"])

    output = capfd.readouterr()
    assert exit_code == expected_exit, output.out + output.err
    if expected_exit:
        assert "healthy baseline snapshot healthy.json" in output.out
        assert "service-selector-check" in output.out
        assert "Service shop/cart" in output.out


def test_check_cli_test_without_healthy_baseline_keeps_default_behavior(
    tmp_path: Path,
    capfd: pytest.CaptureFixture[str],
) -> None:
    app_root = tmp_path / "app"
    _write_incident_app(app_root, condition=_NOISY_CONDITION)

    exit_code = check_main(["test", "--app", str(app_root)])

    output = capfd.readouterr()
    assert exit_code == 0, output.out + output.err
    assert "healthy baseline" not in output.out


def test_check_cli_rejects_healthy_baseline_outside_the_application(tmp_path: Path) -> None:
    app_root = tmp_path / "app"
    _write_incident_app(app_root, condition=_NOISY_CONDITION)
    _write_healthy_baseline(tmp_path / "elsewhere")

    assert check_main(["test", "--app", str(app_root), "--healthy-baseline", "../elsewhere"]) == 1
