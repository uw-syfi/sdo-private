#!/usr/bin/env bash
# Clean stale artifacts from E2E prompt-optimization experiments.
#
# Default mode is dry-run (prints what would be removed).
# Use --apply to actually delete files/directories.
#
# Targets are derived from:
#   - exp_config/rlm-e2e/config.toml
#   - exp_config/subagent-e2e/config.toml
#   - exp_config/hybrid-e2e/config.toml
#
# It removes:
#   - work_dir from each config
#   - per-experiment logs (exp_config/*-e2e/e2e_optimize.log)
#   - master log (exp_config/rlm_e2e_experiments.log)
#   - optimized prompt outputs (app_operator/prompts/optimized/<output_prefix>)
#   - Docker containers from experiment runs (matched by compose project label)
#   - Docker networks from experiment runs
#   - .sds directories left in training/validation app source dirs

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(realpath "$SCRIPT_DIR/../..")"
SESSION="rlm-e2e-exp"

DRY_RUN=true
KEEP_OPTIMIZED=false
KILL_SESSION=false

CONFIGS=(
    "$REPO/exp_config/rlm-e2e/config.toml"
    "$REPO/exp_config/subagent-e2e/config.toml"
    "$REPO/exp_config/hybrid-e2e/config.toml"
)

usage() {
    cat <<EOF
Usage:
  bash scripts/prompt_opt/clean_e2e_experiments.sh [options]

Options:
  --apply            Actually delete artifacts (default is dry-run)
  --dry-run          Print what would be removed (default)
  --keep-optimized   Keep app_operator/prompts/optimized/<output_prefix> dirs
  --kill-session     Kill tmux session '$SESSION' if it exists
  -h, --help         Show this help
EOF
}

log() {
    echo "[$(date '+%H:%M:%S')] $*"
}

