#!/bin/bash
# Collect rich training data for GEPA optimization
# Uses controlled cleanup with docker compose down only

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

print_header() {
    echo -e "\n${BLUE}========================================${NC}"
    echo -e "${BLUE}$1${NC}"
    echo -e "${BLUE}========================================${NC}\n"
}

print_info() {
    echo -e "${YELLOW}[INFO]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Show usage information
show_usage() {
    echo "Usage: $0 [OPTIONS] [APP_NAMES...]"
    echo ""
    echo "Collect training data for GEPA optimization by running the SDS operator"
    echo "on DeathStarBench applications multiple times."
    echo ""
    echo "APP_NAMES (optional):"
    echo "  hotel  - Hotel Reservation"
    echo "  social - Social Network"
    echo "  media  - Media Microservices"
    echo ""
    echo "If no apps are specified, all three will be run."
    echo ""
    echo "OPTIONS:"
    echo "  -h, --help            Show this help message"
    echo "  -n NUM                Number of runs per app (default: 3)"
    echo "  -f, --faults          Enable fault injection"
    echo "  --num-faults NUM      Number of faults per run (1-5, default: 2)"
    echo "  --fault-seed NUM      Base random seed for faults (default: 42)"
    echo "  --fault-categories    Fault categories (space-separated, e.g., misconfiguration correlated)"
    echo "  --fault-severities    Fault severities (space-separated, e.g., low medium high)"
    echo ""
    echo "Examples:"
    echo "  $0                                          # Run all apps (3 runs each)"
    echo "  $0 hotel                                    # Run only hotelReservation"
    echo "  $0 -n 5 hotel                               # Run hotelReservation 5 times"
    echo "  $0 -f hotel                                 # Run with fault injection"
    echo "  $0 -f --num-faults 3 hotel                  # Inject 3 faults per run"
    echo "  $0 -f --fault-seed 100 hotel                # Custom fault seed"
    echo "  $0 -f --fault-severities high social media  # Only high-severity faults"
    echo ""
}

# Map short names to full paths
declare -A APP_PATHS=(
  ["hotel"]="/mnt/nvme1/khoav/Research/DeathStarBench/hotelReservation"
  ["social"]="/mnt/nvme1/khoav/Research/DeathStarBench/socialNetwork"
  ["media"]="/mnt/nvme1/khoav/Research/DeathStarBench/mediaMicroservices"
)

# Parse command-line arguments
apps=()
RUNS_PER_APP=3
FAULTS_ENABLED=false
NUM_FAULTS=2
FAULT_SEED=42
FAULT_CATEGORIES=()
FAULT_SEVERITIES=()

while [[ $# -gt 0 ]]; do
  case $1 in
    -h|--help)
      show_usage
      exit 0
      ;;
    -n)
      if [[ -z "$2" ]] || [[ ! "$2" =~ ^[0-9]+$ ]]; then
        echo -e "${RED}[ERROR]${NC} -n requires a positive number"
        exit 1
      fi
      RUNS_PER_APP=$2
      shift 2
      ;;
    -f|--faults)
      FAULTS_ENABLED=true
      shift
      ;;
    --num-faults)
      if [[ -z "$2" ]] || [[ ! "$2" =~ ^[1-5]$ ]]; then
        echo -e "${RED}[ERROR]${NC} --num-faults requires a number between 1 and 5"
        exit 1
      fi
      NUM_FAULTS=$2
      shift 2
      ;;
    --fault-seed)
      if [[ -z "$2" ]] || [[ ! "$2" =~ ^[0-9]+$ ]]; then
        echo -e "${RED}[ERROR]${NC} --fault-seed requires a positive number"
        exit 1
      fi
      FAULT_SEED=$2
      shift 2
      ;;
    --fault-categories)
      shift
      while [[ $# -gt 0 ]] && [[ ! "$1" =~ ^- ]]; do
        FAULT_CATEGORIES+=("$1")
        shift
      done
      ;;
    --fault-severities)
      shift
      while [[ $# -gt 0 ]] && [[ ! "$1" =~ ^- ]]; do
        FAULT_SEVERITIES+=("$1")
        shift
      done
      ;;
    *)
      # Treat as app name
      if [[ -n "${APP_PATHS[$1]}" ]]; then
        apps+=("${APP_PATHS[$1]}")
      else
        echo -e "${RED}[ERROR]${NC} Unknown app: $1 (valid: hotel, social, media)"
        echo "Run '$0 --help' for usage information"
        exit 1
      fi
      shift
      ;;
  esac
done

# If no apps specified, use all apps
if [ ${#apps[@]} -eq 0 ]; then
  apps=(
    "/mnt/nvme1/khoav/Research/DeathStarBench/hotelReservation"
    "/mnt/nvme1/khoav/Research/DeathStarBench/socialNetwork"
    "/mnt/nvme1/khoav/Research/DeathStarBench/mediaMicroservices"
  )
fi

# SDS operator path
SDS_ROOT="/mnt/nvme1/khoav/Research/sds"

# Summary counters
total_runs=0
total_trajectories_before=0
total_trajectories_after=0

print_header "Training Data Collection for GEPA"
echo "Apps to process: ${#apps[@]}"
echo "Runs per app: $RUNS_PER_APP"
echo "Total runs: $((${#apps[@]} * RUNS_PER_APP))"
if [ "$FAULTS_ENABLED" = true ]; then
  echo "Fault injection: ENABLED"
  echo "  Faults per run: $NUM_FAULTS"
  echo "  Base seed: $FAULT_SEED"
  if [ ${#FAULT_CATEGORIES[@]} -gt 0 ]; then
    echo "  Categories: ${FAULT_CATEGORIES[*]}"
  fi
  if [ ${#FAULT_SEVERITIES[@]} -gt 0 ]; then
    echo "  Severities: ${FAULT_SEVERITIES[*]}"
  fi
fi
echo ""

# Count existing trajectories
for app in "${apps[@]}"; do
  if [ -d "$app/.sds/trajectories" ]; then
    count=$(ls "$app/.sds/trajectories"/*.json 2>/dev/null | wc -l)
    total_trajectories_before=$((total_trajectories_before + count))
  fi
done

print_info "Existing trajectories: $total_trajectories_before"
echo ""

# Process each app
for app in "${apps[@]}"; do
  app_name=$(basename "$app")

  print_header "Processing: $app_name"

  # Check if app directory exists
  if [ ! -d "$app" ]; then
    print_error "Directory not found: $app"
    continue
  fi

  # Check if docker-compose.yml exists
  if [ ! -f "$app/docker-compose.yml" ] && [ ! -f "$app/docker-compose.yaml" ]; then
    print_error "No docker-compose.yml found in $app"
    continue
  fi

  # Run multiple times
  for run in $(seq 1 $RUNS_PER_APP); do
    print_info "Run $run/$RUNS_PER_APP for $app_name"

    # Navigate to app directory
    cd "$app"

    # 1. Clean shutdown of existing containers
    print_info "Cleaning up existing deployment..."
    if docker compose down -v 2>/dev/null; then
      print_success "Docker compose down completed"
    else
      print_info "No existing deployment to clean (this is fine)"
    fi

    # 2. Reset docker-compose.yml to original state
    print_info "Resetting docker-compose.yml from git..."
    if git checkout docker-compose.yml 2>/dev/null || git checkout docker-compose.yaml 2>/dev/null; then
      print_success "docker-compose.yml restored to original state"
    else
      print_info "No git changes to docker-compose.yml (already clean)"
    fi

    # 3. Remove generated scripts to force regeneration
    print_info "Removing old scripts to force regeneration..."
    if [ -d ".sds" ]; then
      rm -f .sds/deploy.sh .sds/health_check.sh
      rm -rf .sds/logs/*
      rm -f .sds/fault_injection.json
      print_success "Scripts removed (trajectories preserved)"
    fi

    # 4. Inject faults (if enabled)
    if [ "$FAULTS_ENABLED" = true ]; then
      run_seed=$((FAULT_SEED + run))
      print_info "Injecting $NUM_FAULTS fault(s) (seed=$run_seed)..."
      cd "$SDS_ROOT"

      inject_args="--repo-path $app --num-faults $NUM_FAULTS --seed $run_seed"
      if [ ${#FAULT_CATEGORIES[@]} -gt 0 ]; then
        inject_args="$inject_args --categories ${FAULT_CATEGORIES[*]}"
      fi
      if [ ${#FAULT_SEVERITIES[@]} -gt 0 ]; then
        inject_args="$inject_args --severities ${FAULT_SEVERITIES[*]}"
      fi

      if uv run -m app_operator.fault_injection.cli inject $inject_args; then
        print_success "Faults injected successfully"
      else
        print_error "Fault injection failed, running without faults"
      fi
    fi

    # 5. Run the operator
    print_info "Running SDS operator..."
    cd "$SDS_ROOT"

    if uv run -m app_operator run "$app"; then
      print_success "Operator completed run $run/$RUNS_PER_APP"
      total_runs=$((total_runs + 1))
    else
      print_error "Operator failed on run $run/$RUNS_PER_APP"
      # Continue to next run anyway
    fi

    # 6. Revert faults (if enabled)
    if [ "$FAULTS_ENABLED" = true ]; then
      print_info "Reverting fault injection..."
      cd "$SDS_ROOT"
      if uv run -m app_operator.fault_injection.cli revert --repo-path "$app"; then
        print_success "Faults reverted"
      else
        # Fallback: git checkout
        print_info "No backup to revert, using git checkout..."
        cd "$app"
        git checkout docker-compose.yml 2>/dev/null || git checkout docker-compose.yaml 2>/dev/null || true
      fi
    fi

    # Small delay between runs
    if [ $run -lt $RUNS_PER_APP ]; then
      print_info "Waiting 10 seconds before next run..."
      sleep 10
    fi
  done

  # Final cleanup for this app
  print_info "Cleaning up $app_name containers..."
  cd "$app"
  if docker compose down -v 2>/dev/null; then
    print_success "Cleaned up $app_name containers"
  else
    print_info "No containers to clean (already stopped)"
  fi

  # Report trajectories for this app
  if [ -d "$app/.sds/trajectories" ]; then
    count=$(ls "$app/.sds/trajectories"/*.json 2>/dev/null | wc -l)
    print_success "$app_name: $count total trajectories"
  fi

  echo ""
done

# Count final trajectories
for app in "${apps[@]}"; do
  if [ -d "$app/.sds/trajectories" ]; then
    count=$(ls "$app/.sds/trajectories"/*.json 2>/dev/null | wc -l)
    total_trajectories_after=$((total_trajectories_after + count))
  fi
done

new_trajectories=$((total_trajectories_after - total_trajectories_before))

# Final summary
print_header "Collection Complete"
echo "Total runs executed: $total_runs / $((${#apps[@]} * RUNS_PER_APP))"
echo "Trajectories before: $total_trajectories_before"
echo "Trajectories after: $total_trajectories_after"
echo -e "${GREEN}New trajectories: $new_trajectories${NC}"
echo ""

# Detailed breakdown
print_info "Trajectory breakdown by app:"
for app in "${apps[@]}"; do
  app_name=$(basename "$app")
  if [ -d "$app/.sds/trajectories" ]; then
    count=$(ls "$app/.sds/trajectories"/*.json 2>/dev/null | wc -l)
    echo "  $app_name: $count trajectories"
  else
    echo "  $app_name: 0 trajectories"
  fi
done

echo ""
print_info "Performing final cleanup of all containers..."
for app in "${apps[@]}"; do
  app_name=$(basename "$app")
  if (cd "$app" && docker compose down -v 2>/dev/null); then
    print_success "Final cleanup: $app_name"
  else
    print_info "Final cleanup: $app_name (no containers)"
  fi
done

echo ""
print_header "Next Steps"
echo "1. Analyze the collected data:"
echo "   cd $SDS_ROOT"
echo "   uv run -m app_operator analyze-prompts --phase deployment"
echo ""
echo "2. Run GEPA optimization:"
echo "   uv run -m app_operator optimize-prompts \\"
echo "       --prompts deployer_fix_error \\"
echo "       --use-seeds \\"
echo "       --optimizer COPRO \\"
echo "       --trajectories-dir /mnt/nvme1/khoav/Research/DeathStarBench/hotelReservation/.sds/trajectories \\"
echo "       --trajectories-dir /mnt/nvme1/khoav/Research/DeathStarBench/socialNetwork/.sds/trajectories \\"
echo "       --trajectories-dir /mnt/nvme1/khoav/Research/DeathStarBench/mediaMicroservices/.sds/trajectories"
echo ""

print_success "Training data collection complete!"
