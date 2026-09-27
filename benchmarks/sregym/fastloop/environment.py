"""The warm environment a fast loop runs against, persisted by ``fastloop up``."""

from __future__ import annotations

import json
from pathlib import Path  # noqa: TC003 - Pydantic resolves this annotation at runtime.

from pydantic import BaseModel, ConfigDict, Field

ENVIRONMENT_FILENAME = "fastloop.json"
DEFAULT_PROBLEM = "missing_configmap_hotel_reservation"


class Images(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    controller: str = "sdo-controller:fastloop"
    responder: str = "sdo-sregym-responder:fastloop"
    validator: str = "sdo-detector-validator:fastloop"


class FastloopEnvironment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_dir: Path
    cluster: str
    kubeconfig: Path
    namespace: str
    application: str
    description: str = ""
    workspace: Path
    sregym_dir: Path
    #: Private ``/tmp`` for the SREGym worker; ``None`` runs it unsandboxed.
    private_tmp: Path | None
    deploy_problem: str = DEFAULT_PROBLEM
    images: Images = Field(default_factory=Images)
    #: Environment for the SREGym worker process (deploy-from-source, kind, builder settings).
    worker_env: dict[str, str] = Field(default_factory=dict)

    @property
    def path(self) -> Path:
        return self.run_dir / ENVIRONMENT_FILENAME

    @property
    def state_path(self) -> Path:
        """The persistent controller registry; it outlives single ``run`` invocations."""

        return self.run_dir / "sdo_persistent_controller.json"

    @property
    def baseline_path(self) -> Path:
        return self.run_dir / "cluster_baseline_state.json"

    def save(self) -> Path:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(self.model_dump_json(indent=2) + "\n", encoding="utf-8")
        temporary.replace(self.path)
        return self.path

    @classmethod
    def load(cls, run_dir: Path) -> FastloopEnvironment:
        path = run_dir / ENVIRONMENT_FILENAME
        if not path.is_file():
            raise FileNotFoundError(f"{path} does not exist; run `fastloop up --run-dir {run_dir}` first")
        return cls.model_validate(json.loads(path.read_text(encoding="utf-8")))

    def worker_process_env(self) -> dict[str, str]:
        return {**self.worker_env, "KUBECONFIG": str(self.kubeconfig), "PYTHONUNBUFFERED": "1"}
