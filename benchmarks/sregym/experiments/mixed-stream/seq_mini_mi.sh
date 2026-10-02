#!/bin/bash
# usage: seq_mini_mi.sh <worker-id> <name> [problem ...]   (default: the mini stream from benchmarks.sregym.runner.incident_stream)
# Phase A step 3: the mixed mini stream on the integration build (images mi3). Differences from seq_mini.sh:
#  - no --inject-before-resume: every fault goes through the baseline + prober-warm gate, so the link probe can qualify an
#    edge before an isolating NetworkPolicy lands;
#  - the close-out state gate is on; per-incident cap (--timeout 900, composites --composite-deadline 900) and
#    --detection-timeout 120, and `run` always recovers the fault before the next incident (never kill without recovery);
#  - seed = a lifecycle-only workspace (no incident memory) from the mi2 cold lifecycle; SDO_VALIDATOR_IMAGE stays unset.
WID=$1; NAME=$2; shift 2
unset SDO_VALIDATOR_IMAGE
TAG=${TAG:-mi3}
HERE=/mnt/data/shli/sdo-worktrees/mixed-integration
cd $HERE
if [ $# -eq 0 ]; then
  set -- $(uv run --extra test python -c "from benchmarks.sregym.runner.incident_stream import mini_stream; print(*[i.problem_id for i in mini_stream()])")
fi
RUN=/mnt/data/shli/clc-runs/$NAME
mkdir -p $RUN
i=1
for P in "$@"; do
  echo "$(date -u +%FT%TZ) load=$(cut -d' ' -f1 /proc/loadavg) up before #$i $P" >> $RUN/seq.log
  SEEDARG=""; [ $i -eq 1 ] && SEEDARG="--seed ${SEED_REPO:-/mnt/data/shli/clc-runs/seeds/mi-lifecycle}"
  uv run --extra test python -m benchmarks.sregym.fastloop up --run-dir $RUN $SEEDARG --cluster-prefix mini-w --worker-id $WID \
    --controller-image sdo-controller:$TAG --responder-image sdo-sregym-responder:$TAG --validator-image sdo-detector-validator:$TAG \
    --builder mini$WID --redeploy > $RUN/up-$i.log 2>&1 || { echo "up failed #$i" >> $RUN/seq.log; exit 1; }
  echo "$(date -u +%FT%TZ) load=$(cut -d' ' -f1 /proc/loadavg) run #$i $P" >> $RUN/seq.log
  uv run --extra test python -m benchmarks.sregym.fastloop run --run-dir $RUN --agent sdo --problem $P -n 1 \
    --run-id $NAME-$i --reflection-session fresh --reflection-guidance generalize --late-findings pull --max-follow-ups 3 \
    --follow-up-cooldown-seconds 30 --healthy-baseline --closeout-state-gate --timeout 900 --composite-deadline 900 \
    --detection-timeout 120 > $RUN/run-$i.log 2>&1
  echo "$(date -u +%FT%TZ) load=$(cut -d' ' -f1 /proc/loadavg) done #$i exit=$?" >> $RUN/seq.log
  i=$((i+1))
done