extract_toml_string() {
    local key="$1"
    local file="$2"
    awk -F= -v k="$key" '
        $1 ~ "^[[:space:]]*" k "[[:space:]]*$" {
            value=$2
            sub(/[[:space:]]*#.*/, "", value)
            gsub(/^[[:space:]]+|[[:space:]]+$/, "", value)
            gsub(/^"/, "", value)
            gsub(/"$/, "", value)
            print value
            exit
        }
    ' "$file"
}

# Extract app paths from [training] and [validation] TOML sections.
extract_app_paths() {
    local config="$1"
    for section in training validation; do
        while IFS= read -r line; do
            line="$(echo "$line" | sed 's/^[[:space:]]*"//;s/"[[:space:]]*,*$//')"
            [[ -n "$line" ]] && echo "$line"
        done < <(grep -A20 "^\[$section\]" "$config" 2>/dev/null | grep -E '^\s*"' || true)
    done
}

to_abs_path() {
    local path="$1"
    if [[ "$path" = /* ]]; then
        echo "$path"
    else
        echo "$REPO/$path"
    fi
}

while [[ $# -gt 0 ]]; do
    case "$1" in
    --apply)
        DRY_RUN=false
        ;;
    --dry-run)
        DRY_RUN=true
        ;;
    --keep-optimized)
        KEEP_OPTIMIZED=true
        ;;
    --kill-session)
        KILL_SESSION=true
        ;;
    -h | --help)
        usage
        exit 0
        ;;
    *)
        echo "Unknown argument: $1" >&2
        usage >&2
        exit 2
        ;;
    esac
    shift
done

if tmux has-session -t "$SESSION" 2>/dev/null; then
    if [[ "$DRY_RUN" == true ]]; then
        log "tmux session '$SESSION' is currently running."
    elif [[ "$KILL_SESSION" == true ]]; then
        log "Killing tmux session '$SESSION'..."
        tmux kill-session -t "$SESSION"
    else
        log "tmux session '$SESSION' is still running."
        log "Stop it first, or rerun with --kill-session."
        exit 1
    fi
fi

# ── Phase 1: Collect file targets ────────────────────────────────────────────

declare -A seen_paths=()
declare -a targets=()

add_target() {
    local target="$1"
    [[ -z "$target" ]] && return 0
    if [[ -n "${seen_paths[$target]:-}" ]]; then
        return 0
    fi
    seen_paths["$target"]=1
    targets+=("$target")
}

add_target "$REPO/exp_config/rlm_e2e_experiments.log"

for config in "${CONFIGS[@]}"; do
    if [[ ! -f "$config" ]]; then
        log "Skipping missing config: $config"
        continue
    fi

    config_dir="$(dirname "$config")"
    add_target "$config_dir/e2e_optimize.log"

    work_dir="$(extract_toml_string "work_dir" "$config")"
    if [[ -n "$work_dir" ]]; then
        add_target "$(to_abs_path "$work_dir")"
    fi

    if [[ "$KEEP_OPTIMIZED" == false ]]; then
        output_prefix="$(extract_toml_string "output_prefix" "$config")"
        if [[ -n "$output_prefix" ]]; then
            add_target "$REPO/app_operator/prompts/optimized/$output_prefix"
        fi
    fi
done

# ── Phase 2: Collect Docker targets ──────────────────────────────────────────

# Collect work_dir paths to find experiment subdirectory names.
declare -a work_dirs=()
for config in "${CONFIGS[@]}"; do
    [[ ! -f "$config" ]] && continue
    wd="$(extract_toml_string "work_dir" "$config")"
    [[ -n "$wd" ]] && work_dirs+=("$(to_abs_path "$wd")")
done

# Build list of docker compose project names from experiment workdir subdirs.
# Experiment dirs are named like iter1_c1_hotelReservation; docker compose
# normalises project names by lowercasing and stripping non-alphanumeric chars.
declare -A docker_projects=()
for wd in "${work_dirs[@]}"; do
    [[ ! -d "$wd" ]] && continue
    for sub in "$wd"/*/; do
        [[ ! -d "$sub" ]] && continue
        dname="$(basename "$sub")"
        normalised="$(echo "$dname" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9]//g')"
        [[ -n "$normalised" ]] && docker_projects["$normalised"]=1
        hyphenised="$(echo "$dname" | tr '[:upper:]' '[:lower:]' | tr '_' '-' | sed 's/[^a-z0-9-]//g')"
        [[ -n "$hyphenised" ]] && docker_projects["$hyphenised"]=1
    done
done

# Also catch containers from training/validation apps run directly.
for config in "${CONFIGS[@]}"; do
    [[ ! -f "$config" ]] && continue
    while IFS= read -r app_path; do
        app_name="$(basename "$app_path")"
        normalised="$(echo "$app_name" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9]//g')"
        [[ -n "$normalised" ]] && docker_projects["$normalised"]=1
    done < <(extract_app_paths "$config")
done

# Find experiment Docker containers.
container_ids=()
if [[ "${#docker_projects[@]}" -gt 0 ]]; then
    for project in "${!docker_projects[@]}"; do
        while IFS= read -r cid; do
            [[ -n "$cid" ]] && container_ids+=("$cid")
        done < <(docker ps -a -q --filter "label=com.docker.compose.project=$project" 2>/dev/null)
    done
    if [[ "${#container_ids[@]}" -gt 0 ]]; then
        readarray -t container_ids < <(printf '%s\n' "${container_ids[@]}" | sort -u)
    fi
fi

# Find experiment Docker networks.
docker_networks=()
if [[ "${#docker_projects[@]}" -gt 0 ]]; then
    for project in "${!docker_projects[@]}"; do
        while IFS= read -r net; do
            [[ -n "$net" ]] && docker_networks+=("$net")
        done < <(docker network ls --format '{{.Name}}' | grep -F "$project" 2>/dev/null)
    done
fi

# ── Phase 3: Collect .sds dirs in source apps ────────────────────────────────

sds_dirs=()
for config in "${CONFIGS[@]}"; do
    [[ ! -f "$config" ]] && continue
    while IFS= read -r app_path; do
        abs_app="$(to_abs_path "$app_path")"
        sds_dir="$abs_app/.sds"
        [[ -d "$sds_dir" ]] && sds_dirs+=("$sds_dir")
    done < <(extract_app_paths "$config")
done

# ── Phase 4: Report ──────────────────────────────────────────────────────────

existing=0
missing=0
log "File targets:"
for path in "${targets[@]}"; do
    if [[ -e "$path" ]]; then
        echo "  [exists]  $path"
        existing=$((existing + 1))
    else
        echo "  [missing] $path"
        missing=$((missing + 1))
    fi
done
echo "  ($existing existing, $missing missing)"

log "Docker targets:"
echo "  Containers: ${#container_ids[@]}"
echo "  Networks:   ${#docker_networks[@]}"
for net in "${docker_networks[@]}"; do
    echo "    $net"
done

log "Source app .sds dirs: ${#sds_dirs[@]}"
for d in "${sds_dirs[@]}"; do
    echo "    $d"
done

total=$((existing + ${#container_ids[@]} + ${#docker_networks[@]} + ${#sds_dirs[@]}))

if [[ "$DRY_RUN" == true ]]; then
    log "Dry-run complete ($total items to clean). Re-run with --apply to clean."
    exit 0
fi

if [[ "$total" -eq 0 ]]; then
    log "Nothing to clean."
    exit 0
fi

# ── Phase 5: Apply ───────────────────────────────────────────────────────────

# Remove files/directories
for path in "${targets[@]}"; do
    [[ ! -e "$path" ]] && continue
    log "Removing: $path"
    rm -rf "$path"
done

# Remove Docker containers
if [[ "${#container_ids[@]}" -gt 0 ]]; then
    log "Removing ${#container_ids[@]} experiment Docker container(s)..."
    docker rm -f "${container_ids[@]}" 2>/dev/null || true
fi

# Remove Docker networks
for net in "${docker_networks[@]}"; do
    log "Removing Docker network: $net"
    docker network rm "$net" 2>/dev/null || true
done

# Remove .sds dirs from source apps
for sds_dir in "${sds_dirs[@]}"; do
    log "Removing .sds from source app: $sds_dir"
    rm -rf "$sds_dir"
done

log "Cleanup complete."
