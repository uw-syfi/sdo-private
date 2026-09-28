"""Generated wiring for an application's synthetic-traffic generators.

The health judge writes Go generators in ``.sdo/diagnostics/traffic/generators/``
(a package exporting ``Scenarios() traffic.Catalog``; responders may add
incident-scoped ones in its ``incident/`` subpackage) and workload profiles in
``.sdo/diagnostics/traffic/workloads/<name>.yaml``. The builder compiles them
into a separate prober binary, never into the controller, and generates
tests that prove the generators detect their fault classes and the
workloads name existing scenarios.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

GENERATORS_DIR = Path("traffic") / "generators"
INCIDENT_GENERATORS_DIR = GENERATORS_DIR / "incident"
WORKLOADS_DIR = Path("traffic") / "workloads"
PROBER_MAIN_DIR = Path("cmd") / "prober"
WORKLOAD_NAME_RE = re.compile(r"[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?")

# Generators build requests and judge responses; the engine owns time,
# randomness, and the network. These imports cannot reach the network,
# the filesystem, or the cluster.
ALLOWED_GENERATOR_IMPORTS = frozenset(
    {
        "bytes",
        "context",
        "encoding/base64",
        "encoding/json",
        "errors",
        "fmt",
        "math",
        "math/rand/v2",
        "net/url",
        "regexp",
        "sort",
        "strconv",
        "strings",
        "time",
        "unicode",
        "unicode/utf8",
        "sdo.dev/controller/sdk/traffic",
    }
)
# Package-level randomness and clock reads would make iterations
# unreplayable or let generators measure time themselves.
_FORBIDDEN_CALL_RE = re.compile(
    r"\brand\.(?!Rand\b)[A-Z]\w*|\btime\.(Now|Since|Until|Sleep|After|AfterFunc|Tick|NewTimer|NewTicker)\b"
)
_IMPORT_BLOCK_RE = re.compile(r"^import\s*\((.*?)^\)", re.MULTILINE | re.DOTALL)
_IMPORT_LINE_RE = re.compile(r'^import\s+(?:[\w.]+\s+)?"([^"]+)"', re.MULTILINE)
_QUOTED_RE = re.compile(r'"([^"]+)"')


def _go_sources(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(path for path in directory.glob("*.go") if path.is_file())


def _imports(source: str) -> list[str]:
    imports = list(_IMPORT_LINE_RE.findall(source))
    for block in _IMPORT_BLOCK_RE.findall(source):
        imports.extend(_QUOTED_RE.findall(block))
    return imports


def generator_source_errors(diagnostics_dir: Path, module_path: str) -> list[str]:
    """Return static violations in generator packages (test files may import anything)."""

    errors: list[str] = []
    allowed_local = {f"{module_path}/{GENERATORS_DIR.as_posix()}"}
    for package in (GENERATORS_DIR, INCIDENT_GENERATORS_DIR):
        for path in _go_sources(diagnostics_dir / package):
            if path.name.endswith("_test.go"):
                continue
            source = path.read_text(encoding="utf-8")
            relative = path.relative_to(diagnostics_dir).as_posix()
            errors.extend(
                f"{relative}: generators may not import {imported!r}"
                for imported in _imports(source)
                if imported not in ALLOWED_GENERATOR_IMPORTS and imported not in allowed_local
            )
            errors.extend(
                f"{relative}: generators may not call {match.group(0)}; "
                "use the engine's rng and let the engine measure time"
                for match in _FORBIDDEN_CALL_RE.finditer(source)
            )
    packages = {diagnostics_dir / GENERATORS_DIR, diagnostics_dir / INCIDENT_GENERATORS_DIR}
    errors.extend(
        f"{path.relative_to(diagnostics_dir).as_posix()}: generators live in exactly two packages"
        for path in sorted((diagnostics_dir / GENERATORS_DIR).rglob("*.go"))
        if path.parent not in packages
    )
    return errors


def load_workload_documents(diagnostics_dir: Path) -> dict[str, dict[str, object]]:
    """Load workload YAML documents by file name, without semantic validation."""

    workloads: dict[str, dict[str, object]] = {}
    directory = diagnostics_dir / WORKLOADS_DIR
    if not directory.is_dir():
        return workloads
    for path in sorted(directory.iterdir()):
        if path.suffix not in {".yaml", ".yml"} or not path.is_file():
            raise ValueError(f"traffic workloads must be <name>.yaml files: {path.name}")
        if not WORKLOAD_NAME_RE.fullmatch(path.stem):
            raise ValueError(f"traffic workload file name must be a DNS label: {path.name}")
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(document, dict):
            raise ValueError(f"traffic workload {path.name} must be a mapping")
        workloads[path.stem] = document
    return workloads


def write_generated_traffic(workspace_path: Path, *, module_path: str) -> bool:
    """Generate the traffic catalog, conformance tests, and prober main.

    Returns whether the application has generators, and so a prober.
    """

    generators = _go_sources(workspace_path / GENERATORS_DIR)
    workloads = load_workload_documents(workspace_path)
    if not generators:
        if workloads:
            raise ValueError("traffic workloads exist but .sdo/diagnostics/traffic/generators/ has no Go package")
        _write_no_traffic(workspace_path)
        return False
    errors = generator_source_errors(workspace_path, module_path)
    if errors:
        raise ValueError("invalid traffic generators:\n" + "\n".join(errors))
    has_incident = bool(
        [path for path in _go_sources(workspace_path / INCIDENT_GENERATORS_DIR) if not path.name.endswith("_test.go")]
    )
    imports = [
        '\t"sdo.dev/controller/sdk/traffic"',
        f'\tgenerators "{module_path}/{GENERATORS_DIR.as_posix()}"',
    ]
    catalog = "\tcatalog := append(traffic.Catalog{}, generators.Scenarios()...)\n"
    if has_incident:
        imports.append(f'\tincident "{module_path}/{INCIDENT_GENERATORS_DIR.as_posix()}"')
        catalog += "\tcatalog = append(catalog, incident.Scenarios()...)\n"
    rendered_workloads = "".join(
        f"\t{json.dumps(name)}: []byte({json.dumps(json.dumps(document, sort_keys=True))}),\n"
        for name, document in sorted(workloads.items())
    )
    source = (
        "package generated\n\nimport (\n" + "\n".join(imports) + "\n)\n\n"
        "// TrafficCatalog is every scenario the application's generators provide.\n"
        "func TrafficCatalog() traffic.Catalog {\n" + catalog + "\treturn catalog\n}\n\n"
        "// TrafficWorkloads are the workload profiles, as JSON, by name.\n"
        "var TrafficWorkloads = map[string][]byte{\n" + rendered_workloads + "}\n"
    )
    generated = workspace_path / "generated"
    generated.mkdir(parents=True, exist_ok=True)
    (generated / "traffic.go").write_text(source, encoding="utf-8")
    (generated / "traffic_test.go").write_text(_TRAFFIC_TEST, encoding="utf-8")
    main_dir = workspace_path / PROBER_MAIN_DIR
    main_dir.mkdir(parents=True, exist_ok=True)
    (main_dir / "main.go").write_text(
        f"""package main

