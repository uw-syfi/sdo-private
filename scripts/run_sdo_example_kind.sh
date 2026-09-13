#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

export SDO_SMOKE_AGENT_PROVIDER="${SDO_SMOKE_AGENT_PROVIDER:-claude}"
export SDO_SMOKE_MODEL="${SDO_SMOKE_MODEL:-haiku}"
export SDO_SMOKE_REAL_LIFECYCLE="${SDO_SMOKE_REAL_LIFECYCLE:-0}"

bash "${repo_root}/scripts/build_sdo_images.sh"
exec bash "${repo_root}/scripts/smoke_sdo_runtime_kind.sh"
