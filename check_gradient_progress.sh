#!/bin/bash

echo "==============================================="
echo "Gradient Collection Progress"
echo "==============================================="
echo ""

HOTEL_DIR="/mnt/nvme1/khoav/Research/DeathStarBench/hotelReservation/.sds/trajectories"
SOCIAL_DIR="/mnt/nvme1/khoav/Research/DeathStarBench/socialNetwork/.sds/trajectories"
MEDIA_DIR="/mnt/nvme1/khoav/Research/DeathStarBench/mediaMicroservices/.sds/trajectories"

HOTEL_STATE="/mnt/nvme1/khoav/Research/DeathStarBench/hotelReservation/.sds/gradient_state.txt"
SOCIAL_STATE="/mnt/nvme1/khoav/Research/DeathStarBench/socialNetwork/.sds/gradient_state.txt"
MEDIA_STATE="/mnt/nvme1/khoav/Research/DeathStarBench/mediaMicroservices/.sds/gradient_state.txt"

hotel_count=$(ls -1 "$HOTEL_DIR"/*.json 2>/dev/null | wc -l)
social_count=$(ls -1 "$SOCIAL_DIR"/*.json 2>/dev/null | wc -l)
media_count=$(ls -1 "$MEDIA_DIR"/*.json 2>/dev/null | wc -l)
total=$((hotel_count + social_count + media_count))

echo "Current trajectory counts:"
echo "  Hotel:  $hotel_count (target: ~105)"
echo "  Social: $social_count (target: ~87)"
echo "  Media:  $media_count (target: ~80)"
echo ""
echo "Total:    $total (target: ~272)"
echo ""

# Calculate progress percentage
hotel_progress=$((hotel_count * 100 / 105))
social_progress=$((social_count * 100 / 87))
media_progress=$((media_count * 100 / 80))

echo "Progress:"
printf "  Hotel:  [%-50s] %d%%\n" "$(printf '#%.0s' $(seq 1 $((hotel_progress/2))))" "$hotel_progress"
printf "  Social: [%-50s] %d%%\n" "$(printf '#%.0s' $(seq 1 $((social_progress/2))))" "$social_progress"
printf "  Media:  [%-50s] %d%%\n" "$(printf '#%.0s' $(seq 1 $((media_progress/2))))" "$media_progress"
echo ""

# Show resume state
echo "Resume state (gradient collection):"
echo ""

if [ -f "$HOTEL_STATE" ]; then
    source "$HOTEL_STATE"
    hotel_total=$((tier2_completed + tier3_completed + tier4_completed))
    echo "  Hotel:  Tier2=$tier2_completed/20  Tier3=$tier3_completed/15  Tier4=$tier4_completed/7  (Total: $hotel_total/42)"
else
    echo "  Hotel:  Not started"
fi

if [ -f "$SOCIAL_STATE" ]; then
    source "$SOCIAL_STATE"
    social_total=$((tier2_completed + tier3_completed + tier4_completed))
    echo "  Social: Tier2=$tier2_completed/20  Tier3=$tier3_completed/15  Tier4=$tier4_completed/7  (Total: $social_total/42)"
else
    echo "  Social: Not started"
fi

if [ -f "$MEDIA_STATE" ]; then
    source "$MEDIA_STATE"
    media_total=$((tier2_completed + tier3_completed + tier4_completed))
    echo "  Media:  Tier2=$tier2_completed/20  Tier3=$tier3_completed/15  Tier4=$tier4_completed/7  (Total: $media_total/42)"
else
    echo "  Media:  Not started"
fi

echo ""
echo "Tip: To reset progress for an app, delete its state file:"
echo "  rm $HOTEL_STATE"
echo "  rm $SOCIAL_STATE"
echo "  rm $MEDIA_STATE"
echo ""
