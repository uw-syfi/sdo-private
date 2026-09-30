#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# SDO_IMAGE_TAG builds under a private tag so shared v0.1.0 images stay untouched.
tag="${SDO_IMAGE_TAG:-v0.1.0}"
docker build \
  --load \
  --file "${repo_root}/controller/Dockerfile.validator" \
  --tag sdo-detector-validator:${tag} \
  "${repo_root}"
docker build \
  --load \
  --file "${repo_root}/controller/Dockerfile.runtime" \
  --target controller \
  --tag sdo-controller:${tag} \
  "${repo_root}"
docker build \
  --load \
  --file "${repo_root}/controller/Dockerfile.runtime" \
  --target responder \
  --tag sdo-responder:${tag} \
  "${repo_root}"
docker build \
  --load \
  --file "${repo_root}/controller/Dockerfile.runtime" \
  --target sregym-responder \
  --tag sdo-sregym-responder:${tag} \
  "${repo_root}"
docker run --rm --user 65532:65532 \
  sdo-sregym-responder:${tag} \
  python3 -m benchmarks.sregym.adapter.submission --help >/dev/null
docker run --rm --user 65532:65532 \
  sdo-sregym-responder:${tag} \
  python3 -c "from sdo.agent_runtime.responder.codex import execute_incident" >/dev/null
