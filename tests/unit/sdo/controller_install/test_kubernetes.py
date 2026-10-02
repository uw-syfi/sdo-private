from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from sdo.controller_install import (
    ControllerInstallConfig,
    controller_resources,
    install_controller,
    kubernetes,
    set_controller_maintenance,
)


def _config() -> ControllerInstallConfig:
    return ControllerInstallConfig(
        repository=Path("/tmp/application"),
        namespace="demo",
        application="demo",
        controller_image="controller:test",
        responder_image="responder:test",
        repository_pvc="repository",
        credentials_secret="credentials",
        model="gpt-test",
        timeout_seconds=60,
    )


def test_production_runtime_resources_have_no_benchmark_transport() -> None:
    resources = controller_resources(_config())
    serialized = repr(resources).upper()
    controller = next(resource for resource in resources if resource["kind"] == "Job")
    args = controller["spec"]["template"]["spec"]["containers"][0]["args"]

    assert "SREGYM" not in serialized
    assert "SUBMISSION" not in serialized
    assert not any(resource["metadata"]["name"] == "sdo-sregym-bridge" for resource in resources)
    assert "--exit-after-closure" not in args
    assert "--duration" not in args
    assert args[args.index("--response-timeout") + 1] == "60s"
    assert args[args.index("--verification-timeout") + 1] == "120s"
    assert "--responder-env=SDO_RESPONDER_MODEL=gpt-test" in args
    assert args[args.index("--repair-policy") + 1] == "commit"


def test_recorded_actions_policy_is_passed_to_controller() -> None:
    config = ControllerInstallConfig(**{**_config().__dict__, "repair_policy": "recorded-actions"})
    controller = next(resource for resource in controller_resources(config) if resource["kind"] == "Job")
    args = controller["spec"]["template"]["spec"]["containers"][0]["args"]

    assert args[args.index("--repair-policy") + 1] == "recorded-actions"


def _broker_args(config: ControllerInstallConfig) -> list[str]:
    controller = next(resource for resource in controller_resources(config) if resource["kind"] == "Job")
    args = controller["spec"]["template"]["spec"]["containers"][0]["args"]
    return [arg.removeprefix("--broker-arg=") for arg in args if arg.startswith("--broker-arg=")]


def test_reflection_session_mode_defaults_to_fresh_and_is_passed_to_the_broker() -> None:
    broker = _broker_args(_config())

    assert broker[broker.index("--reflection-session") + 1] == "fresh"
    # A fresh reflection brief quotes the responder's commands from its per-turn log.
    assert broker[broker.index("--responder-turn-log") + 1] == "/workspace/.sdo-runtime/usage/responder-turns.jsonl"


def test_fresh_reflection_session_mode_is_passed_to_the_broker() -> None:
    config = ControllerInstallConfig(**{**_config().__dict__, "reflection_session": "fresh"})
    broker = _broker_args(config)

    assert broker[broker.index("--reflection-session") + 1] == "fresh"


def test_unknown_reflection_session_mode_is_rejected() -> None:
    with pytest.raises(ValueError, match="reflection_session"):
        ControllerInstallConfig(**{**_config().__dict__, "reflection_session": "transcript"})


def test_production_runtime_module_has_no_benchmark_dependency() -> None:
    source = Path(__file__).parents[4] / "sdo" / "controller_install" / "kubernetes.py"
    contents = source.read_text(encoding="utf-8").upper()

    assert "SREGYM" not in contents
    assert "PRODUCTION_RECEIPT" not in contents


def test_broker_and_responder_pods_log_per_turn_usage_on_the_workspace_pvc() -> None:
    controller = next(resource for resource in controller_resources(_config()) if resource["kind"] == "Job")
    container = controller["spec"]["template"]["spec"]["containers"][0]
    args = container["args"]

    # The broker, and so every reflection turn, runs inside the controller pod.
    assert {
        "name": "SDO_TURN_USAGE_LOG",
        "value": "/workspace/.sdo-runtime/usage/controller-turns.jsonl",
    } in container["env"]
    assert "--responder-env=SDO_TURN_USAGE_LOG=/workspace/.sdo-runtime/usage/responder-turns.jsonl" in args
    assert {"name": "repository", "mountPath": "/workspace"} in container["volumeMounts"]


