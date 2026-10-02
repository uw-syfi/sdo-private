#!/bin/bash
# usage: seq_mini.sh <worker-id> <name> [problem ...]   (default: the mini stream from benchmarks.sregym.runner.incident_stream)
# Tier 1 fastloop run of the mixed stream: all fixes on (pull late findings, 3 follow-ups, inject-before-resume,
# healthy-baseline gate, generalize/fresh reflection), Codex gpt-6-luna, probe-graded, no LLM judge.
# One persistent controller and `.sdo` per sequence; the app is redeployed before each incident.
# Same flags as composite-stream/seq_stream.sh; images mini1 are a local retag of main's mx1 build.
WID=$1; NAME=$2; shift 2
# Host-side lifecycle validation reads SDO_VALIDATOR_IMAGE (default sdo-detector-validator:v0.1.0, which predates the
# link-probe SDK); without it the health judge compiles against a stale SDK and the cold lifecycle fails.
export SDO_VALIDATOR_IMAGE=sdo-detector-validator:mini1
HERE=/mnt/data/shli/sdo-worktrees/mixed-stream
cd $HERE
if [ $# -eq 0 ]; then
  set -- $(uv run --extra test python -c "from benchmarks.sregym.runner.incident_stream import mini_stream; print(*[i.problem_id for i in mini_stream()])")
fi
RUN=/mnt/data/shli/clc-runs/$NAME
mkdir -p $RUN
i=1
for P in "$@"; do
  echo "$(date -u +%FT%TZ) load=$(cut -d' ' -f1 /proc/loadavg) up before #$i $P" >> $RUN/seq.log
  SEEDARG=""; [ $i -eq 1 ] && SEEDARG="--seed /mnt/data/shli/detgen-runs/seeds/lifecycle-stream"
  uv run --extra test python -m benchmarks.sregym.fastloop up --run-dir $RUN $SEEDARG --cluster-prefix mini-w --worker-id $WID \
    --controller-image sdo-controller:mini1 --responder-image sdo-sregym-responder:mini1 --validator-image sdo-detector-validator:mini1 \
    --builder mini$WID --redeploy > $RUN/up-$i.log 2>&1 || { echo "up failed #$i" >> $RUN/seq.log; exit 1; }
  echo "$(date -u +%FT%TZ) run #$i $P" >> $RUN/seq.log
  uv run --extra test python -m benchmarks.sregym.fastloop run --run-dir $RUN --agent sdo --problem $P -n 1 \
    --run-id $NAME-$i --reflection-session fresh --reflection-guidance generalize --late-findings pull --max-follow-ups 3 \
    --follow-up-cooldown-seconds 30 --inject-before-resume --healthy-baseline > $RUN/run-$i.log 2>&1
  echo "$(date -u +%FT%TZ) done #$i exit=$?" >> $RUN/seq.log
  i=$((i+1))
done
