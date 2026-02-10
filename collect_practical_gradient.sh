#!/bin/bash
set -e

echo "==============================================="
echo "Practical Gradient Fault Injection Collection"
echo "==============================================="
echo ""
echo "Tier 2: 20 runs × 2 high-severity faults"
echo "Tier 3: 15 runs × 3 high-severity faults"
echo "Tier 4:  7 runs × 4 high-severity faults"
echo "Total: 42 runs per app = 126 total runs"
echo ""

# ============================================================================
# Tier 2: Challenging (2 high-severity faults)
# ============================================================================

echo "=== Tier 2: Challenging (2 high-severity faults) ==="
echo ""

echo "[1/3] Hotel Reservation - Tier 2"
scripts/collect_training_data.sh -f -n 20 hotel \
    --num-faults 2 \
    --fault-severities high \
    --fault-seed 30000

echo ""
echo "[2/3] Social Network - Tier 2"
scripts/collect_training_data.sh -f -n 20 social \
    --num-faults 2 \
    --fault-severities high \
    --fault-seed 30100

echo ""
echo "[3/3] Media Microservices - Tier 2"
scripts/collect_training_data.sh -f -n 20 media \
    --num-faults 2 \
    --fault-severities high \
    --fault-seed 30200

echo ""
echo "✓ Tier 2 complete (60 runs)"
echo ""

# ============================================================================
# Tier 3: Hard (3 high-severity faults)
# ============================================================================

echo "=== Tier 3: Hard (3 high-severity faults) ==="
echo ""

echo "[1/3] Hotel Reservation - Tier 3"
scripts/collect_training_data.sh -f -n 15 hotel \
    --num-faults 3 \
    --fault-severities high \
    --fault-seed 40000

echo ""
echo "[2/3] Social Network - Tier 3"
scripts/collect_training_data.sh -f -n 15 social \
    --num-faults 3 \
    --fault-severities high \
    --fault-seed 40100

echo ""
echo "[3/3] Media Microservices - Tier 3"
scripts/collect_training_data.sh -f -n 15 media \
    --num-faults 3 \
    --fault-severities high \
    --fault-seed 40200

echo ""
echo "✓ Tier 3 complete (45 runs)"
echo ""

# ============================================================================
# Tier 4: Extreme (4 high-severity faults)
# ============================================================================

echo "=== Tier 4: Extreme (4 high-severity faults) ==="
echo ""

echo "[1/3] Hotel Reservation - Tier 4"
scripts/collect_training_data.sh -f -n 7 hotel \
    --num-faults 4 \
    --fault-severities high \
    --fault-seed 50000

echo ""
echo "[2/3] Social Network - Tier 4"
scripts/collect_training_data.sh -f -n 7 social \
    --num-faults 4 \
    --fault-severities high \
    --fault-seed 50100

echo ""
echo "[3/3] Media Microservices - Tier 4"
scripts/collect_training_data.sh -f -n 7 media \
    --num-faults 4 \
    --fault-severities high \
    --fault-seed 50200

echo ""
echo "✓ Tier 4 complete (21 runs)"
echo ""

# ============================================================================
# Final Summary
# ============================================================================

echo "==============================================="
echo "Practical Gradient Collection Complete!"
echo "==============================================="
echo ""
echo "Total runs: 126 (42 per app)"
echo "  Hotel:  42 runs"
echo "  Social: 42 runs"
echo "  Media:  42 runs"
echo ""
echo "Breakdown by tier:"
echo "  Tier 2 (2 faults): 60 runs"
echo "  Tier 3 (3 faults): 45 runs"
echo "  Tier 4 (4 faults): 21 runs"
echo ""
echo "Next steps:"
echo "  1. Analyze results:"
echo "     uv run -m app_operator analyze-prompts --phase deployment \\"
echo "       --trajectories-dir /mnt/nvme1/khoav/Research/DeathStarBench/hotelReservation/.sds/trajectories \\"
echo "       --trajectories-dir /mnt/nvme1/khoav/Research/DeathStarBench/socialNetwork/.sds/trajectories \\"
echo "       --trajectories-dir /mnt/nvme1/khoav/Research/DeathStarBench/mediaMicroservices/.sds/trajectories"
echo ""
echo "  2. If overall success rate is 65-75%, proceed to DSPy optimization!"
echo ""