def test_controller_seeds_its_go_build_cache_from_the_image() -> None:
    """A cold in-pod compile of the detector controller took about 110 s at the
    pod's 2-CPU limit; the image's warm cache brings it to seconds."""

    controller = next(resource for resource in controller_resources(_config()) if resource["kind"] == "Job")
    env = controller["spec"]["template"]["spec"]["containers"][0]["env"]

    assert {"name": "SDO_GO_CACHE_SEED", "value": "/opt/sdo/go-build-cache"} in env
    assert {"name": "GOCACHE", "value": "/workspace/.sdo-runtime/build/go-cache"} in env


def test_go_cache_seeds_are_built_for_the_cgo_free_runtimes() -> None:
    """Cache keys include the cgo setting; the runtimes have no C compiler, so a
    seed compiled with cgo on misses almost entirely (88 s instead of 6 s)."""

    root = Path(__file__).resolve().parents[4]
    runtime = (root / "controller/Dockerfile.runtime").read_text(encoding="utf-8")
    validator = (root / "controller/Dockerfile.validator").read_text(encoding="utf-8")

    for dockerfile in (runtime, validator):
        seed_stage = dockerfile.split("FROM ", 2)[1]
        assert "CGO_ENABLED=0" in seed_stage
        assert "GOCACHE=/go/build-cache" in seed_stage
    controller_stage = runtime.split("AS controller", 1)[1].split("FROM ", 1)[0]
    assert "COPY --from=go-runtime /go/build-cache /opt/sdo/go-build-cache" in controller_stage


def _split_config(**overrides: object) -> ControllerInstallConfig:
    return ControllerInstallConfig(**{**_config().__dict__, "controller_namespace": "demo-sdo", **overrides})


def _job(resources: list[dict[str, Any]]) -> dict[str, Any]:
    return next(resource for resource in resources if resource["kind"] == "Job")


def test_split_namespace_keeps_every_sdo_workload_and_state_out_of_the_application_namespace() -> None:
    resources = controller_resources(_split_config())
    in_app = [resource for resource in resources if resource["metadata"].get("namespace") == "demo"]
    namespace = next(resource for resource in resources if resource["kind"] == "Namespace")

    assert namespace["metadata"]["name"] == "demo-sdo"
    assert namespace["metadata"]["labels"]["sdo.dev/application-namespace"] == "demo"
    # The application namespace receives only namespace-scoped access grants.
    assert {resource["kind"] for resource in in_app} == {"Role", "RoleBinding"}
    for kind in ("PersistentVolumeClaim", "Pod", "Job", "ServiceAccount"):
        assert all(
            resource["metadata"]["namespace"] == "demo-sdo" for resource in resources if resource["kind"] == kind
        ), kind
    assert not any(resource["kind"] in {"ClusterRole", "ClusterRoleBinding"} for resource in resources)
    for binding in (resource for resource in in_app if resource["kind"] == "RoleBinding"):
        assert all(subject["namespace"] == "demo-sdo" for subject in binding["subjects"])
    observer = next(
        resource
        for resource in in_app
        if resource["kind"] == "Role" and resource["metadata"]["name"] == "sdo-controller"
    )
    assert {verb for rule in observer["rules"] for verb in rule["verbs"]} <= {"get", "list", "watch"}
    responder = next(
        resource
        for resource in in_app
        if resource["kind"] == "Role" and resource["metadata"]["name"] == "sdo-responder"
    )
    assert any("patch" in rule["verbs"] for rule in responder["rules"])


