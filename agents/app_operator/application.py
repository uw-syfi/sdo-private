"""Abstract base class for applications."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass
class DeploymentResult:
    """Result of a deployment or shutdown operation."""
    
    success: bool
    message: str
    
    def __str__(self) -> str:
        status = "SUCCESS" if self.success else "FAILURE"
        return f"[{status}] {self.message}"


@dataclass
class HealthCheckResult:
    """Result of a health check operation."""
    
    healthy: bool
    message: str
    details: Optional[dict] = None
    
    def __str__(self) -> str:
        status = "HEALTHY" if self.healthy else "UNHEALTHY"
        return f"[{status}] {self.message}"


class Application(ABC):
    """Abstract base class for all applications.
    
    Applications must implement methods for deployment, health checking,
    and graceful shutdown. The implementation details (scripts, APIs, etc.)
    are left to concrete subclasses.
    """
    
    @property
    @abstractmethod
    def name(self) -> str:
        """Return the application name.
        
        Returns:
            str: The unique identifier for this application.
        """
        pass
    
    @property
    @abstractmethod
    def description(self) -> str:
        """Return the application description.
        
        Returns:
            str: A human-readable description of the application.
        """
        pass
    
    @abstractmethod
    def deploy(self) -> DeploymentResult:
        """Deploy the application.
        
        This method should start all necessary services and infrastructure
        for the application to run.
        
        Returns:
            DeploymentResult: The result of the deployment operation.
        """
        pass
    
    @abstractmethod
    def health_check(self) -> HealthCheckResult:
        """Check the health of the application.
        
        This method should verify that the application is running correctly
        and all critical services are accessible.
        
        Returns:
            HealthCheckResult: The health status of the application.
        """
        pass
    
    @abstractmethod
    def shutdown(self) -> DeploymentResult:
        """Gracefully shutdown the application.
        
        This method should stop all services and clean up resources.
        
        Returns:
            DeploymentResult: The result of the shutdown operation.
        """
        pass
