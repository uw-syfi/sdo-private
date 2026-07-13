#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
docker build \
  --file "${repo_root}/observer/Dockerfile.validator" \
  --tag sdo-observer-validator:v0.1.0 \
  "${repo_root}"
docker build \
  --file "${repo_root}/observer/Dockerfile.runtime" \
  --target controller \
  --tag sdo-controller:v0.1.0 \
  "${repo_root}"
docker build \
  --file "${repo_root}/observer/Dockerfile.runtime" \
  --target responder \
  --tag sdo-responder:v0.1.0 \
  "${repo_root}"
