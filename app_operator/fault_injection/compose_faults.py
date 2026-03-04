"""Docker Compose fault implementations.

22 fault types inspired by SREGym's fault taxonomy, implemented as
modifications to Docker Compose YAML data structures.
"""

import random
from typing import Any, Callable

from app_operator.fault_injection.base import ComposeManipulator, FaultInjector
from app_operator.fault_injection.models import (
    Fault,
    FaultCategory,
    FaultResult,
    FaultSeverity,
)

# ---------------------------------------------------------------------------
# Fault catalog: 22 Docker Compose fault types
# ---------------------------------------------------------------------------

COMPOSE_FAULTS: list[Fault] = [
    # Misconfiguration faults
    Fault(
        fault_id="MISC-001",
        name="wrong_port_mapping",
        category=FaultCategory.MISCONFIGURATION,
        severity=FaultSeverity.LOW,
        description="Change host port to a random high port, breaking external access.",
    ),
    Fault(
        fault_id="MISC-002",
        name="missing_env_var",
        category=FaultCategory.MISCONFIGURATION,
        severity=FaultSeverity.MEDIUM,
        description="Remove a required environment variable from a service.",
    ),
    Fault(
        fault_id="MISC-003",
        name="wrong_image_tag",
        category=FaultCategory.MISCONFIGURATION,
        severity=FaultSeverity.MEDIUM,
        description="Change image tag to a nonexistent version.",
    ),
    Fault(
        fault_id="MISC-004",
        name="wrong_entrypoint",
        category=FaultCategory.MISCONFIGURATION,
        severity=FaultSeverity.HIGH,
        description="Override entrypoint with a nonexistent command.",
    ),
    Fault(
        fault_id="MISC-005",
        name="bad_volume_mount",
        category=FaultCategory.MISCONFIGURATION,
        severity=FaultSeverity.MEDIUM,
        description="Add a volume mount to a nonexistent host path.",
    ),
    Fault(
        fault_id="MISC-006",
        name="duplicate_port_conflict",
        category=FaultCategory.MISCONFIGURATION,
        severity=FaultSeverity.HIGH,
        description="Make two services bind the same host port.",
    ),
    # Security faults
    Fault(
        fault_id="SEC-001",
        name="removed_auth_config",
        category=FaultCategory.SECURITY,
        severity=FaultSeverity.MEDIUM,
        description="Remove authentication-related environment variables.",
    ),
    Fault(
        fault_id="SEC-002",
        name="exposed_debug_port",
        category=FaultCategory.SECURITY,
        severity=FaultSeverity.LOW,
        description="Expose an internal debug port to the host.",
    ),
    Fault(
        fault_id="SEC-003",
        name="privileged_container",
        category=FaultCategory.SECURITY,
        severity=FaultSeverity.MEDIUM,
        description="Set a container to run in privileged mode.",
    ),
    # Metastable faults
    Fault(
        fault_id="META-001",
        name="resource_limit_cpu",
        category=FaultCategory.METASTABLE,
        severity=FaultSeverity.MEDIUM,
        description="Set an extremely low CPU limit to starve the service.",
    ),
    Fault(
        fault_id="META-002",
        name="resource_limit_memory",
        category=FaultCategory.METASTABLE,
        severity=FaultSeverity.HIGH,
        description="Set memory limit too low, causing OOM kills.",
    ),
    Fault(
        fault_id="META-003",
        name="restart_loop_trigger",
        category=FaultCategory.METASTABLE,
        severity=FaultSeverity.HIGH,
        description="Set restart policy to always with a failing command.",
    ),
    Fault(
        fault_id="META-004",
        name="slow_healthcheck",
        category=FaultCategory.METASTABLE,
        severity=FaultSeverity.LOW,
        description="Add an aggressive healthcheck that marks services unhealthy.",
    ),
    Fault(
        fault_id="META-005",
        name="tmpfs_too_small",
        category=FaultCategory.METASTABLE,
        severity=FaultSeverity.MEDIUM,
        description="Mount a tiny tmpfs over a data directory.",
    ),
    # Correlated faults
    Fault(
        fault_id="CORR-001",
        name="remove_dependency",
        category=FaultCategory.CORRELATED,
        severity=FaultSeverity.MEDIUM,
        description="Remove depends_on entry, causing startup ordering issues.",
    ),
    Fault(
        fault_id="CORR-002",
        name="break_shared_database",
        category=FaultCategory.CORRELATED,
        severity=FaultSeverity.HIGH,
        description="Change the database connection string for one consumer.",
        applicable_services=("backend",),
    ),
    Fault(
        fault_id="CORR-003",
        name="cascading_port_change",
        category=FaultCategory.CORRELATED,
        severity=FaultSeverity.HIGH,
        description="Change a service's container port without updating consumers.",
    ),
    Fault(
        fault_id="CORR-004",
        name="remove_shared_network",
        category=FaultCategory.CORRELATED,
        severity=FaultSeverity.HIGH,
        description="Remove a shared network, isolating services.",
    ),
    Fault(
        fault_id="CORR-005",
        name="remove_shared_volume",
        category=FaultCategory.CORRELATED,
        severity=FaultSeverity.MEDIUM,
        description="Remove a named volume used by multiple services.",
    ),
    # Infrastructure faults
    Fault(
        fault_id="INFRA-001",
        name="dns_override",
        category=FaultCategory.INFRASTRUCTURE,
        severity=FaultSeverity.MEDIUM,
        description="Add a bogus DNS entry pointing a service to 127.0.0.1.",
    ),
    Fault(
        fault_id="INFRA-002",
        name="init_failure",
        category=FaultCategory.INFRASTRUCTURE,
        severity=FaultSeverity.HIGH,
        description="Add a failing init container that blocks startup.",
    ),
    Fault(
        fault_id="INFRA-003",
        name="read_only_rootfs",
        category=FaultCategory.INFRASTRUCTURE,
        severity=FaultSeverity.MEDIUM,
        description="Set read_only: true, preventing writes to the root filesystem.",
    ),
]

