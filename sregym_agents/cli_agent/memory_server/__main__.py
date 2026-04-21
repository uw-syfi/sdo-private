"""Entry point for the incident memory MCP server.

Subcommands:
  (no subcommand)  stdio MCP server (used when cli_agent spawns it as a subprocess)
  start-daemon     start an HTTP/SSE MCP daemon in the background; writes a PID file
  stop-daemon      stop a running daemon by its PID file

start-daemon / stop-daemon read their configuration from SREGYM_EXPERIMENT_AGENT_CONFIG
(the JSON-encoded agent config written by run_sregym.py), so explicit flags are optional.
"""

from __future__ import annotations

import argparse
import errno
import json
import logging
import os
import signal
import socket
import sys
import time
from pathlib import Path
from typing import Any, cast

from .server import MemoryMCPServer
from .store import IncidentStore

_DEFAULT_PORT = 9953


def _pid_file(store_path: str) -> Path:
    return Path(store_path).with_suffix(".pid")


def _agent_config() -> dict[str, Any]:
    raw = os.getenv("SREGYM_EXPERIMENT_AGENT_CONFIG", "")
    if not raw:
        return {}
    try:
        return cast("dict[str, Any]", json.loads(raw))
    except json.JSONDecodeError:
        return {}


def _port_in_use(host: str, port: int) -> bool:
    """Return True only when a live process is listening on *port*.

    ``SO_REUSEADDR`` matches how the HTTP daemon binds, so sockets lingering
    in ``TIME_WAIT`` (from a just-stopped daemon) aren't reported as in-use.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((host, port))
    except OSError as exc:
        return exc.errno in (errno.EADDRINUSE, errno.EACCES)
    finally:
        sock.close()
    return False


def _pids_listening_on(port: int) -> list[int]:
    """Return PIDs with a LISTEN socket on *port* (127.0.0.1 or any-iface).

    Uses /proc directly so we don't depend on lsof/ss and avoid false positives
    from cmdline-based matching (which would also match the current ``uv run``
    wrapper process).
    """
    target_hex_be = f":{port:04X}"
    inodes: set[str] = set()
    for fname in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            with open(fname) as fh:
                next(fh, None)
                for line in fh:
                    parts = line.split()
                    if len(parts) < 10:
                        continue
                    local = parts[1]
                    state = parts[3]
                    if state == "0A" and local.endswith(target_hex_be):
                        inodes.add(parts[9])
        except OSError:
            continue
    if not inodes:
        return []
    pids: list[int] = []
    self_pid = os.getpid()
    for pid_dir in os.listdir("/proc"):
        if not pid_dir.isdigit():
            continue
        pid = int(pid_dir)
        if pid == self_pid:
            continue
        fd_dir = f"/proc/{pid_dir}/fd"
        try:
            fds = os.listdir(fd_dir)
        except OSError:
            continue
        for fd in fds:
            try:
                link = os.readlink(f"{fd_dir}/{fd}")
            except OSError:
                continue
            if link.startswith("socket:[") and link[8:-1] in inodes:
                pids.append(pid)
                break
    return pids


def _kill_stale_daemons(port: int) -> bool:
    """Kill stray daemons and wait for the port to free. Returns True on success."""
    pids = _pids_listening_on(port)
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
            print(f"  killed stale memory daemon pid={pid}", file=sys.stderr)
        except ProcessLookupError:
            pass
    # Wait up to ~3s for port to free.
    for _ in range(30):
        if not _port_in_use("127.0.0.1", port):
            return True
        time.sleep(0.1)
    # Escalate to SIGKILL.
    for pid in pids:
        try:
            os.kill(pid, signal.SIGKILL)
            print(f"  SIGKILLed stale memory daemon pid={pid}", file=sys.stderr)
        except ProcessLookupError:
            pass
    for _ in range(30):
        if not _port_in_use("127.0.0.1", port):
            return True
        time.sleep(0.1)
    return False


# ---------------------------------------------------------------------------
# stdio (legacy / per-agent subprocess mode)
# ---------------------------------------------------------------------------


def _cmd_stdio(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(description="Incident memory MCP server (stdio)")
    parser.add_argument("--store-path", required=True)
    parser.add_argument("--merge-model", default=None)
    args = parser.parse_args(argv)
    store = IncidentStore(args.store_path)
    server = MemoryMCPServer(store, merge_model=args.merge_model)
    server.run()


# ---------------------------------------------------------------------------
# start-daemon
# ---------------------------------------------------------------------------


def _cmd_start_daemon(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(description="Start memory MCP HTTP daemon")
    parser.add_argument("--store-path", default=None)
    parser.add_argument("--merge-model", default=None)
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args(argv)

    cfg = _agent_config()
    store_path: str | None = cast("str | None", args.store_path) or cast("str | None", cfg.get("memory_store"))
    merge_model: str | None = cast("str | None", args.merge_model) or cast("str | None", cfg.get("memory_merge_model"))
    port: int = cast("int | None", args.port) or int(cast("int | str", cfg.get("memory_port", _DEFAULT_PORT)))

    if not store_path:
        # No memory configured for this run — nothing to start.
        sys.exit(0)

    pid_path = _pid_file(store_path)
    pid_path.parent.mkdir(parents=True, exist_ok=True)

    # If a stale daemon holds the port, kill it — otherwise the new daemon
    # silently fails to bind and the agents end up talking to a daemon whose
    # DB file may have been deleted, producing cryptic
    # "unable to open database file" errors.
    if _port_in_use("127.0.0.1", port):
        print(
            f"  port {port} in use; attempting to reclaim by killing stale daemons",
            file=sys.stderr,
        )
        if not _kill_stale_daemons(port):
            print(
                f"ERROR: port {port} still in use after killing stale daemons; aborting",
                file=sys.stderr,
            )
            sys.exit(1)

    # Bind the socket in the parent so bind errors are reported loudly
    # instead of disappearing into the forked child's /dev/null stderr.
    from .http_server import MemoryHTTPDaemon

    store = IncidentStore(store_path)
    mcp_server = MemoryMCPServer(store, merge_model=merge_model)
    try:
        daemon = MemoryHTTPDaemon(mcp_server, host="127.0.0.1", port=port)
    except OSError as exc:
        print(f"ERROR: failed to bind memory daemon on port {port}: {exc}", file=sys.stderr)
        sys.exit(1)

    log_path = Path(store_path).with_suffix(".log")

    pid = os.fork()
    if pid > 0:
        pid_path.write_text(str(pid))
        print(f"Memory daemon started (pid={pid}) on port {port}, store={store_path}")
        print(f"  daemon log: {log_path}")
        # Parent doesn't serve; let the child own the listening socket.
        sys.exit(0)

    # Daemon child: detach from terminal and serve.
    os.setsid()
    log_fd = os.open(str(log_path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    devnull_in = os.open(os.devnull, os.O_RDONLY)
    os.dup2(devnull_in, 0)
    os.dup2(log_fd, 1)
    os.dup2(log_fd, 2)
    os.close(devnull_in)
    os.close(log_fd)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger(__name__).info("memory daemon serving on 127.0.0.1:%d store=%s", port, store_path)
    daemon.run()


# ---------------------------------------------------------------------------
# stop-daemon
# ---------------------------------------------------------------------------


def _cmd_stop_daemon(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(description="Stop memory MCP HTTP daemon")
    parser.add_argument("--store-path", default=None)
    args = parser.parse_args(argv)

    cfg = _agent_config()
    store_path: str | None = cast("str | None", args.store_path) or cast("str | None", cfg.get("memory_store"))

    if not store_path:
        sys.exit(0)

    pid_path = _pid_file(store_path)
    if not pid_path.exists():
        print(f"No PID file at {pid_path}; daemon may not be running")
        return

    pid = int(pid_path.read_text().strip())
    try:
        os.kill(pid, signal.SIGTERM)
        pid_path.unlink()
        print(f"Memory daemon stopped (pid={pid})")
    except ProcessLookupError:
        print(f"Process {pid} not found; removing stale PID file")
        pid_path.unlink()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)

    args = sys.argv[1:]
    if args and args[0] == "start-daemon":
        _cmd_start_daemon(args[1:])
    elif args and args[0] == "stop-daemon":
        _cmd_stop_daemon(args[1:])
    else:
        _cmd_stdio(args)


if __name__ == "__main__":
    main()
