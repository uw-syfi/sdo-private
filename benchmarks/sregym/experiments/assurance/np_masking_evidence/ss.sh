#!/bin/bash
# ss inside the frontend / recommendation pod netns, via nsenter on the node
for svc in frontend recommendation; do
  cid=$(docker exec np-verify0-worker3 crictl ps --name "hotel-reserv-$svc" -q 2>/dev/null | head -1)
  pid=$(docker exec np-verify0-worker3 crictl inspect "$cid" 2>/dev/null | python3 -c "import sys,json;print(json.load(sys.stdin)[\"info\"][\"pid\"])")
  echo "-- $svc container=$cid pid=$pid"
  docker exec np-verify0-worker3 nsenter -t "$pid" -n ss -tnoi state established 2>&1 | grep -A1 -E "8085|27017" | head -12
done
