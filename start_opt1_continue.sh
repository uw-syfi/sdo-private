#!/bin/bash
# Continue opt1: 2 more iterations
cd /mnt/nvme1/khoav/Research/sds
echo "Continuing opt1 at $(date)"
uv run -m app_operator e2e-optimize --config experiment_opt1_continue.toml --work-dir opt1 2>&1 | tee opt1_continue_run.log
echo "opt1 continuation completed at $(date)"
