#!/bin/bash
# Static checks for the proto contracts:
#   buf lint       — style and consistency of the .proto source
#   buf breaking    — the schema stays wire-compatible with main
#   generated diff  — the committed Go/Python matches what buf generate emits
#
# The generated-diff check is the ratchet: a .proto edit that is not accompanied
# by its regenerated code (or a hand-edit of generated code) fails here, so drift
# is a build failure, not a 10-minutes-into-a-run failure.

set -euo pipefail

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

export PATH="${GOBIN:-${HOME}/go/bin}:${PATH}"

if ! command -v buf >/dev/null 2>&1; then
    echo "Error: 'buf' is not installed (go install github.com/bufbuild/buf/cmd/buf@latest)." >&2
    exit 1
fi

echo "==> buf lint"
buf lint

# Breaking-change detection against the default branch. Skipped when the branch
# point is unavailable (e.g. a shallow checkout without origin/main).
BREAKING_BASE="${BUF_BREAKING_AGAINST:-.git#branch=main}"
echo "==> buf breaking (against ${BREAKING_BASE})"
if ! buf breaking --against "${BREAKING_BASE}" 2>/tmp/buf_breaking.err; then
    if grep -qiE "could not|no such|unknown revision|does not exist|no .proto files|had no" /tmp/buf_breaking.err; then
        echo "   skipped: breaking-change base ${BREAKING_BASE} unavailable" >&2
        cat /tmp/buf_breaking.err >&2
    else
        cat /tmp/buf_breaking.err >&2
        exit 1
    fi
fi

echo "==> generated code is up to date"
"$SCRIPT_DIR/gen_proto.sh" >/dev/null
GEN_DIRS=(controller/contracts/gen sdo/contracts/_gen)
if ! git diff --quiet -- "${GEN_DIRS[@]}"; then
    echo "Generated proto code is out of date. Run scripts/gen_proto.sh and commit:" >&2
    git --no-pager diff --stat -- "${GEN_DIRS[@]}" >&2
    exit 1
fi

echo "Proto checks passed."
