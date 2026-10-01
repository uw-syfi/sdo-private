#!/bin/bash
# usage: run_queue.sh "<name>:<on|off>:<worker-id>" ...   Starts each sequence as soon as fewer than 2 sequences are running
# and the 1-minute load is <= 20 (waits up to 30 min per job for the load to drop, then starts anyway).
S=/mnt/data/shli/sdo-worktrees/hb-ab/benchmarks/sregym/experiments/healthy-baseline-ab/seq_ab.sh
P="composite5_hotel_geo_rate_recommendation_frontend_user composite5_hotel_geo_rate_recommendation_frontend_user composite3_hotel_geo_rate_recommendation"
for J in "$@"; do
  IFS=: read N G W <<< "$J"
  until [ "$(pgrep -fc 'healthy-baseline-ab/seq_a[b].sh')" -lt 2 ]; do sleep 20; done
  for t in $(seq 60); do L=$(cut -d' ' -f1 /proc/loadavg | cut -d. -f1); [ "$L" -le 20 ] && break; sleep 30; done
  echo "$(date -u +%FT%TZ) start $N gate=$G worker=$W"
  $S $W $N $G pull 3 $P > /mnt/data/shli/clc-runs/$N.out 2>&1 &
  sleep 60
done
wait