import (
\t"sdo.dev/controller/runtime/prober"
\t"{module_path}/generated"
)

func main() {{
\tprober.Main(generated.TrafficCatalog(), generated.TrafficWorkloads)
}}
""",
        encoding="utf-8",
    )
    return True


def _write_no_traffic(workspace_path: Path) -> None:
    generated = workspace_path / "generated"
    generated.mkdir(parents=True, exist_ok=True)
    (generated / "traffic_test.go").write_text(_NO_TRAFFIC_TEST, encoding="utf-8")


_TRAFFIC_TEST = """package generated

import (
\t"context"
\t"testing"

\t"sdo.dev/controller/runtime/prober"
\t"sdo.dev/controller/sdk/traffic"
)

func TestTrafficGeneratorsDetectTheirFaultClasses(t *testing.T) {
\tfor _, failure := range traffic.CheckCatalog(context.Background(), TrafficCatalog()) {
\t\tt.Error(failure)
\t}
}

func TestTrafficWorkloadsRunOnlyProvidedScenarios(t *testing.T) {
\tworkloads, err := prober.ParseWorkloads(TrafficWorkloads)
\tif err != nil {
\t\tt.Fatal(err)
\t}
\tconfig := prober.Config{Namespace: "check", Catalog: TrafficCatalog(), Workloads: workloads}
\tif _, err := prober.New(config); err != nil {
\t\tt.Fatal(err)
\t}
}

func TestTrafficDetectorsConsumeHealthProbeWorkloads(t *testing.T) {
\tworkloads, err := prober.ParseWorkloads(TrafficWorkloads)
\tif err != nil {
\t\tt.Fatal(err)
\t}
\tpurposes := map[string]traffic.Purpose{}
\tfor _, workload := range workloads {
\t\tpurposes[workload.Name] = workload.Purpose
\t}
\tfor _, detector := range All() {
\t\tconsumer, ok := detector.(traffic.Consumer)
\t\tif !ok {
\t\t\tcontinue
\t\t}
\t\tfor _, name := range consumer.TrafficWorkloads() {
\t\t\tif purposes[name] != traffic.PurposeHealthProbe {
\t\t\t\tt.Errorf("detector %s consumes %q, which is not a health-probe workload", detector.Spec().ID, name)
\t\t\t}
\t\t}
\t}
}
"""

_NO_TRAFFIC_TEST = """package generated

import (
\t"testing"

\t"sdo.dev/controller/sdk/traffic"
)

func TestTrafficDetectorsHaveGenerators(t *testing.T) {
\tfor _, detector := range All() {
\t\tif consumer, ok := detector.(traffic.Consumer); ok && len(consumer.TrafficWorkloads()) > 0 {
\t\t\tt.Errorf("detector %s consumes traffic workloads but the application has no generators", detector.Spec().ID)
\t\t}
\t}
}
"""
