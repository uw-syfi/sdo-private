"""The builder compiles traffic generators into an isolated prober and proves them."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from controller.builder.check_cli import main as check_main
from controller.builder.workspace import BuildWorkspace, BuildWorkspaceConfig
from tests.unit.controller.builder.test_checker import _write_health_app, _write_tool_root

FIXTURE = Path(__file__).resolve().parents[3] / "fixtures" / "hotel_reservation" / "traffic"
GUARD = "if !sdk.ServiceExpectsEndpoints(service) { continue }"

TRAFFIC_DETECTOR = """package traffichealth

import (
    "time"

    "sdo.dev/controller/sdk"
    "sdo.dev/controller/sdk/traffic"
)

func New() sdk.Detector {
    return traffic.NewDetector(sdk.DetectorSpec{
        ID: "traffic-health", Class: sdk.DetectorClassHealth, Owner: sdk.DetectorOwnerHealthJudge,
        Watches: []sdk.WatchKind{traffic.Watch}, Interval: 10 * time.Second,
        Persistence: sdk.PersistencePolicy{Firing: 2, Clearing: 2},
        Batching: sdk.BatchingPolicy{Severity: sdk.SeverityCritical, Debounce: 500 * time.Millisecond},
        Playbooks: []string{}, OriginatingCommit: "lifecycle-bootstrap",
    }, "__WORKLOAD__")
}
"""

TRAFFIC_REGISTRATION = """  - id: traffic-health
    package: ./detectors/health/traffic-health
    constructor: New
    class: health
    owner: health_judge
    watches:
      - apiVersion: sdo.dev/v1alpha1
        kind: SyntheticTraffic
    interval: 10s
    persistence:
      firing: 2
      clearing: 2
    batching:
      severity: critical
      debounce: 500ms
    possiblePlaybooks: []
    originatingCommit: lifecycle-bootstrap
"""


def _hotel_app(app_root: Path, *, workload: str = "health") -> Path:
    _write_health_app(app_root, guard=GUARD)
    diagnostics = app_root / ".sdo" / "diagnostics"
    shutil.copytree(FIXTURE, diagnostics / "traffic")
    detector = diagnostics / "detectors" / "health" / "traffic-health"
    detector.mkdir(parents=True)
    (detector / "detector.go").write_text(TRAFFIC_DETECTOR.replace("__WORKLOAD__", workload), encoding="utf-8")
    manifest = diagnostics / "manifest.yaml"
    manifest.write_text(manifest.read_text(encoding="utf-8") + TRAFFIC_REGISTRATION, encoding="utf-8")
    return diagnostics


LINK_DETECTOR = """package trafficlinks

import (
    "time"

    "sdo.dev/controller/sdk"
    "sdo.dev/controller/sdk/traffic"
)

func New() sdk.Detector {
    return traffic.NewLinkDetector(sdk.DetectorSpec{
        ID: "traffic-links", Class: sdk.DetectorClassHealth, Owner: sdk.DetectorOwnerHealthJudge,
        Watches: []sdk.WatchKind{traffic.Watch}, Interval: 10 * time.Second,
        Persistence: sdk.PersistencePolicy{Firing: 2, Clearing: 2},
        Batching: sdk.BatchingPolicy{Severity: sdk.SeverityCritical, Debounce: 500 * time.Millisecond},
        Playbooks: []string{}, OriginatingCommit: "lifecycle-bootstrap",
    }, "links")
}
"""

LINK_WORKLOAD = """apiVersion: sdo.dev/v1alpha1
kind: TrafficWorkload
name: links
purpose: link-probe
links:
  - from: frontend
    to: search
    port: 8082
