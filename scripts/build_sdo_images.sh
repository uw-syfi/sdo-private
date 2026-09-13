#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
docker build \
  --load \
  --file "${repo_root}/controller/Dockerfile.validator" \
  --tag sdo-detector-validator:v0.1.0 \
  "${repo_root}"
docker build \
  --load \
  --file "${repo_root}/controller/Dockerfile.runtime" \
  --target controller \
  --tag sdo-controller:v0.1.0 \
  "${repo_root}"
docker build \
  --load \
  --file "${repo_root}/controller/Dockerfile.runtime" \
  --target responder \
  --tag sdo-responder:v0.1.0 \
  "${repo_root}"
docker build \
  --load \
  --file "${repo_root}/controller/Dockerfile.runtime" \
  --target sregym-responder \
  --tag sdo-sregym-responder:v0.1.0 \
  "${repo_root}"
docker run --rm --user 65532:65532 \
  sdo-sregym-responder:v0.1.0 \
  python3 -m benchmarks.sregym.adapter.submission --help >/dev/null
docker run --rm --user 65532:65532 \
  sdo-sregym-responder:v0.1.0 \
  python3 -c "from sdo.agent_runtime.responder.codex import execute_incident" >/dev/null
