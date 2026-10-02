#!/bin/bash
# usage: confirm_single.sh <worker-id> <name> <problem>
# Phase A step 2: cold lifecycle with the validator-image fix (SDO_VALIDATOR_IMAGE deliberately unset; the
# configured --validator-image must be used), then one cold single-fault incident through the fastloop with the
# link probe and the close-out gate on. Codex gpt-6-luna only. Fault recovery runs at the end of `run`.
WID=$1; NAME=$2; P=$3
unset SDO_VALIDATOR_IMAGE
TAG=${TAG:-mi1}
cd /mnt/data/shli/sdo-worktrees/mixed-integration
RUN=/mnt/data/shli/clc-runs/$NAME
mkdir -p $RUN
echo "$(date -u +%FT%TZ) load=$(cut -d' ' -f1 /proc/loadavg) up" >> $RUN/seq.log
uv run --extra test python -m benchmarks.sregym.fastloop up --run-dir $RUN --cluster-prefix mi-w --worker-id $WID \
  --controller-image sdo-controller:$TAG --responder-image sdo-sregym-responder:$TAG --validator-image sdo-detector-validator:$TAG \
  --builder mi$WID --redeploy > $RUN/up.log 2>&1 || { echo "up failed" >> $RUN/seq.log; exit 1; }
echo "$(date -u +%FT%TZ) load=$(cut -d' ' -f1 /proc/loadavg) run $P" >> $RUN/seq.log
uv run --extra test python -m benchmarks.sregym.fastloop run --run-dir $RUN --agent sdo --problem $P -n 1 \
  --run-id $NAME --reflection-session fresh --reflection-guidance generalize --late-findings pull --max-follow-ups 3 \
  --follow-up-cooldown-seconds 30 --inject-before-resume --healthy-baseline --closeout-state-gate --timeout 900 > $RUN/run.log 2>&1
echo "$(date -u +%FT%TZ) load=$(cut -d' ' -f1 /proc/loadavg) done exit=$?" >> $RUN/seq.log
