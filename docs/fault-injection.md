# Fault Injection for Training Data

The operator includes an SREGym-inspired fault injection module that modifies Docker Compose files before operator runs to generate diverse training trajectories for GEPA/DSPy optimization.

## Fault Taxonomy (22 types)

- **Misconfiguration (6):** wrong_port_mapping, missing_env_var, wrong_image_tag, wrong_entrypoint, bad_volume_mount, duplicate_port_conflict
- **Security (3):** removed_auth_config, exposed_debug_port, privileged_container
- **Metastable (5):** resource_limit_cpu, resource_limit_memory, restart_loop_trigger, slow_healthcheck, tmpfs_too_small
- **Correlated (5):** remove_dependency, break_shared_database, cascading_port_change, remove_shared_network, remove_shared_volume
- **Infrastructure (3):** dns_override, init_failure, read_only_rootfs

## Configuration

Add to `sds.toml`:

```toml
[fault_injection]
enabled = true                    # Enable fault injection for training data (default: false)
num_faults = 2                    # Number of faults per run (1-5, default: 2)
categories = ["misconfiguration", "correlated"]  # Fault categories to include (default: all)
severities = ["low", "medium", "high"]           # Severity levels (default: all)
exclude_faults = []               # Fault IDs to exclude (default: [])
seed = 42                         # Random seed for reproducibility (default: None)
backup_compose = true             # Back up compose files before injection (default: true)
platform = "compose"              # Target platform: "compose" or "k8s" (default: "compose")
```

## CLI Usage

```bash
# List all available faults
uv run -m app_operator.fault_injection.cli list

# Inject faults into a repo's compose file
uv run -m app_operator.fault_injection.cli inject \
    --repo-path /path/to/app --num-faults 2 --seed 42

# Revert to original compose file
uv run -m app_operator.fault_injection.cli revert --repo-path /path/to/app
```

## Training Data Collection with Faults

```bash
# Collect training data with fault injection enabled
scripts/collect_training_data.sh -f hotel
scripts/collect_training_data.sh -f --num-faults 3 --fault-seed 100 hotel
```

## Design Principles

1. **YAML manipulation (not Docker runtime):** Modifies docker-compose.yml before `docker compose up`. Simpler, reproducible, no elevated privileges needed.
2. **Standalone CLI + shell script:** Faults injected before operator runs, keeping operator unaware of faults. Preserves self-healing integrity.
3. **Backup-based revert:** `.sds-fault-backup` file works without git.
4. **Seeded random:** Reproducible fault selection. Shell script uses `seed + run_number` for diversity.
5. **ABC for extensibility:** `FaultInjector` ABC + `Fault.platform` field enables future `K8sFaultInjector` without changing registry/config/orchestrator.

## Module Structure

**Fault Injection** (`app_operator/fault_injection/`):
- **Models (`models.py`):** Fault, FaultResult, FaultCategory, FaultSeverity enums/dataclasses
- **Config (`config.py`):** FaultInjectionConfig with validation (`[fault_injection]` in sds.toml)
- **Base (`base.py`):** FaultInjector ABC and ComposeManipulator utility
- **Compose Faults (`compose_faults.py`):** 22 Docker Compose fault implementations + COMPOSE_FAULTS catalog
- **Registry (`registry.py`):** FaultRegistry for filtering and selecting faults
- **Injector (`injector.py`):** FaultInjectionOrchestrator for backup/inject/revert workflow
- **Reporter (`reporter.py`):** FaultReport for trajectory metadata bridge
- **CLI (`cli.py`):** Standalone CLI (`inject`/`revert`/`list`) for shell script integration

## Usage Tips

- When adding fault types: Add the `Fault` to `COMPOSE_FAULTS` in `compose_faults.py`, implement the `_inject_*` method in `ComposeFaultInjector`, register it in the dispatch table, and add tests in `test_compose_faults.py`.
