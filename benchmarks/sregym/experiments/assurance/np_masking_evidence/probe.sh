#!/bin/sh
# usage: probe.sh NAME URL SECONDS ; appends "epoch,name,http,time,exit" per request, ~2 req/s
N=$1; U=$2; D=$3; END=$(( $(date +%s) + D ))
while [ $(date +%s) -lt $END ]; do
  T=$(date +%s.%N)
  R=$(curl -s -m 5 -o /dev/null -w '%{http_code},%{time_total}' "$U"); E=$?
  echo "$T,$N,$R,$E" >> /tmp/ts.csv
  sleep 0.45
done
