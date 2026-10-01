"""Reflection guidance that generalizes one detector across parameter variants of a root-cause class."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import pytest

from sdo.agent_runtime.responder import reflection_brief
from sdo.agent_runtime.responder.broker_cli import _argument_parser
from sdo.agent_runtime.responder.reflection import SessionReflector
from sdo.operational_memory import DETECTOR_SDK_REFERENCE, REFLECTION_GUIDANCE_MODES, BrokerClosure
from tests.unit.sdo.agent_runtime.responder.test_reflection import (
    _REVIEW,
    _CapturingBackend,
    _fresh_closure,
    _memory_worktree,
    _outcome,
)

if TYPE_CHECKING:
    from pathlib import Path

# Terms that identify a particular fault, workload, or benchmark. The reflection prompt and the brief's static
# text must stay problem-agnostic; data read from the repository at runtime is the agent's own memory.
_LEAK_DENYLIST = (
    r"probes?",
    r"readiness",
    r"liveness",
    r"startup",
    r"config-?maps?",
    r"secrets?",
    r"selectors?",
    r"dns",
    r"network-?polic\w*",
    r"network",
    r"mongo\w*",
    r"hotel",
    r"geo",
    r"reservation",
    r"recommendation",
    r"search",
    r"profile",
    r"frontend",
    r"memcached",
    r"redis",
    r"nginx",
    r"mounts?",
    r"volumes?",
    r"FailedMount",
    r"CrashLoop\w*",
    r"ImagePull\w*",
    r"OOM\w*",
    r"target-?ports?",
    r"port numbers?",
    r"wrong ports?",
    r"\d{4,5}",
    r"sregym",
    r"benchmarks?",
    r"oracle",
    r"verdicts?",
    r"injected|injection",
    r"chaos",
    r"misconfig\w*",
    r"typo",
    r"kubelet",
    r"scale[- ]?(to )?zero",
    r"replicas?",
    r"image",
)


def _leaks(text: str, *, runtime_paths: tuple[Path, ...] = ()) -> list[str]:
    for path in runtime_paths:  # temp directories are runtime data, not wording, and may contain digits
        text = text.replace(str(path), "")
    text = re.sub(r"\d{4}-\d\d-\d\d[T0-9:.+\-]*", "", text)  # runtime timestamps
    pattern = re.compile(r"(?<![A-Za-z0-9])(?:" + "|".join(_LEAK_DENYLIST) + r")(?![A-Za-z0-9])", re.IGNORECASE)
    return sorted({match.group(0) for match in pattern.finditer(text)})


def _request_text(tmp_path: Path, guidance: str) -> str:
    backend = _CapturingBackend()
    outcome = _outcome()
    SessionReflector(backend, guidance=guidance).resume(  # type: ignore[arg-type]
        session_id="session-1",
        incident_id="inc-1",
        worktree=tmp_path,
        outcome=outcome,
        history=[outcome],
        outcome_commit="outcome-sha",
        topology_review=_REVIEW,
    )
    prompt = str(backend.calls[0]["prompt"])
    # Runtime data (the outcome record and its history) and the trusted SDK API reference are not prompt wording.
    prompt = prompt.split("Current outcome:", 1)[0]
    return prompt.replace(DETECTOR_SDK_REFERENCE, "")


def _static_brief(tmp_path: Path, *, detail: bool) -> str:
    """The brief rendered with no runtime content: only its static wording remains."""

    empty = tmp_path / "empty"
    empty.mkdir()
    closure = _fresh_closure(empty)
    request = closure.request.model_copy(
        update={
            "findings": [],
            "detector_history": [],
            "surfaced_playbooks": [],
            "relevant_outcomes": [],
            "application": "app",
            "namespace": "ns",
            "source_commit": "c1",
            "deployed_commit": "c2",
        }
    )
    closure = closure.model_copy(
        update={"request": request, "result": None, "final_detector_states": [], "incident_detector_states": []}
    )
    return reflection_brief.incident_brief(closure, worktree=empty, detector_detail=detail)


@pytest.mark.parametrize("guidance", REFLECTION_GUIDANCE_MODES)
def test_reflection_prompt_wording_names_no_particular_problem(tmp_path: Path, guidance: str) -> None:
    assert _leaks(_request_text(tmp_path, guidance), runtime_paths=(tmp_path,)) == []


@pytest.mark.parametrize("detail", [False, True])
def test_brief_static_wording_names_no_particular_problem(tmp_path: Path, detail: bool) -> None:
    assert _leaks(_static_brief(tmp_path, detail=detail), runtime_paths=(tmp_path,)) == []


def test_leak_scanner_flags_problem_specific_terms() -> None:
    assert _leaks("the readiness probe on 8080 of the geo service") == [
        "8080",
        "geo",
        "probe",
        "readiness",
    ]
    assert _leaks("compare the confirmed signature with every existing incident detector") == []


def test_leak_scanner_ignores_temporary_paths_that_contain_digits(tmp_path: Path) -> None:
    digits = tmp_path / "pytest-1000" / "3666" / "4830"

    assert _leaks(f"worktree {digits}/x", runtime_paths=(digits,)) == []
    assert _leaks(f"worktree {digits}/x") != []


def test_baseline_guidance_keeps_the_per_cause_learning_directives(tmp_path: Path) -> None:
    prompt = _request_text(tmp_path, "baseline")

    assert "Generalization protocol" not in prompt
    assert "without generalizing beyond the observed successful evidence" in prompt
    assert "sharp fault-specific playbook" in prompt


def test_generalize_guidance_widens_an_existing_detector_instead_of_adding_a_sibling(tmp_path: Path) -> None:
    prompt = _request_text(tmp_path, "generalize")

    assert "Generalization protocol" in prompt
    assert "every existing incident detector" in prompt
    assert "ParameterBindings" in prompt
    assert "do not add a sibling detector or playbook" in prompt
    assert "near-miss" in prompt
    assert "originatingIncident" in prompt
    # The baseline directives that forbid generalizing are replaced, not contradicted.
    assert "without generalizing beyond the observed successful evidence" not in prompt
    assert "sharp fault-specific playbook" not in prompt


def test_spec_first_guidance_is_a_superset_of_generalize(tmp_path: Path) -> None:
    generalize = _request_text(tmp_path, "generalize")
    spec = _request_text(tmp_path, "generalize-spec")

    assert "Generalization protocol" in spec
    assert "do not add a sibling detector or playbook" in spec
    assert "Spec-first protocol" in spec
    assert "Spec-first protocol" not in generalize
    for phrase in (
        "current spec or status",
        "without any event objects",
        "only corroborate",
        "not decidable",
        "earliest available evidence",
    ):
        assert phrase in spec
        assert phrase not in generalize


def test_spec_first_guidance_does_not_change_the_other_modes(tmp_path: Path) -> None:
    for guidance in ("baseline", "generalize"):
        assert "Spec-first protocol" not in _request_text(tmp_path, guidance)


def test_spec_first_brief_reports_whether_each_detector_reads_events(tmp_path: Path) -> None:
    event_reader = _DESCRIBED_DETECTOR.replace("snap.Deployments()", "snap.Events()")
    worktree = _memory_worktree(tmp_path)
    detector = worktree / ".sdo/diagnostics/detectors/incidents/missing_configmap/detector.go"

    detector.write_text(event_reader, encoding="utf-8")
    brief = reflection_brief.incident_brief(
        _fresh_closure(worktree), worktree=worktree, detector_detail=True, event_dependence=True
    )
    assert "reads Event objects: yes" in brief

    detector.write_text(_DESCRIBED_DETECTOR, encoding="utf-8")
    brief = reflection_brief.incident_brief(
        _fresh_closure(worktree), worktree=worktree, detector_detail=True, event_dependence=True
    )
    assert "reads Event objects: no" in brief
    plain = reflection_brief.incident_brief(_fresh_closure(worktree), worktree=worktree, detector_detail=True)
    assert "reads Event objects" not in plain


def test_fresh_spec_first_reflection_receives_the_event_flag(tmp_path: Path) -> None:
    worktree = _memory_worktree(tmp_path)
    (worktree / ".sdo/diagnostics/detectors/incidents/missing_configmap/detector.go").write_text(
        _DESCRIBED_DETECTOR, encoding="utf-8"
    )
    outcome = _outcome()
    seen = {}
    for guidance in ("generalize", "generalize-spec"):
        backend = _CapturingBackend()
        SessionReflector(backend, guidance=guidance).resume(  # type: ignore[arg-type]
            session_id="s",
            incident_id="inc-1",
            worktree=worktree,
            outcome=outcome,
            history=[outcome],
            outcome_commit="outcome-sha",
            topology_review=_REVIEW,
            session_mode="fresh",
            closure=_fresh_closure(worktree),
        )
        seen[guidance] = str(backend.calls[0]["prompt"])

    assert "reads Event objects: no" in seen["generalize-spec"]
    assert "reads Event objects" not in seen["generalize"]


def test_unknown_guidance_is_rejected() -> None:
    with pytest.raises(ValueError, match="guidance"):
        SessionReflector(_CapturingBackend(), guidance="sibling")  # type: ignore[arg-type]


_DESCRIBED_DETECTOR = """package missing_configmap

