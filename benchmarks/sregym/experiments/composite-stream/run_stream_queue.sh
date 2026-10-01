#!/bin/bash
# usage: run_stream_queue.sh "<kind>:<name>:<worker-id>" ...   kind = seq | codex. At most 2 jobs run at once; each waits (up to 30 min) for 1-minute load <= 20.
X=/mnt/data/shli/sdo-worktrees/composite-stream/benchmarks/sregym/experiments/composite-stream
# stream: C1 C2 C3 C1 C4 C2 C3 C5 C1 C4
C1=composite3_hotel_geo_rate_recommendation
C2=composite3b_hotel_profile_mongodb_geo_recommendation
C3=composite5_hotel_geo_rate_recommendation_frontend_user
C4=composite3c_hotel_rate_mongodb_geo_user
C5=composite4_hotel_profile_rate_recommendation_frontend
for J in "$@"; do
  IFS=: read K N W <<< "$J"
  until [ "$(pgrep -fc 'composite-stream/(seq_strea[m]|codex_strea[m]).sh')" -lt 2 ]; do sleep 20; done
  for t in $(seq 60); do L=$(cut -d' ' -f1 /proc/loadavg | cut -d. -f1); [ "$L" -le 20 ] && break; sleep 30; done
  echo "$(date -u +%FT%TZ) start $K $N worker=$W"
  if [ "$K" = seq ]; then $X/seq_stream.sh $W $N $C1 $C2 $C3 $C1 $C4 $C2 $C3 $C5 $C1 $C4 > /mnt/data/shli/clc-runs/$N.out 2>&1 &
  else $X/codex_stream.sh $W $N > /mnt/data/shli/clc-runs/$N.out 2>&1 & fi
  sleep 60
done
wait
