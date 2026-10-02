#!/bin/bash
# usage: seq_mixed.sh <worker-id> <name> <image-tag> [problem ...]
# Warm-iteration run of the mixed stream through the fastloop (probe-graded, no LLM judge), Codex gpt-6-luna only.
# One persistent controller and `.sdo` per sequence; the app is redeployed before each incident.
#
# What it does differently from seq_mini.sh and confirm_single.sh (see docs/efficiency-decisions.md):
#  - cold lifecycles are cached across runs (~/.cache/sdo/lifecycle-seeds), so a retry or a second sequence restores
#    the ~11 min lifecycle instead of re-authoring it; COLD=1 measures a cold lifecycle instead
#  - `up` waits for a calm host load and records host-load.json; set NO_LOAD_GOVERNOR=1 to skip
#  - an incident nothing detects ends as `undetected` after DETECTION_TIMEOUT seconds (default 240) with the fault
#    recovered, instead of waiting out the whole per-incident timeout (observed: 8.5-11 min wasted each)
#  - no --inject-before-resume for single faults: it injects while the controller is paused, so the link probe has
#    no baseline and an isolating NetworkPolicy fault can never be detected
#  - SDO_VALIDATOR_IMAGE is left unset: the configured --validator-image reaches host-side validation
#  - watch a live sequence with: python -m benchmarks.sregym.fastloop.hostguard watch --path $RUN --interval 120
# SEED_REPO is a git repository with the application source and no .sdo (a cold seed).
WID=$1; NAME=$2; TAG=$3; shift 3
unset SDO_VALIDATOR_IMAGE
HERE=$(cd "$(dirname "$0")/../../../.." && pwd)
cd "$HERE" || exit 1
RUN=${RUN_ROOT:-/mnt/data/shli/clc-runs}/$NAME
mkdir -p "$RUN"
if [ $# -eq 0 ]; then
  set -- $(uv run --extra test python -c "from benchmarks.sregym.runner.incident_stream import mini_stream; print(*[i.problem_id for i in mini_stream()])")
fi
COMMON=(--run-dir "$RUN")
RUN_FLAGS=(--agent sdo --reflection-session fresh --reflection-guidance generalize --late-findings pull
  --max-follow-ups 3 --follow-up-cooldown-seconds 30 --healthy-baseline --closeout-state-gate
  --timeout 900 --detection-timeout "${DETECTION_TIMEOUT:-240}")
[ "${COLD:-0}" = 1 ] && RUN_FLAGS+=(--cold-lifecycle)
UP_FLAGS=()
[ "${NO_LOAD_GOVERNOR:-0}" = 1 ] && UP_FLAGS+=(--no-load-governor)
i=1
for P in "$@"; do
  echo "$(date -u +%FT%TZ) load=$(cut -d' ' -f1 /proc/loadavg) up before #$i $P" >> "$RUN/seq.log"
  SEEDARG=(); [ $i -eq 1 ] && SEEDARG=(--seed "${SEED_REPO:?SEED_REPO must name a cold source repository}")
  uv run --extra test python -m benchmarks.sregym.fastloop up "${COMMON[@]}" "${SEEDARG[@]}" "${UP_FLAGS[@]}" \
    --cluster-prefix mi-w --worker-id "$WID" --builder "mi$WID" --redeploy \
    --controller-image "sdo-controller:$TAG" --responder-image "sdo-sregym-responder:$TAG" \
    --validator-image "sdo-detector-validator:$TAG" > "$RUN/up-$i.log" 2>&1 || { echo "up failed #$i" >> "$RUN/seq.log"; exit 1; }
  echo "$(date -u +%FT%TZ) run #$i $P" >> "$RUN/seq.log"
  uv run --extra test python -m benchmarks.sregym.fastloop run "${COMMON[@]}" "${RUN_FLAGS[@]}" --problem "$P" -n 1 \
    --run-id "$NAME-$i" > "$RUN/run-$i.log" 2>&1
  echo "$(date -u +%FT%TZ) done #$i exit=$?" >> "$RUN/seq.log"
  i=$((i+1))
done
