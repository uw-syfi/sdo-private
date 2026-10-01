#!/bin/bash
# usage: replay_gate.sh <seq-name>...   Offline replay of the sequence's final learned detectors against the healthy snapshots
# (captured earlier in /mnt/data/shli/clc-runs/hb-live/healthy) with the gate's own entry point. Writes gate-<seq>.txt in the run dir.
cd /mnt/data/shli/sdo-worktrees/hb-ab
for N in "$@"; do
  RUN=/mnt/data/shli/clc-runs/$N; WS=$RUN/replay-ws; rm -rf $WS; mkdir -p $WS/.sdo-baseline
  git -C $RUN/application_workspace archive HEAD .sdo | tar -x -C $WS
  cp -r /mnt/data/shli/clc-runs/hb-live/healthy $WS/.sdo-baseline/healthy
  uv run --extra test python -m controller.builder.check_cli test --app $WS --healthy-baseline .sdo-baseline/healthy > $RUN/gate-replay.txt 2>&1
  echo "$N exit=$?"
done