import (
    "context"

    "sdo.dev/controller/sdk"
)

func New() sdk.Detector { return Detector{} }

type Detector struct{}

func (Detector) Spec() sdk.DetectorSpec {
    return sdk.DetectorSpec{
        ID:          "missing-configmap",
        Description: "Fires when a workload reports the widget condition.",
    }
}

func (Detector) Detect(ctx context.Context, snap sdk.DetectionContext) ([]sdk.Finding, error) {
    for _, d := range snap.Deployments() {
        if d.Name != "widget" {
            continue
        }
        return []sdk.Finding{{RuleID: "widget-condition"}}, nil
    }
    return nil, nil
}
"""


def _brief_with_described_detector(tmp_path: Path, *, detail: bool) -> str:
    worktree = _memory_worktree(tmp_path)
    (worktree / ".sdo/diagnostics/detectors/incidents/missing_configmap/detector.go").write_text(
        _DESCRIBED_DETECTOR, encoding="utf-8"
    )
    return reflection_brief.incident_brief(_fresh_closure(worktree), worktree=worktree, detector_detail=detail)


def test_generalizing_brief_shows_what_each_incident_detector_matches(tmp_path: Path) -> None:
    brief = _brief_with_described_detector(tmp_path, detail=True)

    assert "Fires when a workload reports the widget condition." in brief
    assert 'if d.Name != "widget"' in brief


def test_default_brief_lists_detectors_without_their_match_logic(tmp_path: Path) -> None:
    brief = _brief_with_described_detector(tmp_path, detail=False)

    assert "missing-configmap (package" in brief
    assert "widget" not in brief


def test_brief_bounds_each_detector_predicate_excerpt(tmp_path: Path) -> None:
    worktree = _memory_worktree(tmp_path)
    huge = _DESCRIBED_DETECTOR.replace("return nil, nil", "x := 1\n" * 5000 + "return nil, nil")
    (worktree / ".sdo/diagnostics/detectors/incidents/missing_configmap/detector.go").write_text(huge, encoding="utf-8")

    brief = reflection_brief.incident_brief(_fresh_closure(worktree), worktree=worktree, detector_detail=True)

    assert len(brief) <= reflection_brief.BRIEF_MAX_CHARS + 200
    assert "omitted" in brief


def test_fresh_generalizing_reflection_receives_the_detailed_brief(tmp_path: Path) -> None:
    worktree = _memory_worktree(tmp_path)
    (worktree / ".sdo/diagnostics/detectors/incidents/missing_configmap/detector.go").write_text(
        _DESCRIBED_DETECTOR, encoding="utf-8"
    )
    outcome = _outcome()
    prompts = {}
    for guidance in REFLECTION_GUIDANCE_MODES:
        backend = _CapturingBackend()
        SessionReflector(backend, guidance=guidance).resume(  # type: ignore[arg-type]
            session_id="s",
            incident_id="inc-1",
            worktree=worktree,
            outcome=outcome,
            history=[outcome],
            outcome_commit="outcome-sha",
            topology_review=_REVIEW,
            session_mode="fresh",
            closure=_fresh_closure(worktree),
        )
        prompts[guidance] = str(backend.calls[0]["prompt"])

    assert 'if d.Name != "widget"' in prompts["generalize"]
    assert 'if d.Name != "widget"' not in prompts["baseline"]


def test_broker_cli_selects_the_reflection_guidance() -> None:
    parser = _argument_parser()
    base = ["--repository", "r", "--worktree-root", "w"]

    assert parser.parse_args(base).reflection_guidance == "baseline"
    assert parser.parse_args([*base, "--reflection-guidance", "generalize"]).reflection_guidance == "generalize"
    spec = parser.parse_args([*base, "--reflection-guidance", "generalize-spec"])
    assert spec.reflection_guidance == "generalize-spec"
    with pytest.raises(SystemExit):
        parser.parse_args([*base, "--reflection-guidance", "sibling"])


def test_closure_type_is_exported_for_brief_callers() -> None:
    assert BrokerClosure is not None
