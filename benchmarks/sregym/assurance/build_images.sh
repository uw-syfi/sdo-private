#!/usr/bin/env bash
# Build the scripted-responder test images from SDO images.
#
#   bash benchmarks/sregym/assurance/build_images.sh [BASE_TAG] [TAG]
#
# BASE_TAG (default v0.1.0) names the SDO images to wrap; TAG (default assure)
# names the results. The base images are pinned under an `<image>:<TAG>-base`
# tag first, so a later rebuild of the shared tags cannot change a run midway.
# The validator runs no model and is only re-tagged.
set -euo pipefail

base_tag="${1:-v0.1.0}"
tag="${2:-assure}"
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"

docker tag "sdo-detector-validator:${base_tag}" "sdo-detector-validator:${tag}"
for image in sdo-controller sdo-sregym-responder; do
  docker tag "${image}:${base_tag}" "${image}:${tag}-base"
  docker build --quiet \
    --file "${repo_root}/benchmarks/sregym/assurance/Dockerfile.scripted" \
    --build-arg "BASE=${image}:${tag}-base" \
    --tag "${image}:${tag}" \
    "${repo_root}"
done
# The scripted CLI answers agentshim's probe, and no real model CLI is left.
docker run --rm --user 65532:65532 "sdo-sregym-responder:${tag}" codex --help >/dev/null
docker run --rm --user 65532:65532 "sdo-controller:${tag}" sh -c '! command -v claude && codex --version'
