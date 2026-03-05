# SDS Docker Controller MCP Server

An MCP (Model Context Protocol) server that exposes Docker and Prometheus controls to AI agents for autonomous health remediation of SDS-managed applications.

## What It Does

The `docker_controller` MCP server gives AI agents (e.g. Gemini) the ability to inspect running containers, restart unhealthy services, run health checks, and query Prometheus metrics — all without leaving the agent's tool-call loop.

## Running

```bash
uv run ./mcp_server/server.py
```

## Exposed Tools

| Tool | Description |
|---|---|
| `list_running_containers` | Lists all currently running Docker containers (`docker ps`) |
| `stop_container` | Stops a container by ID |
| `restart_container` | Restarts a container by name; use to recover unhealthy or stuck services |
| `run_health_check` | Runs `health_check.sh` for the Hotel Reservation app and returns the full log |
| `read_prometheus_metric` | Queries the local Prometheus instance (localhost:9090) with a PromQL expression |

## Configuration

The server is registered as `docker_controller` in `.gemini/settings.json`:

```json
"mcpServers": {
  "docker_controller": {
    "command": "uv",
    "args": ["run", "./mcp_server/server.py"]
  }
}
```
