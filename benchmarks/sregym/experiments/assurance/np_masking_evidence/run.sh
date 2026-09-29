#!/bin/bash
export KUBECONFIG=/mnt/data/shli/assurance-runs/np-verify/kubeconfigs/worker_0.kubeconfig
K="kubectl -n hotel-reservation"
OUT=/tmp/claude-1000/np/out; mkdir -p $OUT
REC='http://frontend:5000/recommendations?require=dis&lat=37.7749&lon=-122.4194'
CTL='http://frontend:5000/hotels?inDate=2015-04-09&outDate=2015-04-10&lat=37.7749&lon=-122.4194'
RC=192.168.59.138; FE=192.168.59.135
snap(){ # label
  echo "## $1 $(date +%s.%N)" >> $OUT/conntrack.txt
  docker exec np-verify0-worker3 conntrack -L 2>/dev/null | grep -E "src=$FE .*dst=$RC .*dport=8085|src=$RC .*dport=27017" >> $OUT/conntrack.txt
  }
TOTAL=$((60+600+30))
$K exec helper -- sh -c "rm -f /tmp/ts.csv"
$K exec helper -- sh -c "nohup /tmp/probe.sh rec '$REC' $TOTAL >/dev/null 2>&1 &"
$K exec helper -- sh -c "nohup /tmp/probe.sh ctl '$CTL' $TOTAL >/dev/null 2>&1 &"
echo "baseline_start $(date +%s.%N)" > $OUT/markers.txt
snap baseline_0s
sleep 55; snap baseline_55s; sleep 5
echo "fault_applied $(date +%s.%N)" >> $OUT/markers.txt
$K apply -f /tmp/claude-1000/np/netpol.yaml >> $OUT/markers.txt
snap fault_0s
for i in $(seq 1 20); do sleep 30; snap fault_$((i*30))s; done
echo "fault_window_end $(date +%s.%N)" >> $OUT/markers.txt
echo DONE >> $OUT/markers.txt
