#!/bin/bash
set -e

# Configuration
APP_NAME="hotel"
APP_PATH="/mnt/nvme1/khoav/Research/DeathStarBench/hotelReservation"
STATE_FILE="$APP_PATH/.sds/gradient_state.txt"

# Initialize state tracking
if [ ! -f "$STATE_FILE" ]; then
    echo "tier2_completed=0" > "$STATE_FILE"
    echo "tier3_completed=0" >> "$STATE_FILE"
    echo "tier4_completed=0" >> "$STATE_FILE"
fi

# Load current state
source "$STATE_FILE"

# Tier configurations
TIER2_TOTAL=20
TIER2_BASE_SEED=30000
TIER3_TOTAL=15
TIER3_BASE_SEED=40000
TIER4_TOTAL=7
TIER4_BASE_SEED=50000

echo "==============================================="
echo "Hotel Reservation - Gradient Collection"
echo "==============================================="
echo ""
echo "Tier 2: 20 runs × 2 high-severity faults"
echo "Tier 3: 15 runs × 3 high-severity faults"
echo "Tier 4:  7 runs × 4 high-severity faults"
echo "Total: 42 runs"
echo ""
echo "Resume state:"
echo "  Tier 2: $tier2_completed/$TIER2_TOTAL completed"
echo "  Tier 3: $tier3_completed/$TIER3_TOTAL completed"
echo "  Tier 4: $tier4_completed/$TIER4_TOTAL completed"
echo ""

# ============================================================================
# Tier 2: Challenging (2 high-severity faults)
# ============================================================================

TIER2_REMAINING=$((TIER2_TOTAL - tier2_completed))

if [ $TIER2_REMAINING -gt 0 ]; then
    echo "=== Tier 2: Challenging (2 high-severity faults) ==="
    echo "Resuming from run $((tier2_completed + 1))/$TIER2_TOTAL (${TIER2_REMAINING} remaining)"
    echo ""

    TIER2_RESUME_SEED=$((TIER2_BASE_SEED + tier2_completed))

    scripts/collect_training_data.sh -f -n $TIER2_REMAINING $APP_NAME \
        --num-faults 2 \
        --fault-severities high \
        --fault-seed $TIER2_RESUME_SEED

    # Update state
    sed -i "s/tier2_completed=.*/tier2_completed=$TIER2_TOTAL/" "$STATE_FILE"

    echo ""
    echo "✓ Tier 2 complete ($TIER2_TOTAL runs)"
    echo ""
else
    echo "=== Tier 2: Already Complete ==="
    echo "Skipping $TIER2_TOTAL runs (already done)"
    echo ""
fi

# Reload state in case of interruption
source "$STATE_FILE"

# ============================================================================
# Tier 3: Hard (3 high-severity faults)
# ============================================================================

TIER3_REMAINING=$((TIER3_TOTAL - tier3_completed))

if [ $TIER3_REMAINING -gt 0 ]; then
    echo "=== Tier 3: Hard (3 high-severity faults) ==="
    echo "Resuming from run $((tier3_completed + 1))/$TIER3_TOTAL (${TIER3_REMAINING} remaining)"
    echo ""

    TIER3_RESUME_SEED=$((TIER3_BASE_SEED + tier3_completed))

    scripts/collect_training_data.sh -f -n $TIER3_REMAINING $APP_NAME \
        --num-faults 3 \
        --fault-severities high \
        --fault-seed $TIER3_RESUME_SEED

    # Update state
    sed -i "s/tier3_completed=.*/tier3_completed=$TIER3_TOTAL/" "$STATE_FILE"

    echo ""
    echo "✓ Tier 3 complete ($TIER3_TOTAL runs)"
    echo ""
else
    echo "=== Tier 3: Already Complete ==="
    echo "Skipping $TIER3_TOTAL runs (already done)"
    echo ""
fi

# Reload state
source "$STATE_FILE"

# ============================================================================
# Tier 4: Extreme (4 high-severity faults)
# ============================================================================

TIER4_REMAINING=$((TIER4_TOTAL - tier4_completed))

if [ $TIER4_REMAINING -gt 0 ]; then
    echo "=== Tier 4: Extreme (4 high-severity faults) ==="
    echo "Resuming from run $((tier4_completed + 1))/$TIER4_TOTAL (${TIER4_REMAINING} remaining)"
    echo ""

    TIER4_RESUME_SEED=$((TIER4_BASE_SEED + tier4_completed))

    scripts/collect_training_data.sh -f -n $TIER4_REMAINING $APP_NAME \
        --num-faults 4 \
        --fault-severities high \
        --fault-seed $TIER4_RESUME_SEED

    # Update state
    sed -i "s/tier4_completed=.*/tier4_completed=$TIER4_TOTAL/" "$STATE_FILE"

    echo ""
    echo "✓ Tier 4 complete ($TIER4_TOTAL runs)"
    echo ""
else
    echo "=== Tier 4: Already Complete ==="
    echo "Skipping $TIER4_TOTAL runs (already done)"
    echo ""
fi

# ============================================================================
# Final Summary
# ============================================================================

echo "==============================================="
echo "Hotel Reservation - Collection Complete!"
echo "==============================================="
echo ""
echo "Total runs: 42"
echo ""
echo "Breakdown by tier:"
echo "  Tier 2 (2 faults): 20 runs ✓"
echo "  Tier 3 (3 faults): 15 runs ✓"
echo "  Tier 4 (4 faults):  7 runs ✓"
echo ""
echo "Hotel trajectories:"
ls -1 "$APP_PATH/.sds/trajectories"/*.json 2>/dev/null | wc -l
echo ""
echo "State file: $STATE_FILE"
echo "To reset progress: rm $STATE_FILE"
echo ""
