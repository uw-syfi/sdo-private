#!/bin/bash
# Stress-test SREGym's deploy/inject/verify/recover pipeline at N-worker
# parallelism, without involving an agent. Useful for catching flakes in
# the fault-injection path itself.
#
# Usage:
#   scripts/run_sregym_stress.sh --parallel N [--problems id1,id2 | --all | --tasklist path]
#                                [--filter substr] [--max-per-worker M]
#                                [--stop-on-first-failure]
#                                [--no-loadgen-probe] [--probe-duration-sec S]
#                                [--agent-verify] [--agent-verify-model M]
#                                [--agent-verify-timeout-sec S]
#                                [--with-k8s-proxy]
#                                [--out-dir DIR]
#
# Reports land in bench/sregym/logs/stress/<timestamp>/ by default. When
# --agent-verify is set, per-problem verifier outputs also land under
# <out-dir>/agent_verify/.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Preserve the user's invocation cwd so stress_test.py can resolve relative
# path arguments (e.g. --tasklist, --out-dir) correctly after the cd below.
export SREGYM_STRESS_INVOCATION_CWD="$PWD"
cd "$SCRIPT_DIR/../bench/sregym"

exec uv run python stress_test.py "$@"