def test_split_namespace_grants_exec_only_in_the_application_namespace() -> None:
    """Exec parity: the responder may exec in the app namespace, never in the SDO control namespace."""
    resources = controller_resources(_split_config())

    def responder_role(namespace: str) -> dict[str, Any]:
        return next(
            resource
            for resource in resources
            if resource["kind"] == "Role"
            and resource["metadata"]["name"] == "sdo-responder"
            and resource["metadata"]["namespace"] == namespace
        )

    def subresources(role: dict[str, Any]) -> set[str]:
        return {resource for rule in role["rules"] for resource in rule["resources"] if "/" in resource}

    app = responder_role("demo")
    assert {"pods/exec", "pods/attach", "pods/portforward"} <= subresources(app)
    assert not {"pods/exec", "pods/attach", "pods/portforward"} & subresources(responder_role("demo-sdo"))
    assert not any(resource["kind"] in {"ClusterRole", "ClusterRoleBinding"} for resource in resources)
    for resource in resources:
        if resource["kind"] != "Role" or "responder" not in resource["metadata"]["name"]:
            continue
        granted = {name for rule in resource["rules"] for name in rule["resources"]}
        assert "secrets" not in granted
        assert "roles" not in granted
        assert "rolebindings" not in granted


def test_split_namespace_controller_may_only_list_and_delete_pods_and_jobs_to_clean_responder_helpers() -> None:
    resources = controller_resources(_split_config())
    cleanup = next(
        resource
        for resource in resources
        if resource["kind"] == "Role" and resource["metadata"]["name"] == "sdo-controller-helper-cleanup"
    )
    binding = next(
        resource
        for resource in resources
        if resource["kind"] == "RoleBinding" and resource["metadata"]["name"] == "sdo-controller-helper-cleanup"
    )

    assert cleanup["metadata"]["namespace"] == "demo"
    assert cleanup["rules"] == [
        {"apiGroups": [""], "resources": ["pods"], "verbs": ["list", "delete"]},
        {"apiGroups": ["batch"], "resources": ["jobs"], "verbs": ["list", "delete"]},
    ]
    assert binding["subjects"] == [{"kind": "ServiceAccount", "name": "sdo-controller", "namespace": "demo-sdo"}]


def test_split_namespace_controller_observes_app_and_runs_jobs_in_its_own_namespace() -> None:
    args = _job(controller_resources(_split_config()))["spec"]["template"]["spec"]["containers"][0]["args"]

    assert args[args.index("--namespace") + 1] == "demo"
    assert args[args.index("--control-namespace") + 1] == "demo-sdo"
    assert args[args.index("--broker-arg=--validator-namespace") + 1] == "--broker-arg=demo-sdo"
    assert "--supervise" in args


def test_single_namespace_install_is_the_default() -> None:
    resources = controller_resources(_config())
    args = _job(resources)["spec"]["template"]["spec"]["containers"][0]["args"]

    assert not any(resource["kind"] == "Namespace" for resource in resources)
    assert all(resource["metadata"]["namespace"] == "demo" for resource in resources)
    assert "--control-namespace" not in args
    assert "--supervise" in args


@pytest.mark.parametrize("controller_namespace", ["Bad_Name", "", "x" * 64])
def test_invalid_controller_namespace_is_rejected(controller_namespace: str) -> None:
    with pytest.raises(ValueError, match="controller_namespace"):
        _split_config(controller_namespace=controller_namespace)


