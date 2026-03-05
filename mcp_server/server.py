# sds/mcp_server/server.py
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path

from mcp.server.fastmcp import FastMCP

# Initialize the server
mcp = FastMCP("SDS_Controller")

# --- Helper: Find the Project Root ---
# This assumes server.py is in sds/mcp_server/
# We go up two levels to find 'sds/'
SERVER_DIR = Path(__file__).parent.resolve()
PROJECT_ROOT = SERVER_DIR.parent
HEALTH_SCRIPT = PROJECT_ROOT / "apps/deathstarbench/hotelReservation/.sds/health_check.sh"


@mcp.tool()
def list_running_containers() -> str:
    """Checks which Docker containers are currently running."""
    try:
        return subprocess.check_output(["docker", "ps"], text=True)
    except subprocess.CalledProcessError as e:
        return f"Error checking Docker: {e}"


@mcp.tool()
def stop_container(container_id: str) -> str:
    """Stops a specific Docker container by ID."""
    try:
        subprocess.check_call(["docker", "stop", container_id])
        return f"Successfully stopped container {container_id}"
    except subprocess.CalledProcessError:
        return f"Failed to stop container {container_id}"


@mcp.tool()
def restart_container(container_name: str) -> str:
    """
    Restarts a Docker container by name.
    Use this to fix services that are unhealthy or stuck.
    """
    try:
        # We use 'docker restart' which is safer than stop+start
        subprocess.check_call(["docker", "restart", container_name])
        return f"Successfully restarted {container_name}"
    except subprocess.CalledProcessError as e:
        return f"Failed to restart {container_name}: {e}"


@mcp.tool()
def run_health_check() -> str:
    """
    Runs the comprehensive health_check.sh script for the Hotel Reservation app.
    Returns the full success/failure log.
    """
    if not HEALTH_SCRIPT.exists():
        return f"Error: Could not find health script at {HEALTH_SCRIPT}"

    try:
        # Run the bash script and capture output
        result = subprocess.run(["bash", str(HEALTH_SCRIPT)], capture_output=True, text=True)
        return result.stdout + "\n" + result.stderr
    except Exception as e:
        return f"Failed to run health check: {e!s}"


@mcp.tool()
def read_prometheus_metric(query: str = "up") -> str:
    """
    Queries the local Prometheus instance (localhost:9090) for metrics.
    Args:
        query: The PromQL query to run (default: 'up' checks if instances are alive).
    """
    prometheus_url = f"http://localhost:9090/api/v1/query?query={urllib.parse.quote(query)}"
    try:
        with urllib.request.urlopen(prometheus_url, timeout=10) as response:
            return response.read().decode("utf-8")
    except Exception as e:
        return f"Failed to query Prometheus: {e!s}"


if __name__ == "__main__":
    mcp.run()
