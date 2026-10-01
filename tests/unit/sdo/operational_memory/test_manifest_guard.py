from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from sdo.operational_memory.manifest_guard import validate_yaml_manifests
from sdo.operational_memory.validation import MemoryValidationError

if TYPE_CHECKING:
    from pathlib import Path

_DEPLOYMENT = """apiVersion: apps/v1
kind: Deployment
metadata:
  name: web
spec:
  template:
    spec:
      containers:
        - name: web
          image: web:latest
          imagePullPolicy: Always
"""


def _write(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_accepts_valid_multi_document_manifest(tmp_path: Path) -> None:
    _write(tmp_path, "k8s/app.yaml", _DEPLOYMENT + "---\n" + _DEPLOYMENT)

    validate_yaml_manifests(tmp_path, ["k8s/app.yaml"])


def test_rejects_duplicate_mapping_key_with_actionable_message(tmp_path: Path) -> None:
    _write(tmp_path, "k8s/app.yml", _DEPLOYMENT + "          imagePullPolicy: IfNotPresent\n")

    with pytest.raises(MemoryValidationError) as excinfo:
        validate_yaml_manifests(tmp_path, ["k8s/app.yml"])

    message = str(excinfo.value)
    assert "k8s/app.yml" in message
    assert "imagePullPolicy" in message
    assert "duplicate" in message
    assert "line 12" in message


def test_rejects_duplicate_key_in_later_document(tmp_path: Path) -> None:
    _write(tmp_path, "a.yaml", _DEPLOYMENT + "---\nkind: A\nkind: B\n")

    with pytest.raises(MemoryValidationError, match="duplicate"):
        validate_yaml_manifests(tmp_path, ["a.yaml"])


def test_rejects_unparseable_yaml(tmp_path: Path) -> None:
    _write(tmp_path, "a.yaml", "kind: [unclosed\n")

    with pytest.raises(MemoryValidationError, match="a.yaml.*not valid YAML"):
        validate_yaml_manifests(tmp_path, ["a.yaml"])


def test_ignores_deleted_non_yaml_and_templated_files(tmp_path: Path) -> None:
    _write(tmp_path, "notes.txt", "a: 1\na: 2\n")
    _write(tmp_path, "chart/templates/d.yaml", "name: {{ .Values.name }}\nname: x\n")

    validate_yaml_manifests(tmp_path, ["gone.yaml", "notes.txt", "chart/templates/d.yaml"])


def test_does_not_reject_cluster_specific_image_tags(tmp_path: Path) -> None:
    _write(tmp_path, "d.yaml", _DEPLOYMENT.replace("web:latest", "registry.local/web:sha-1234"))

    validate_yaml_manifests(tmp_path, ["d.yaml"])
