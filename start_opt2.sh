#!/bin/bash
# Start opt2: train-ticket in training set
cd /mnt/nvme1/khoav/Research/sds
echo "Starting opt2 at $(date)"
uv run -m app_operator e2e-optimize --config experiment_opt2.toml --work-dir opt2 2>&1 | tee opt2_run.log
echo "opt2 completed at $(date)"
