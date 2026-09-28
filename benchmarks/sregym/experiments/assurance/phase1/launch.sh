#!/usr/bin/env bash
# One-command launcher for the phase-1 live assurance matrix (PLAN.md (d), RUNBOOK.md).
#
# Thin wrapper: all logic lives in benchmarks.sregym.assurance.phase1_launch, so
# it is unit-tested (tests/unit/benchmarks/sregym/assurance/test_phase1_launch.py)
# without touching a real cluster, quota, or subprocess. This script only
# fixes the working directory and forwards arguments.
#
# Usage:
#   bash benchmarks/sregym/experiments/assurance/phase1/launch.sh [--dry-run] [options...]
#
# See RUNBOOK.md for preconditions, the exact commands, and abort criteria.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../../.." && pwd)"
cd "${repo_root}"
exec uv run python -m benchmarks.sregym.assurance.phase1_launch "$@"
