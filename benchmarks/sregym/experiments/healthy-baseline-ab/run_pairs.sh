#!/bin/bash
# usage: run_pairs.sh <first-worker-id> <letters...>  e.g. run_pairs.sh 130 a b c d e
# For each letter runs abon-<l> (gate on) and aboff-<l> (gate off) concurrently (2 sequences at a time), pairs back to back.
# Waits (up to 30 min) while the 1-minute load average is above 20.
W=$1; shift
S=/mnt/data/shli/sdo-worktrees/hb-ab/benchmarks/sregym/experiments/healthy-baseline-ab/seq_ab.sh
P="composite5_hotel_geo_rate_recommendation_frontend_user composite5_hotel_geo_rate_recommendation_frontend_user composite3_hotel_geo_rate_recommendation"
for L in "$@"; do
  for t in $(seq 60); do
    LOAD=$(cut -d' ' -f1 /proc/loadavg | cut -d. -f1); [ "$LOAD" -le 20 ] && break
    echo "$(date -u +%FT%TZ) load $LOAD > 20, waiting before pair $L"; sleep 30
  done
  echo "$(date -u +%FT%TZ) pair $L start (workers $W,$((W+1)))"
  $S $W abon-$L on pull 3 $P > /mnt/data/shli/clc-runs/abon-$L.out 2>&1 &
  $S $((W+1)) aboff-$L off pull 3 $P > /mnt/data/shli/clc-runs/aboff-$L.out 2>&1 &
  wait
  echo "$(date -u +%FT%TZ) pair $L done"
  W=$((W+2))
done
