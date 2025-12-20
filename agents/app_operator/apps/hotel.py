"""Hotel Reservation application implementation."""

import os
import subprocess
from pathlib import Path

from app_operator.application import Application, DeploymentResult, HealthCheckResult


class HotelApplication(Application):
    """DeathStarBench Hotel Reservation application.
    
    This application uses bash scripts for deployment and health checking.
    The scripts are located in deploy/deathstarbench/hotel/.
    """
    
    def __init__(self, project_root: str = "/mnt/sda/shli/sds"):
        """Initialize the hotel application.
        
        Args:
            project_root: Path to the project root directory.
        """
        self.project_root = Path(project_root)
        self.deploy_dir = self.project_root / "deploy" / "deathstarbench" / "hotel"
        self.deploy_script = self.deploy_dir / "deploy.sh"
        self.health_check_script = self.deploy_dir / "health_check.sh"
        
        # Validate that scripts exist
        if not self.deploy_script.exists():
            raise FileNotFoundError(f"Deploy script not found: {self.deploy_script}")
        if not self.health_check_script.exists():
            raise FileNotFoundError(f"Health check script not found: {self.health_check_script}")
    
    @property
    def name(self) -> str:
        """Return the application name."""
        return "hotel"
    
    @property
    def description(self) -> str:
        """Return the application description."""
        return "DeathStarBench Hotel Reservation"
    
    def is_deployed(self) -> bool:
        """Check if the hotel application is already deployed.
        
        This checks if Docker containers are running by using docker compose ps.
        
        Returns:
            bool: True if containers are running, False otherwise.
        """
        try:
            # Check if containers are running
            result = subprocess.run(
                ["docker", "compose", "ps", "-q", "--status", "running"],
                cwd=self.project_root / "apps" / "deathstarbench" / "hotelReservation",
                capture_output=True,
                text=True,
                timeout=10
            )
            
            # If we get output, containers are running
            running_containers = result.stdout.strip()
            return bool(running_containers) and result.returncode == 0
            
        except Exception:
            # On any error, assume not deployed
            return False
    
    def deploy(self) -> DeploymentResult:
        """Deploy the hotel application using deploy.sh start.
        
        Returns:
            DeploymentResult: The result of the deployment operation.
        """
        try:
            result = subprocess.run(
                ["./deploy.sh", "start"],
                cwd=self.deploy_dir,
                capture_output=True,
                text=True,
                timeout=300  # 5 minute timeout
            )
            
            # Collect output
            output = result.stdout if result.stdout else result.stderr
            
            if result.returncode == 0:
                return DeploymentResult(
                    success=True,
                    message="Application deployed successfully"
                )
            else:
                return DeploymentResult(
                    success=False,
                    message=f"Deployment failed with exit code {result.returncode}: {output}"
                )
                
        except subprocess.TimeoutExpired:
            return DeploymentResult(
                success=False,
                message="Deployment timed out after 5 minutes"
            )
        except Exception as e:
            return DeploymentResult(
                success=False,
                message=f"Deployment error: {str(e)}"
            )
    
    def health_check(self) -> HealthCheckResult:
        """Check the health of the hotel application using health_check.sh.
        
        Returns:
            HealthCheckResult: The health status of the application.
        """
        try:
            result = subprocess.run(
                ["./health_check.sh"],
                cwd=self.deploy_dir,
                capture_output=True,
                text=True,
                timeout=30  # 30 second timeout for health check
            )
            
            is_healthy = result.returncode == 0
            
            return HealthCheckResult(
                healthy=is_healthy,
                message="All health checks passed" if is_healthy else "Some health checks failed",
                details={
                    "exit_code": result.returncode,
                    "output": result.stdout if result.stdout else result.stderr
                }
            )
            
        except subprocess.TimeoutExpired:
            return HealthCheckResult(
                healthy=False,
                message="Health check timed out",
                details={"error": "timeout"}
            )
        except Exception as e:
            return HealthCheckResult(
                healthy=False,
                message=f"Health check error: {str(e)}",
                details={"error": str(e)}
            )
    
    def shutdown(self) -> DeploymentResult:
        """Shutdown the hotel application using deploy.sh stop.
        
        Returns:
            DeploymentResult: The result of the shutdown operation.
        """
        try:
            result = subprocess.run(
                ["./deploy.sh", "stop"],
                cwd=self.deploy_dir,
                capture_output=True,
                text=True,
                timeout=120  # 2 minute timeout
            )
            
            output = result.stdout if result.stdout else result.stderr
            
            if result.returncode == 0:
                return DeploymentResult(
                    success=True,
                    message="Application stopped successfully"
                )
            else:
                return DeploymentResult(
                    success=False,
                    message=f"Shutdown failed with exit code {result.returncode}: {output}"
                )
                
        except subprocess.TimeoutExpired:
            return DeploymentResult(
                success=False,
                message="Shutdown timed out after 2 minutes"
            )
        except Exception as e:
            return DeploymentResult(
                success=False,
                message=f"Shutdown error: {str(e)}"
            )
