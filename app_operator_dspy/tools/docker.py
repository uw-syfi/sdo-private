"""Docker Compose tools for DSPy agents."""

from app_operator_dspy.tools.shell import run_shell

DEFAULT_TAIL = 50


def docker_compose_up(cwd: str, build: bool = True) -> str:
    """Start services with Docker Compose in detached mode.

    Args:
        cwd: Directory containing the docker-compose file.
        build: Whether to rebuild images before starting.

    Returns:
        Combined stdout/stderr output from the command.
    """
    build_flag = " --build" if build else ""
    return run_shell(f"docker compose up{build_flag} -d", cwd=cwd, timeout=300)


def docker_ps(cwd: str = ".") -> str:
    """List running Docker Compose containers and their status.

    Args:
        cwd: Directory containing the docker-compose file.

    Returns:
        Table of running containers with name, status, and ports.
    """
    return run_shell("docker compose ps", cwd=cwd)


def docker_logs(service: str, tail: int = DEFAULT_TAIL, cwd: str = ".") -> str:
    """Get recent Docker Compose logs for a specific service.

    Args:
        service: Name of the Docker Compose service.
        tail: Number of log lines to retrieve.
        cwd: Directory containing the docker-compose file.

    Returns:
        The last ``tail`` lines of the service's logs.
    """
    return run_shell(f"docker compose logs --tail={tail} {service}", cwd=cwd)