# Patterns for identifying authentication-related environment variable names.
AUTH_ENV_PATTERNS = ["password", "secret", "token", "auth", "key", "credential"]


class ComposeFaultInjector(FaultInjector):
    """Injects faults into Docker Compose YAML data structures.

    Uses a dispatch table mapping fault_id to an injection method.
    Each method modifies compose_data in-place and returns a FaultResult.
    """

    def __init__(self, rng: random.Random | None = None):
        self._rng = rng or random.Random()
        self._dispatch: dict[str, Callable[..., FaultResult]] = {
            "MISC-001": self._inject_wrong_port_mapping,
            "MISC-002": self._inject_missing_env_var,
            "MISC-003": self._inject_wrong_image_tag,
            "MISC-004": self._inject_wrong_entrypoint,
            "MISC-005": self._inject_bad_volume_mount,
            "MISC-006": self._inject_duplicate_port_conflict,
            "SEC-001": self._inject_removed_auth_config,
            "SEC-002": self._inject_exposed_debug_port,
            "SEC-003": self._inject_privileged_container,
            "META-001": self._inject_resource_limit_cpu,
            "META-002": self._inject_resource_limit_memory,
            "META-003": self._inject_restart_loop_trigger,
            "META-004": self._inject_failing_healthcheck,
            "META-005": self._inject_tmpfs_too_small,
            "CORR-001": self._inject_remove_dependency,
            "CORR-002": self._inject_break_shared_database,
            "CORR-003": self._inject_cascading_port_change,
            "CORR-004": self._inject_remove_shared_network,
            "CORR-005": self._inject_remove_shared_volume,
            "INFRA-001": self._inject_dns_override,
            "INFRA-002": self._inject_init_failure,
            "INFRA-003": self._inject_read_only_rootfs,
        }

    def inject(
        self,
        fault: Fault,
        compose_data: dict[str, Any],
        target_service: str | None = None,
    ) -> FaultResult:
        handler = self._dispatch.get(fault.fault_id)
        if handler is None:
            return FaultResult(
                fault=fault,
                target_service=target_service or "",
                success=False,
                error_message=f"No handler for fault {fault.fault_id}",
            )

        if target_service is None:
            applicable = self.get_applicable_services(fault, compose_data)
            if not applicable:
                return FaultResult(
                    fault=fault,
                    target_service="",
                    success=False,
                    error_message="No applicable services found",
                )
            target_service = self._rng.choice(applicable)

        return handler(fault, compose_data, target_service)

    def get_applicable_services(
        self, fault: Fault, compose_data: dict[str, Any]
    ) -> list[str]:
        services = ComposeManipulator.get_services(compose_data)
        if not services:
            return []

        # Faults with specific service-role requirements
        if fault.applicable_services:
            result = []
            for svc_name, svc_cfg in services.items():
                role = ComposeManipulator.classify_service(svc_name, svc_cfg)
                if role in fault.applicable_services:
                    result.append(svc_name)
            return result

        # Fault-specific applicability checks
        if fault.fault_id in ("MISC-001", "MISC-006", "CORR-003"):
            return [s for s in services if services[s].get("ports")]

        if fault.fault_id == "MISC-002":
            return [s for s in services if services[s].get("environment")]

        if fault.fault_id == "SEC-001":
            return [
                s for s in services
                if self._has_auth_env(services[s])
            ]

        if fault.fault_id == "CORR-001":
            return [s for s in services if services[s].get("depends_on")]

        if fault.fault_id in ("CORR-004",):
            return list(services.keys()) if compose_data.get("networks") else []

        if fault.fault_id == "CORR-005":
            return list(services.keys()) if compose_data.get("volumes") else []

        return list(services.keys())

    @staticmethod
    def _has_auth_env(svc_cfg: dict[str, Any]) -> bool:
        """Check if a service has authentication-related env vars."""
        env = svc_cfg.get("environment", [])
        if isinstance(env, dict):
            keys = [k.lower() for k in env.keys()]
        else:
            keys = [str(e).split("=")[0].lower() for e in env]

        return any(
            pat in k for k in keys for pat in AUTH_ENV_PATTERNS
        )

    # ------------------------------------------------------------------
    # Misconfiguration faults
    # ------------------------------------------------------------------

    def _inject_wrong_port_mapping(
        self, fault: Fault, data: dict[str, Any], service: str
    ) -> FaultResult:
        svc = data["services"][service]
        ports = svc.get("ports", [])
        if not ports:
            return FaultResult(fault=fault, target_service=service, success=False,
                               error_message="Service has no ports")

        old_port = str(ports[0])
        new_host_port = self._rng.randint(40000, 49999)
        parsed = ComposeManipulator.parse_port_mapping(old_port)
        if parsed:
            ports[0] = f"{new_host_port}:{parsed['container']}"
        else:
            ports[0] = str(new_host_port)

        return FaultResult(
            fault=fault, target_service=service,
            modified_fields={"ports[0]": {"old": old_port, "new": str(ports[0])}},
        )

    def _inject_missing_env_var(
        self, fault: Fault, data: dict[str, Any], service: str
    ) -> FaultResult:
        svc = data["services"][service]
        env = svc.get("environment", [])

        if isinstance(env, dict):
            if not env:
                return FaultResult(fault=fault, target_service=service, success=False,
                                   error_message="No environment variables")
            key = self._rng.choice(list(env.keys()))
            old_val = env.pop(key)
            return FaultResult(
                fault=fault, target_service=service,
                modified_fields={"environment": {"removed_key": key, "old_value": str(old_val)}},
            )

        if not env:
            return FaultResult(fault=fault, target_service=service, success=False,
                               error_message="No environment variables")
        idx = self._rng.randrange(len(env))
        removed = env.pop(idx)
        return FaultResult(
            fault=fault, target_service=service,
            modified_fields={"environment": {"removed": str(removed)}},
        )

    def _inject_wrong_image_tag(
        self, fault: Fault, data: dict[str, Any], service: str
    ) -> FaultResult:
        svc = data["services"][service]
        old_image = svc.get("image", "")
        if not old_image:
            return FaultResult(fault=fault, target_service=service, success=False,
                               error_message="Service has no image field")

        base = old_image.split(":")[0]
        new_image = f"{base}:nonexistent-v999.99.99"
        svc["image"] = new_image
        return FaultResult(
            fault=fault, target_service=service,
            modified_fields={"image": {"old": old_image, "new": new_image}},
        )

    def _inject_wrong_entrypoint(
        self, fault: Fault, data: dict[str, Any], service: str
    ) -> FaultResult:
        svc = data["services"][service]
        old_entrypoint = svc.get("entrypoint")
        svc["entrypoint"] = "/bin/nonexistent-cmd"
        return FaultResult(
            fault=fault, target_service=service,
            modified_fields={"entrypoint": {"old": old_entrypoint, "new": "/bin/nonexistent-cmd"}},
        )

    def _inject_bad_volume_mount(
        self, fault: Fault, data: dict[str, Any], service: str
    ) -> FaultResult:
        svc = data["services"][service]
        volumes = svc.setdefault("volumes", [])
        bad_mount = "/nonexistent/host/path/sds-fault:/data/fault-test"
        volumes.append(bad_mount)
        return FaultResult(
            fault=fault, target_service=service,
            modified_fields={"volumes": {"added": bad_mount}},
        )

    def _inject_duplicate_port_conflict(
        self, fault: Fault, data: dict[str, Any], service: str
    ) -> FaultResult:
        services = data["services"]
        svc = services[service]
        ports = svc.get("ports", [])
        if not ports:
            return FaultResult(fault=fault, target_service=service, success=False,
                               error_message="Service has no ports")

        # Find another service to conflict with
        other_services = [
            s for s in services
            if s != service and services[s].get("ports")
        ]
        if not other_services:
            return FaultResult(fault=fault, target_service=service, success=False,
                               error_message="No other service with ports to conflict with")

        other = self._rng.choice(other_services)
        other_ports = services[other]["ports"]
        # Take first port of target and assign it to the other service
        target_port = str(ports[0])
        parsed = ComposeManipulator.parse_port_mapping(target_port)
        if parsed:
            conflicting = f"{parsed['host']}:{parsed['container']}"
        else:
            conflicting = target_port

        old_other_port = str(other_ports[0])
        other_ports[0] = conflicting
        return FaultResult(
            fault=fault, target_service=other,
            modified_fields={
                "ports[0]": {"old": old_other_port, "new": conflicting},
                "conflicts_with": service,
            },
        )

    # ------------------------------------------------------------------
    # Security faults
    # ------------------------------------------------------------------

    def _inject_removed_auth_config(
        self, fault: Fault, data: dict[str, Any], service: str
    ) -> FaultResult:
        svc = data["services"][service]
        env = svc.get("environment", [])
        removed = []

        if isinstance(env, dict):
            keys_to_remove = [
                k for k in env
                if any(p in k.lower() for p in AUTH_ENV_PATTERNS)
            ]
            for k in keys_to_remove:
                removed.append(f"{k}={env.pop(k)}")
        else:
            new_env = []
            for e in env:
                var_name = str(e).split("=")[0].lower()
                if any(p in var_name for p in AUTH_ENV_PATTERNS):
                    removed.append(str(e))
                else:
                    new_env.append(e)
            svc["environment"] = new_env

        if not removed:
            return FaultResult(fault=fault, target_service=service, success=False,
                               error_message="No auth env vars found")

        return FaultResult(
            fault=fault, target_service=service,
            modified_fields={"environment": {"removed_auth_vars": removed}},
        )

    def _inject_exposed_debug_port(
        self, fault: Fault, data: dict[str, Any], service: str
    ) -> FaultResult:
        svc = data["services"][service]
        ports = svc.setdefault("ports", [])
        debug_port = f"{self._rng.randint(9000, 9999)}:9999"
        ports.append(debug_port)
        return FaultResult(
            fault=fault, target_service=service,
            modified_fields={"ports": {"added_debug_port": debug_port}},
        )

    def _inject_privileged_container(
        self, fault: Fault, data: dict[str, Any], service: str
    ) -> FaultResult:
        svc = data["services"][service]
        old_privileged = svc.get("privileged")
        svc["privileged"] = True
        return FaultResult(
            fault=fault, target_service=service,
            modified_fields={"privileged": {"old": old_privileged, "new": True}},
        )

    # ------------------------------------------------------------------
    # Metastable faults
    # ------------------------------------------------------------------

    def _inject_resource_limit_cpu(
        self, fault: Fault, data: dict[str, Any], service: str
    ) -> FaultResult:
        svc = data["services"][service]
        deploy = svc.setdefault("deploy", {})
        resources = deploy.setdefault("resources", {})
        limits = resources.setdefault("limits", {})
        old_cpus = limits.get("cpus")
        limits["cpus"] = "0.01"
        return FaultResult(
            fault=fault, target_service=service,
            modified_fields={"deploy.resources.limits.cpus": {"old": old_cpus, "new": "0.01"}},
        )

    def _inject_resource_limit_memory(
        self, fault: Fault, data: dict[str, Any], service: str
    ) -> FaultResult:
        svc = data["services"][service]
        deploy = svc.setdefault("deploy", {})
        resources = deploy.setdefault("resources", {})
        limits = resources.setdefault("limits", {})
        old_mem = limits.get("memory")
        limits["memory"] = "4m"
        return FaultResult(
            fault=fault, target_service=service,
            modified_fields={"deploy.resources.limits.memory": {"old": old_mem, "new": "4m"}},
        )

    def _inject_restart_loop_trigger(
        self, fault: Fault, data: dict[str, Any], service: str
    ) -> FaultResult:
        svc = data["services"][service]
        old_restart = svc.get("restart")
        old_command = svc.get("command")
        svc["restart"] = "always"
        svc["command"] = "sh -c 'exit 1'"
        return FaultResult(
            fault=fault, target_service=service,
            modified_fields={
                "restart": {"old": old_restart, "new": "always"},
                "command": {"old": old_command, "new": "sh -c 'exit 1'"},
            },
        )

    def _inject_failing_healthcheck(
        self, fault: Fault, data: dict[str, Any], service: str
    ) -> FaultResult:
        svc = data["services"][service]
        old_healthcheck = svc.get("healthcheck")
        svc["healthcheck"] = {
            "test": ["CMD", "false"],
            "interval": "1s",
            "timeout": "1s",
            "retries": 1,
            "start_period": "0s",
        }
        return FaultResult(
            fault=fault, target_service=service,
            modified_fields={"healthcheck": {"old": old_healthcheck, "new": svc["healthcheck"]}},
        )

    def _inject_tmpfs_too_small(
        self, fault: Fault, data: dict[str, Any], service: str
    ) -> FaultResult:
        svc = data["services"][service]
        tmpfs = svc.get("tmpfs")
        svc["tmpfs"] = "/tmp:size=1k"
        return FaultResult(
            fault=fault, target_service=service,
            modified_fields={"tmpfs": {"old": tmpfs, "new": "/tmp:size=1k"}},
        )

    # ------------------------------------------------------------------
    # Correlated faults
    # ------------------------------------------------------------------

    def _inject_remove_dependency(
        self, fault: Fault, data: dict[str, Any], service: str
    ) -> FaultResult:
        svc = data["services"][service]
        deps = svc.get("depends_on")
        if not deps:
            return FaultResult(fault=fault, target_service=service, success=False,
                               error_message="Service has no depends_on")

        if isinstance(deps, dict):
            key = self._rng.choice(list(deps.keys()))
            removed = {key: deps.pop(key)}
        elif isinstance(deps, list):
            idx = self._rng.randrange(len(deps))
            removed = deps.pop(idx)
        else:
            return FaultResult(fault=fault, target_service=service, success=False,
                               error_message="Unexpected depends_on format")

        return FaultResult(
            fault=fault, target_service=service,
            modified_fields={"depends_on": {"removed": removed}},
        )

    def _inject_break_shared_database(
        self, fault: Fault, data: dict[str, Any], service: str
    ) -> FaultResult:
        svc = data["services"][service]
        env = svc.get("environment", [])
        db_patterns = ["database", "db_host", "db_url", "mongo", "mysql", "postgres", "redis"]

        if isinstance(env, dict):
            db_keys = [k for k in env if any(p in k.lower() for p in db_patterns)]
            if not db_keys:
                return FaultResult(fault=fault, target_service=service, success=False,
                                   error_message="No database env vars found")
            key = self._rng.choice(db_keys)
            old_val = env[key]
            env[key] = "broken-host-sds-fault:65535"
            return FaultResult(
                fault=fault, target_service=service,
                modified_fields={
                    "environment": {
                        key: {
                            "old": str(old_val),
                            "new": "broken-host-sds-fault:65535"}}},
            )

        db_indices = [
            i for i, e in enumerate(env)
            if any(p in str(e).split("=")[0].lower() for p in db_patterns)
        ]
        if not db_indices:
            return FaultResult(fault=fault, target_service=service, success=False,
                               error_message="No database env vars found")
        idx = self._rng.choice(db_indices)
        old_val = env[idx]
        var_name = str(old_val).split("=")[0]
        env[idx] = f"{var_name}=broken-host-sds-fault:65535"
        return FaultResult(
            fault=fault, target_service=service,
            modified_fields={"environment": {var_name: {"old": str(old_val), "new": env[idx]}}},
        )

    def _inject_cascading_port_change(
        self, fault: Fault, data: dict[str, Any], service: str
    ) -> FaultResult:
        svc = data["services"][service]
        ports = svc.get("ports", [])
        if not ports:
            return FaultResult(fault=fault, target_service=service, success=False,
                               error_message="Service has no ports")

        old_port = str(ports[0])
        parsed = ComposeManipulator.parse_port_mapping(old_port)
        if not parsed:
            return FaultResult(fault=fault, target_service=service, success=False,
                               error_message="Cannot parse port mapping")

        new_container_port = parsed["container"] + 1000
        ports[0] = f"{parsed['host']}:{new_container_port}"
        return FaultResult(
            fault=fault, target_service=service,
            modified_fields={
                "ports[0]": {"old": old_port, "new": str(ports[0])},
                "note": "Container port changed without updating consumers",
            },
        )

    def _inject_remove_shared_network(
        self, fault: Fault, data: dict[str, Any], service: str
    ) -> FaultResult:
        networks = data.get("networks", {})
        if not networks:
            return FaultResult(fault=fault, target_service=service, success=False,
                               error_message="No top-level networks defined")

        net_name = self._rng.choice(list(networks.keys()))
        removed_config = networks.pop(net_name)

        # Also remove from services that reference it
        affected_services = []
        for svc_name, svc_cfg in data.get("services", {}).items():
            svc_networks = svc_cfg.get("networks")
            if isinstance(svc_networks, list) and net_name in svc_networks:
                svc_networks.remove(net_name)
                affected_services.append(svc_name)
            elif isinstance(svc_networks, dict) and net_name in svc_networks:
                del svc_networks[net_name]
                affected_services.append(svc_name)

        return FaultResult(
            fault=fault, target_service=service,
            modified_fields={
                "networks": {"removed": net_name, "config": str(removed_config)},
                "affected_services": affected_services,
            },
        )

    def _inject_remove_shared_volume(
        self, fault: Fault, data: dict[str, Any], service: str
    ) -> FaultResult:
        volumes = data.get("volumes", {})
        if not volumes:
            return FaultResult(fault=fault, target_service=service, success=False,
                               error_message="No top-level volumes defined")

        vol_name = self._rng.choice(list(volumes.keys()))
        removed_config = volumes.pop(vol_name)

        # Also remove service-level references to the volume
        affected_services = []
        for svc_name, svc_cfg in data.get("services", {}).items():
            svc_volumes = svc_cfg.get("volumes", [])
            if isinstance(svc_volumes, list):
                new_volumes = [v for v in svc_volumes if not str(v).startswith(f"{vol_name}:")]
                if len(new_volumes) != len(svc_volumes):
                    svc_cfg["volumes"] = new_volumes
                    affected_services.append(svc_name)

        return FaultResult(
            fault=fault, target_service=service,
            modified_fields={
                "volumes": {"removed": vol_name, "config": str(removed_config)},
                "affected_services": affected_services,
            },
        )

    # ------------------------------------------------------------------
    # Infrastructure faults
    # ------------------------------------------------------------------

    def _inject_dns_override(
        self, fault: Fault, data: dict[str, Any], service: str
    ) -> FaultResult:
        svc = data["services"][service]
        extra_hosts = svc.setdefault("extra_hosts", [])
        # Pick another service to break DNS for
        other_services = [s for s in data["services"] if s != service]
        if not other_services:
            dns_entry = "broken-host:127.0.0.1"
        else:
            target = self._rng.choice(other_services)
            dns_entry = f"{target}:127.0.0.1"
        extra_hosts.append(dns_entry)
        return FaultResult(
            fault=fault, target_service=service,
            modified_fields={"extra_hosts": {"added": dns_entry}},
        )

    def _inject_init_failure(
        self, fault: Fault, data: dict[str, Any], service: str
    ) -> FaultResult:
        svc = data["services"][service]
        old_entrypoint = svc.get("entrypoint")
        old_command = svc.get("command")

        # Wrap entrypoint to fail on init
        svc["entrypoint"] = "sh"
        svc["command"] = "-c 'echo SDS_FAULT: init failure && exit 1'"
        return FaultResult(
            fault=fault, target_service=service,
            modified_fields={
                "entrypoint": {"old": old_entrypoint, "new": "sh"},
                "command": {"old": old_command, "new": "-c 'echo SDS_FAULT: init failure && exit 1'"},
            },
        )

    def _inject_read_only_rootfs(
        self, fault: Fault, data: dict[str, Any], service: str
    ) -> FaultResult:
        svc = data["services"][service]
        old_read_only = svc.get("read_only")
        svc["read_only"] = True
        return FaultResult(
            fault=fault, target_service=service,
            modified_fields={"read_only": {"old": old_read_only, "new": True}},
        )
