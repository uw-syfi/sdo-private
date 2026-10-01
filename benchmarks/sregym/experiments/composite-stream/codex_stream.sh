#!/bin/bash
# usage: codex_stream.sh <worker-id> <name>   Codex gpt-6-luna + verify protocol, n=2 on each stream-only composite (C4, C5).
cd /mnt/data/shli/sdo-worktrees/composite-stream
WID=$1; NAME=$2
RUN=/mnt/data/shli/clc-runs/$NAME
mkdir -p $RUN
uv run --extra test python -m benchmarks.sregym.fastloop up --run-dir $RUN --seed /mnt/data/shli/detgen-runs/seeds/lifecycle-stream --cluster-prefix cl-w --worker-id $WID \
  --controller-image sdo-controller:nf5 --responder-image sdo-sregym-responder:nf5 --validator-image sdo-detector-validator:nf5 --builder cs$WID --redeploy > $RUN/up.log 2>&1 || { echo "up failed" >> $RUN/arms.log; exit 1; }
VERIFY="$(uv run --extra test python -c 'from benchmarks.sregym.runner.codex_baseline import VERIFY_PROTOCOL_PROMPT as p; print(p)')"
export SREGYM_AGENT_PROMPT_APPENDIX="$VERIFY"
KC=$RUN/kubeconfigs/worker_$WID.kubeconfig
for spec in "verify-c4:composite3c_hotel_rate_mongodb_geo_user" "verify-c5:composite4_hotel_profile_rate_recommendation_frontend"; do
  id=${spec%%:*}; P=${spec#*:}
  for rep in 1 2; do
    # fresh app instance per run so a previous run's live/source edits cannot make an injection inert
    uv run --extra test python -m benchmarks.sregym.fastloop up --run-dir $RUN --cluster-prefix cl-w --worker-id $WID \
      --controller-image sdo-controller:nf5 --responder-image sdo-sregym-responder:nf5 --validator-image sdo-detector-validator:nf5 --builder cs$WID --redeploy > $RUN/up-$id-$rep.log 2>&1 || { echo "up failed $id-$rep" >> $RUN/arms.log; continue; }
    if kubectl --kubeconfig $KC -n hotel-reservation get deploy frontend -o jsonpath='{.spec.template.metadata.labels}' | grep -q current_service_name; then
      kubectl --kubeconfig $KC -n hotel-reservation patch deploy frontend --type=json -p '[{"op":"remove","path":"/spec/template/metadata/labels/current_service_name"}]' >> $RUN/arms.log 2>&1
      kubectl --kubeconfig $KC -n hotel-reservation rollout status deploy frontend --timeout=180s >> $RUN/arms.log 2>&1
    fi
    echo "$(date -u +%FT%TZ) start $id-$rep" >> $RUN/arms.log
    uv run --extra test python -m benchmarks.sregym.fastloop run --run-dir $RUN --agent codex --problem $P -n 1 --reasoning-effort medium --run-id $id-$rep > $RUN/$id-$rep.log 2>&1
    echo "$(date -u +%FT%TZ) done $id-$rep exit=$?" >> $RUN/arms.log
  done
done
