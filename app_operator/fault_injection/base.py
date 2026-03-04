"""Base abstractions for fault injection.

Provides the FaultInjector ABC and ComposeManipulator utility for
working with Docker Compose data structures.
"""

import re
from abc import ABC, abstractmethod
from typing import Any

from app_operator.fault_injection.models import Fault, FaultResult


class FaultInjector(ABC):
    """Abstract base class for fault injectors.

    Subclasses implement platform-specific fault injection logic
    (e.g., Docker Compose, Kubernetes).
    """

    @abstractmethod
    def inject(
        self,
        fault: Fault,
        compose_data: dict[str, Any],
        target_service: str | None = None,
    ) -> FaultResult:
        """Inject a fault into the compose data.

        Args:
            fault: The fault to inject.
            compose_data: Parsed docker-compose data (modified in-place).
            target_service: Specific service to target. If None, auto-select.

        Returns:
            FaultResult describing what was changed.
        """

    @abstractmethod
    def get_applicable_services(
        self, fault: Fault, compose_data: dict[str, Any]
    ) -> list[str]:
        """Get services this fault can be applied to.

        Args:
            fault: The fault descriptor.
            compose_data: Parsed docker-compose data.

        Returns:
            List of applicable service names.
        """


class ComposeManipulator:
    """Utility for inspecting and modifying Docker Compose data structures."""

    @staticmethod
    def get_services(compose_data: dict[str, Any]) -> dict[str, Any]:
        """Get the services dictionary from compose data."""
        return compose_data.get("services", {})

    @staticmethod
    def get_service_ports(
        compose_data: dict[str, Any], service: str
    ) -> list[str]:
        """Get port mappings for a service.

        Returns:
            List of port mapping strings (e.g., ["8080:80"]).
        """
        services = compose_data.get("services", {})
        svc = services.get(service, {})
        return list(svc.get("ports", []))

    @staticmethod
    def get_service_environment(
        compose_data: dict[str, Any], service: str
    ) -> list[str]:
        """Get environment variables for a service.

        Normalizes both dict and list formats to list format.

        Returns:
            List of "KEY=VALUE" strings.
        """
        services = compose_data.get("services", {})
        svc = services.get(service, {})
        env = svc.get("environment", [])

        if isinstance(env, dict):
            return [f"{k}={v}" for k, v in env.items()]
        return list(env)

    @staticmethod
    def classify_service(
        service_name: str, service_config: dict[str, Any]
    ) -> str:
        """Classify a service by its role based on image/name heuristics.

        Returns one of: "database", "cache", "frontend", "backend",
        "proxy", "monitoring", "unknown".
        """
        image = str(service_config.get("image", "")).lower()
        name = service_name.lower()

        db_patterns = ["mongo", "mysql", "postgres", "mariadb", "redis", "memcache"]
        for pat in db_patterns:
            if pat in image or pat in name:
                if "redis" in pat or "memcache" in pat:
                    return "cache"
                return "database"

        proxy_patterns = ["nginx", "envoy", "traefik", "haproxy"]
        for pat in proxy_patterns:
            if pat in image or pat in name:
                return "proxy"

        monitoring_patterns = ["jaeger", "prometheus", "grafana", "consul"]
        for pat in monitoring_patterns:
            if pat in image or pat in name:
                return "monitoring"

        frontend_patterns = ["frontend", "web", "ui"]
        for pat in frontend_patterns:
            if pat in name:
                return "frontend"

        return "backend"

    @staticmethod
    def parse_port_mapping(port_str: str) -> dict[str, int] | None:
        """Parse a port mapping string into host/container ports.

        Args:
            port_str: Port mapping like "8080:80" or "8080".

        Returns:
            Dict with "host" and "container" keys, or None if unparseable.
        """
        port_str = str(port_str)
        match = re.match(r"(\d+):(\d+)", port_str)
        if match:
            return {"host": int(match.group(1)), "container": int(match.group(2))}
        match = re.match(r"^(\d+)$", port_str)
        if match:
            port = int(match.group(1))
            return {"host": port, "container": port}
        return None
