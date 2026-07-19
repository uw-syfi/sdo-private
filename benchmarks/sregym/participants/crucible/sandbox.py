"""Shared SandboxConfig builder for crucible drivers.

Used by both the top-level SRE driver (``crucible.driver.create_driver``)
and the MCP-server-side subagent driver (``tools.mcp_server``) so that
sub-agents launched from KB tools get the same sandbox treatment as the
main agent.
"""

from __future__ import annotations

import os

from libs.agent_cli import SandboxConfig

_SYSTEM_READ_PATHS = (
    "/bin",
    "/sbin",
    "/usr",
    "/lib",
    "/lib64",
    "/etc",
    "/opt",
    "/var",
    "/proc",
    "/sys",
    "/dev",
    "/tmp",
    "/run",
)


def build_crucible_sandbox(exp_cwd: str) -> SandboxConfig:
    """Build the SandboxConfig used by crucible CLI agents.

    Confines bash subprocess reads to ``exp_cwd`` plus the system dirs
    required for shell commands to execute. Allows reads of the kubeconfig
    referenced by ``KUBECONFIG``/``SREGYM_BASE_KUBECONFIG``. Excludes
    ``kubectl``/``curl``/``wget`` from the sandbox so they can reach the
    per-worker k8s proxy on loopback.

    Args:
        exp_cwd: Absolute path to the experiment working directory.
    """
    allow_read = [exp_cwd, *_SYSTEM_READ_PATHS]
    for key in ("KUBECONFIG", "SREGYM_BASE_KUBECONFIG"):
        kc = os.environ.get(key)
        if not kc:
            continue
        kc_abs = os.path.abspath(kc.split(os.pathsep)[0])
        allow_read.append(kc_abs)
        allow_read.append(os.path.dirname(kc_abs))

    # kubectl writes its disk cache to ~/.kube/cache and ~/.kube/http-cache
    # by default; allow writes there so kubectl doesn't warn on every call.
    allow_write = [os.path.expanduser("~/.kube")]

    return SandboxConfig(
        deny_read=["/"],
        allow_read=allow_read,
        allow_write=allow_write,
        confine_native_reads_to=[exp_cwd],
        excluded_commands=["kubectl *", "curl *", "wget *"],
    )
