#!/bin/bash
# usage: seq.sh <worker-id> <name> <late: off|pull> <followups> <problems...>
# One persistent controller / .sdo memory across the sequence; the app is redeployed before each composite.
WID=$1; NAME=$2; LATE=$3; FU=$4; shift 4
cd /mnt/data/shli/sdo-worktrees/simul2
RUN=/mnt/data/shli/clc-runs/$NAME
mkdir -p $RUN
i=1
for P in "$@"; do
  echo "$(date -u +%FT%TZ) up before C$i $P" >> $RUN/seq.log
  SEEDARG=""; [ $i -eq 1 ] && SEEDARG="--seed /mnt/data/shli/detgen-runs/seeds/lifecycle-stream"
  uv run --extra test python -m benchmarks.sregym.fastloop up --run-dir $RUN $SEEDARG --cluster-prefix cl-w --worker-id $WID \
    --controller-image sdo-controller:nf4 --responder-image sdo-sregym-responder:nf4 --validator-image sdo-detector-validator:nf4 \
    --builder sim$WID --redeploy > $RUN/up-C$i.log 2>&1 || { echo "up failed C$i" >> $RUN/seq.log; exit 1; }
  echo "$(date -u +%FT%TZ) run C$i $P" >> $RUN/seq.log
  uv run --extra test python -m benchmarks.sregym.fastloop run --run-dir $RUN --agent sdo --problem $P -n 1 \
    --run-id $NAME-C$i --reflection-session fresh --reflection-guidance generalize --late-findings $LATE --max-follow-ups $FU \
    --follow-up-cooldown-seconds 30 --inject-before-resume > $RUN/run-C$i.log 2>&1
  echo "$(date -u +%FT%TZ) done C$i exit=$?" >> $RUN/seq.log
  i=$((i+1))
done