class _FakeKubectl:
    def __init__(self, existing_job: dict[str, Any] | None) -> None:
        self.existing_job = existing_job
        self.calls: list[tuple[list[str], str | None, str | None]] = []

    def __call__(
        self,
        args: list[str],
        *,
        namespace: str | None,
        input_text: str | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append((args, namespace, input_text))
        if args[:2] == ["get", "job/sdo-controller-run"]:
            if self.existing_job is None:
                return subprocess.CompletedProcess(args, 1, "", "NotFound")
            return subprocess.CompletedProcess(args, 0, json.dumps(self.existing_job), "")
        if args[:1] == ["get"] and args[1].startswith("pod/sdo-repository-sync"):
            ready = {"status": {"conditions": [{"type": "Ready", "status": "True"}]}}
            return subprocess.CompletedProcess(args, 0, json.dumps(ready), "")
        return subprocess.CompletedProcess(args, 0, "", "")

    def applied(self) -> list[dict[str, Any]]:
        documents: list[dict[str, Any]] = []
        for args, _namespace, input_text in self.calls:
            if args[:1] == ["apply"] and input_text:
                documents.extend(document for document in yaml.safe_load_all(input_text) if document)
        return documents


def _running_job(config: ControllerInstallConfig) -> dict[str, Any]:
    job = _job(controller_resources(config))
    return {**job, "status": {"active": 1}}


def test_reinstall_reuses_a_healthy_controller_with_the_same_install_fingerprint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _split_config(reuse_existing=True)
    fake = _FakeKubectl(_running_job(config))
    monkeypatch.setattr(kubernetes, "kubectl", fake)
    monkeypatch.setattr(kubernetes, "_copy_repository_to_pod", _unexpected_seed)

    result = install_controller(config)

    assert result.reused is True
    assert not any(args[:1] == ["delete"] and "job/sdo-controller-run" in args for args, _, _ in fake.calls)
    assert not any(args[:1] == ["exec"] for args, _, _ in fake.calls)
    # A recreated application namespace gets its access grants back.
    app_grants = [document for document in fake.applied() if document["metadata"].get("namespace") == "demo"]
    assert {document["kind"] for document in app_grants} == {"Role", "RoleBinding"}


@pytest.mark.parametrize(
    "stale_override",
    [{"responder_image": "responder:old"}, {"reflection_session": "resume"}],
    ids=["responder-image", "reflection-session"],
)
def test_reinstall_replaces_a_controller_whose_install_fingerprint_differs(
    monkeypatch: pytest.MonkeyPatch, stale_override: dict[str, str]
) -> None:
    config = _split_config(reuse_existing=True)
    stale = _running_job(_split_config(reuse_existing=True, **stale_override))
    fake = _FakeKubectl(stale)
    seeded: list[str] = []
    monkeypatch.setattr(kubernetes, "kubectl", fake)
    monkeypatch.setattr(kubernetes, "_copy_repository_to_pod", lambda cfg: seeded.append(cfg.control_namespace))

    result = install_controller(config)

    assert result.reused is False
    assert seeded == ["demo-sdo"]
    assert any(args[:1] == ["delete"] and "job/sdo-controller-run" in args for args, _, _ in fake.calls)


def test_reinstall_without_reuse_always_reseeds(monkeypatch: pytest.MonkeyPatch) -> None:
    config = _config()
    fake = _FakeKubectl(_running_job(config))
    seeded: list[str] = []
    monkeypatch.setattr(kubernetes, "kubectl", fake)
    monkeypatch.setattr(kubernetes, "_copy_repository_to_pod", lambda cfg: seeded.append(cfg.control_namespace))

    assert install_controller(config).reused is False
    assert seeded == ["demo"]


def test_set_controller_maintenance_declares_mode_and_generation(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeKubectl(None)
    monkeypatch.setattr(kubernetes, "kubectl", fake)

    set_controller_maintenance("demo-sdo", paused=True, generation="stage-1")

    (document,) = fake.applied()
    assert document["metadata"] == {"name": "sdo-controller-maintenance", "namespace": "demo-sdo"}
    assert document["data"] == {"state": "paused", "generation": "stage-1"}


def _unexpected_seed(config: ControllerInstallConfig) -> None:
    raise AssertionError("a reused controller must not be reseeded")


def _git_repository(root: Path) -> Path:
    root.mkdir()
    for args in (
        ["init", "-b", "main"],
        ["config", "user.name", "T"],
        ["config", "user.email", "t@example.com"],
    ):
        subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)
    (root / "k8s-geo-mongo.sh").write_text("#!/bin/sh\nmongo <<EOF   \n\nEOF\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "commit", "-m", "base"], check=True, capture_output=True)
    return root


def test_installed_source_repair_check_accepts_verbatim_whitespace_but_rejects_conflict_markers(
    tmp_path: Path,
) -> None:
    import shlex

    from sdo.operational_memory import MemoryValidationError
    from sdo.operational_memory.commit_broker import CommandProposalValidator

    broker = _broker_args(_config())
    command = shlex.split(broker[broker.index("--proposal-command") + 1])
    validator = CommandProposalValidator([command])
    repository = _git_repository(tmp_path / "app")

    # A verified repair that embeds an existing script verbatim keeps its trailing whitespace.
    configmap = repository / "kubernetes" / "geo" / "mongo-geo-script-configmap.yaml"
    configmap.parent.mkdir(parents=True)
    configmap.write_text("data:\n  k8s-geo-mongo.sh: |\n    mongo <<EOF   \n    \n    EOF\n\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repository), "add", "-A"], check=True, capture_output=True)
    validator.validate(repository, ["kubernetes/geo/mongo-geo-script-configmap.yaml"])

    configmap.write_text("<<<<<<< HEAD\na: 1\n=======\na: 2\n>>>>>>> repair\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repository), "add", "-A"], check=True, capture_output=True)
    with pytest.raises(MemoryValidationError, match="conflict marker"):
        validator.validate(repository, ["kubernetes/geo/mongo-geo-script-configmap.yaml"])


def test_local_controller_builder_uses_the_same_source_repair_check() -> None:
    from sdo.operational_memory import SOURCE_REPAIR_CHECK_COMMAND

    source = (Path(__file__).resolve().parents[4] / "controller" / "builder" / "check_cli.py").read_text(
        encoding="utf-8"
    )
    assert f'"{SOURCE_REPAIR_CHECK_COMMAND}"' in source


def test_reflection_guidance_defaults_to_baseline_and_is_passed_to_the_broker() -> None:
    default = _config()
    generalize = ControllerInstallConfig(**{**_config().__dict__, "reflection_guidance": "generalize"})

    assert default.reflection_guidance == "baseline"
    spec = ControllerInstallConfig(**{**_config().__dict__, "reflection_guidance": "generalize-spec"})
    for config, expected in ((default, "baseline"), (generalize, "generalize"), (spec, "generalize-spec")):
        broker = _broker_args(config)
        assert broker[broker.index("--reflection-guidance") + 1] == expected


def test_unknown_reflection_guidance_is_rejected() -> None:
    with pytest.raises(ValueError, match="reflection_guidance"):
        ControllerInstallConfig(**{**_config().__dict__, "reflection_guidance": "sibling"})


def _controller_args(config: ControllerInstallConfig) -> list[str]:
    controller = next(resource for resource in controller_resources(config) if resource["kind"] == "Job")
    return controller["spec"]["template"]["spec"]["containers"][0]["args"]


def test_late_findings_is_off_by_default_and_leaves_the_controller_args_unchanged() -> None:
    default = _config()
    off = ControllerInstallConfig(**{**_config().__dict__, "late_findings": "off"})

    assert default.late_findings == "off"
    assert _controller_args(off) == _controller_args(default)
    assert "--late-findings" not in _broker_args(default)
    assert not any(arg.startswith("--responder-env=SDO_LATE_FINDINGS") for arg in _controller_args(default))


def test_pull_mode_reaches_the_broker_and_the_responder_environment() -> None:
    pull = ControllerInstallConfig(**{**_config().__dict__, "late_findings": "pull"})

    broker = _broker_args(pull)
    assert broker[broker.index("--late-findings") + 1] == "pull"
    assert "--responder-env=SDO_LATE_FINDINGS=pull" in _controller_args(pull)
    # The responder appends its receipts at the volume root, not inside the application repository.
    log = broker[broker.index("--late-findings-log") + 1]
    assert log == "/workspace/.sdo-runtime/telemetry/late-findings-pulls.jsonl"


def test_unknown_late_findings_mode_is_rejected() -> None:
    with pytest.raises(ValueError, match="late_findings"):
        ControllerInstallConfig(**{**_config().__dict__, "late_findings": "push"})


def test_healthy_baseline_is_off_by_default_and_leaves_the_controller_args_unchanged() -> None:
    default = _config()
    off = ControllerInstallConfig(**{**_config().__dict__, "healthy_baseline": False})

    assert default.healthy_baseline is False
    assert _controller_args(off) == _controller_args(default)
    assert "--healthy-baseline-dir" not in _broker_args(default)
    assert "--healthy-baseline-source" not in _broker_args(default)


def test_healthy_baseline_reaches_the_broker_with_a_volume_source_and_a_worktree_relative_directory() -> None:
    gated = ControllerInstallConfig(**{**_config().__dict__, "healthy_baseline": True})

    broker = _broker_args(gated)
    # The snapshots live on the repository volume, outside the application repository ...
    assert broker[broker.index("--healthy-baseline-source") + 1] == "/workspace/.sdo-baseline/healthy"
    # ... and are staged into the validated worktree under this relative path only while validating.
    assert broker[broker.index("--healthy-baseline-dir") + 1] == ".sdo-baseline/healthy"


def _controller_job_args(config: ControllerInstallConfig) -> list[str]:
    return _controller_args(config)


def test_follow_ups_are_off_by_default_and_leave_the_controller_args_unchanged() -> None:
    default = _config()

    assert default.max_follow_ups == 0
    assert "--max-follow-ups" not in _controller_job_args(default)
    assert "--follow-up-cooldown" not in _controller_job_args(default)


def test_follow_ups_reach_the_controller_arguments() -> None:
    config = ControllerInstallConfig(**{**_config().__dict__, "max_follow_ups": 3, "follow_up_cooldown_seconds": 45})
    args = _controller_job_args(config)

    assert args[args.index("--max-follow-ups") + 1] == "3"
    assert args[args.index("--follow-up-cooldown") + 1] == "45s"


def test_closeout_state_gate_is_opt_in_and_reaches_the_controller() -> None:
    assert "--closeout-state-gate" not in _controller_job_args(_config())
    config = ControllerInstallConfig(**{**_config().__dict__, "closeout_state_gate": True})

    args = _controller_job_args(config)
    assert "--closeout-state-gate" in args
    assert "--responder-env=SDO_CLOSEOUT_STATE_GATE=1" in args
    assert not any("SDO_CLOSEOUT_STATE_GATE" in arg for arg in _controller_job_args(_config()))


@pytest.mark.parametrize("update", [{"max_follow_ups": -1}, {"follow_up_cooldown_seconds": -1}])
def test_invalid_follow_up_settings_are_rejected(update: dict[str, int]) -> None:
    with pytest.raises(ValueError, match="follow_up"):
        ControllerInstallConfig(**{**_config().__dict__, **update})


def test_controller_install_rejects_a_validator_on_another_tag_than_the_controller() -> None:
    with pytest.raises(ValueError, match="different tags"):
        ControllerInstallConfig(
            **{
                **_config().__dict__,
                "controller_image": "sdo-controller:mx1",
                "validator_image": "sdo-detector-validator:v0.1.0",
            }
        )


def test_controller_install_accepts_images_built_together() -> None:
    config = ControllerInstallConfig(
        **{
            **_config().__dict__,
            "controller_image": "sdo-controller:mx1",
            "validator_image": "sdo-detector-validator:mx1",
        }
    )

    assert config.validator_image == "sdo-detector-validator:mx1"
