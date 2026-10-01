#!/bin/bash
# usage: seq_hb.sh <worker-id> <name> <gate: on|off> <late: off|pull> <followups> <problems...>   (seq_sim4.sh with images nf5 and the healthy-baseline gate on)
# One persistent controller / .sdo memory across the sequence; the app is redeployed before each composite.
WID=$1; NAME=$2; GATE=$3; LATE=$4; FU=$5; shift 5
HB=""; [ "$GATE" = on ] && HB="--healthy-baseline"
cd /mnt/data/shli/sdo-worktrees/hb-ab
RUN=/mnt/data/shli/clc-runs/$NAME
mkdir -p $RUN
i=1
for P in "$@"; do
  echo "$(date -u +%FT%TZ) up before C$i $P" >> $RUN/seq.log
  SEEDARG=""; [ $i -eq 1 ] && SEEDARG="--seed /mnt/data/shli/detgen-runs/seeds/lifecycle-stream"
  uv run --extra test python -m benchmarks.sregym.fastloop up --run-dir $RUN $SEEDARG --cluster-prefix cl-w --worker-id $WID \
    --controller-image sdo-controller:nf5 --responder-image sdo-sregym-responder:nf5 --validator-image sdo-detector-validator:nf5 \
    --builder sim$WID --redeploy > $RUN/up-C$i.log 2>&1 || { echo "up failed C$i" >> $RUN/seq.log; exit 1; }
  if [ $i -gt 1 ]; then  # fresh app instance: drop the pod label the previous responder added to neutralize wrong_selector (source stays as the agent left it)
    KC=$RUN/kubeconfigs/worker_$WID.kubeconfig
    if kubectl --kubeconfig $KC -n hotel-reservation get deploy frontend -o jsonpath='{.spec.template.metadata.labels}' | grep -q current_service_name; then
      kubectl --kubeconfig $KC -n hotel-reservation patch deploy frontend --type=json -p '[{"op":"remove","path":"/spec/template/metadata/labels/current_service_name"}]' >> $RUN/seq.log 2>&1
      kubectl --kubeconfig $KC -n hotel-reservation rollout status deploy frontend --timeout=180s >> $RUN/seq.log 2>&1
      echo "$(date -u +%FT%TZ) removed frontend pod label before C$i" >> $RUN/seq.log
    fi
  fi
  echo "$(date -u +%FT%TZ) run C$i $P" >> $RUN/seq.log
  uv run --extra test python -m benchmarks.sregym.fastloop run --run-dir $RUN --agent sdo --problem $P -n 1 \
    --run-id $NAME-C$i --reflection-session fresh --reflection-guidance generalize --late-findings $LATE --max-follow-ups $FU \
    --follow-up-cooldown-seconds 30 --inject-before-resume $HB > $RUN/run-C$i.log 2>&1
  echo "$(date -u +%FT%TZ) done C$i exit=$?" >> $RUN/seq.log
  i=$((i+1))
done
