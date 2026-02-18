#!/bin/bash
# Start opt3: train-ticket in validation set
cd /mnt/nvme1/khoav/Research/sds
echo "Starting opt3 at $(date)"
uv run -m app_operator e2e-optimize --config experiment_opt3.toml --work-dir opt3 2>&1 | tee opt3_run.log
echo "opt3 completed at $(date)"