"""


def _add_link_probe(diagnostics: Path) -> None:
    (diagnostics / "traffic" / "workloads" / "links.yaml").write_text(LINK_WORKLOAD, encoding="utf-8")
    detector = diagnostics / "detectors" / "health" / "traffic-links"
    detector.mkdir(parents=True)
    (detector / "detector.go").write_text(LINK_DETECTOR, encoding="utf-8")
    manifest = diagnostics / "manifest.yaml"
    registration = TRAFFIC_REGISTRATION.replace("traffic-health", "traffic-links")
    manifest.write_text(manifest.read_text(encoding="utf-8") + registration, encoding="utf-8")


def _workspace(app_root: Path, tool_root: Path) -> BuildWorkspace:
    _write_tool_root(tool_root)
    return BuildWorkspace.create(
        BuildWorkspaceConfig(
            app_root=app_root, sdk_dir=tool_root / "controller" / "sdk", core_dir=tool_root / "controller" / "core"
        )
    )


def test_workspace_generates_a_separate_prober_with_embedded_workloads(tmp_path: Path) -> None:
    _hotel_app(tmp_path / "app")
    with _workspace(tmp_path / "app", tmp_path / "sdo") as workspace:
        assert workspace.has_prober
        catalog = (workspace.path / "generated" / "traffic.go").read_text(encoding="utf-8")
        prober_main = (workspace.path / "cmd" / "prober" / "main.go").read_text(encoding="utf-8")
        controller_main = (workspace.path / "cmd" / "controller" / "main.go").read_text(encoding="utf-8")
    assert '"health": []byte(' in catalog
    assert '"verify": []byte(' in catalog
    assert "prober.Main(generated.TrafficCatalog(), generated.TrafficWorkloads)" in prober_main
    assert "traffic/generators" not in controller_main


@pytest.mark.parametrize(
    ("source", "message"),
    [
        pytest.param('import "net/http"\n\nvar _ = http.Get\n', "may not import 'net/http'", id="network"),
        pytest.param('import "os"\n\nvar _ = os.Getenv\n', "may not import 'os'", id="environment"),
        pytest.param('import "time"\n\nvar _ = time.Now\n', "time.Now", id="clock"),
        pytest.param('import "math/rand/v2"\n\nvar _ = rand.IntN\n', "rand.IntN", id="global-rng"),
    ],
)
def test_generators_may_not_reach_the_network_clock_or_global_randomness(
    tmp_path: Path, source: str, message: str
) -> None:
    diagnostics = _hotel_app(tmp_path / "app")
    extra = diagnostics / "traffic" / "generators" / "extra.go"
    extra.write_text("package generators\n\n" + source, encoding="utf-8")

    with pytest.raises(ValueError, match="invalid traffic generators") as raised:
        _workspace(tmp_path / "app", tmp_path / "sdo")
    assert message in str(raised.value)


def test_workloads_without_generators_are_rejected(tmp_path: Path) -> None:
    diagnostics = _hotel_app(tmp_path / "app")
    shutil.rmtree(diagnostics / "traffic" / "generators")

    with pytest.raises(ValueError, match="no Go package"):
        _workspace(tmp_path / "app", tmp_path / "sdo")


def test_link_probe_workloads_need_no_generators(tmp_path: Path) -> None:
    diagnostics = _hotel_app(tmp_path / "app")
    shutil.rmtree(diagnostics / "traffic" / "generators")
    for workload in (diagnostics / "traffic" / "workloads").iterdir():
        workload.unlink()
    (diagnostics / "traffic" / "workloads" / "topology-links.yaml").write_text(
        LINK_WORKLOAD.replace("name: links", "name: topology-links").replace("from: frontend", "from: sdo-prober"),
        encoding="utf-8",
    )

    with _workspace(tmp_path / "app", tmp_path / "sdo") as workspace:
        assert workspace.has_prober
        catalog = (workspace.path / "generated" / "traffic.go").read_text(encoding="utf-8")
        assert (workspace.path / "cmd" / "prober" / "main.go").is_file()
    assert "catalog := traffic.Catalog{}" in catalog
    assert '"topology-links": []byte(' in catalog
    assert "generators" not in catalog.replace("TrafficCatalog is every scenario the application's generators", "")


def test_scenario_workloads_beside_link_probes_still_need_generators(tmp_path: Path) -> None:
    diagnostics = _hotel_app(tmp_path / "app")
    (diagnostics / "traffic" / "workloads" / "links.yaml").write_text(LINK_WORKLOAD, encoding="utf-8")
    shutil.rmtree(diagnostics / "traffic" / "generators")

    with pytest.raises(ValueError, match="no Go package"):
        _workspace(tmp_path / "app", tmp_path / "sdo")


def test_check_accepts_the_hotel_generators_and_builds_the_prober(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    _hotel_app(tmp_path / "app")

    exit_code = check_main(["test", "--app", str(tmp_path / "app")])

    output = capfd.readouterr()
    assert exit_code == 0, output.out + output.err


def test_check_accepts_an_application_that_has_only_link_probes(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    diagnostics = _hotel_app(tmp_path / "app")
    shutil.rmtree(diagnostics / "traffic")
    shutil.rmtree(diagnostics / "detectors" / "health" / "traffic-health")
    manifest = diagnostics / "manifest.yaml"
    manifest.write_text(manifest.read_text(encoding="utf-8").replace(TRAFFIC_REGISTRATION, ""), encoding="utf-8")
    (diagnostics / "traffic" / "workloads").mkdir(parents=True)
    _add_link_probe(diagnostics)

    exit_code = check_main(["test", "--app", str(tmp_path / "app")])

    output = capfd.readouterr()
    assert exit_code == 0, output.out + output.err


def test_check_accepts_a_link_probe_detector_beside_the_health_detector(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    _add_link_probe(_hotel_app(tmp_path / "app"))

    exit_code = check_main(["test", "--app", str(tmp_path / "app")])

    output = capfd.readouterr()
    assert exit_code == 0, output.out + output.err


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        pytest.param(
            lambda diagnostics: _replace(
                diagnostics / "traffic" / "generators" / "generators.go",
                '.Contains("Login successfully!")',
                "",
            ),
            "does not detect fault class wrong-body",
            id="weak-check",
        ),
        pytest.param(
            lambda diagnostics: _replace(
                diagnostics / "traffic" / "workloads" / "health.yaml", "id: login", "id: checkout"
            ),
            'scenario "checkout" is not provided',
            id="unknown-scenario",
        ),
        pytest.param(
            lambda diagnostics: _replace(
                diagnostics / "detectors" / "health" / "traffic-health" / "detector.go", '"health")', '"verify")'
            ),
            "not a health-probe workload",
            id="detector-consumes-burst",
        ),
    ],
)
def test_check_rejects_generators_and_workloads_that_cannot_judge_health(
    tmp_path: Path, capfd: pytest.CaptureFixture[str], mutate: object, expected: str
) -> None:
    diagnostics = _hotel_app(tmp_path / "app")
    mutate(diagnostics)  # type: ignore[operator]

    exit_code = check_main(["test", "--app", str(tmp_path / "app")])

    output = capfd.readouterr()
    assert exit_code != 0
    assert expected in output.out + output.err


def _replace(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text
    path.write_text(text.replace(old, new), encoding="utf-8")


def test_controller_publishes_the_prober_binary_and_passes_it_to_the_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mount = tmp_path / "workspace"
    app_root = mount / "application"
    _hotel_app(app_root)
    tool_root = tmp_path / "sdo"
    _write_tool_root(tool_root)
    calls_log = tmp_path / "go-calls.log"
    controller_log = tmp_path / "controller.log"
    fake_go = tmp_path / "go"
    fake_go.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
printf '%s|%s|%s\\n' "$PWD" "${CGO_ENABLED:-}" "$*" >> "$SDO_GO_CALLS_LOG"
prev=""
for a in "$@"; do
  if [ "$prev" = "-o" ]; then
    printf '#!/usr/bin/env bash\\nprintf "%%s\\n" "$*" > "$SDO_CONTROLLER_LOG"\\n' > "$a"
    chmod +x "$a"
  fi
  prev="$a"
done
""",
        encoding="utf-8",
    )
    fake_go.chmod(0o755)
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
            "hotel-reservation",
            "--responder-image",
            "sdo-responder:v1",
            "--prober-image",
            "sdo-controller:v1",
            "--repository-pvc",
            "sdo-repository",
            "--repository-mount-path",
            str(mount),
            "--credentials-secret",
            "sdo-codex-credentials",
            "--worktree-root",
            str(mount / "worktrees"),
        ]
    )

    assert exit_code == 0
    calls = calls_log.read_text(encoding="utf-8").splitlines()
    prober_build = [line for line in calls if line.endswith("./cmd/prober")]
    assert len(prober_build) == 1
    assert prober_build[0].split("|")[1] == "0"
    published = list((mount / ".sdo-prober").glob("*/sdo-prober"))
    assert len(published) == 1
    argv = controller_log.read_text(encoding="utf-8")
    assert f"--prober-binary {published[0]}" in argv
    assert "--prober-image sdo-controller:v1" in argv
