#!/usr/bin/env python3
"""Assign unique port offsets to experiment subdirectories.

Each subdirectory containing a docker-compose.yml gets a unique host-port
offset (index * STRIDE) so that all subdirectories can run concurrently
without port or container-name collisions.

Usage:
    uv run python scripts/isolate_exp_ports.py /path/to/exp/socialNetwork
    uv run python scripts/isolate_exp_ports.py /path/to/exp/hotelReservation --reference baseline-hotelres

The --reference flag names the subdirectory whose current ports are treated
as the base (offset 0).  Defaults to the first subdirectory alphabetically.
This is most useful when some directories have already been modified by the
operator — pick one whose ports are still pristine.

What it does per directory:
  1. Rewrites host ports in docker-compose.yml  (container ports untouched)
  2. Strips any hardcoded container_name fields  (COMPOSE_PROJECT_NAME handles naming)
  3. Creates .env  with COMPOSE_PROJECT_NAME set to the directory name

Host ports are written as plain literals so the LLM agent reads them
directly when generating deploy.sh / health_check.sh on the next run.
"""

import re
import sys
from pathlib import Path

import yaml

STRIDE = 1000


def find_exp_dirs(root: Path) -> list[Path]:
    """Immediate subdirectories that contain docker-compose.yml, sorted."""
    return sorted(
        d for d in root.iterdir()
        if d.is_dir() and (d / "docker-compose.yml").exists()
    )


def parse_service_ports(compose_path: Path) -> dict[str, list[tuple[str, str]]]:
    """Return {service: [(host_str, container_spec), ...]} for host-mapped ports only.

    container_spec includes the protocol suffix when present (e.g. "53/udp").
    Bare container-only ports like "14269" are skipped.
    """
    with open(compose_path) as f:
        data = yaml.safe_load(f)
    result: dict[str, list[tuple[str, str]]] = {}
    for svc, defn in (data.get("services") or {}).items():
        mapped: list[tuple[str, str]] = []
        for p in defn.get("ports") or []:
            m = re.match(r"^(\d+):([\d]+(?:/\w+)?)$", str(p))
            if m:
                mapped.append((m.group(1), m.group(2)))
        if mapped:
            result[svc] = mapped
    return result


def apply_offsets(root: Path, reference_name: str | None = None) -> None:
    dirs = find_exp_dirs(root)
    if not dirs:
        print(f"No experiment directories found in {root}")
        return

    # Reference directory is placed first (offset 0); rest stay sorted.
    if reference_name:
        ref_path = root / reference_name
        if ref_path not in dirs:
            print(f"ERROR: '{reference_name}' is not a subdirectory with docker-compose.yml")
            sys.exit(1)
        dirs = [ref_path] + [d for d in dirs if d != ref_path]
    # else: already sorted; first entry is the reference

    ref_dir = dirs[0]
    ref_ports = parse_service_ports(ref_dir / "docker-compose.yml")

    print(f"Reference: {ref_dir.name}  (offset 0 — ports unchanged)")
    for svc, ports in ref_ports.items():
        print(f"  {svc}: {[f'{h}:{c}' for h, c in ports]}")
    print()

    # ── apply offsets ────────────────────────────────────────────────────
    all_host_ports: dict[str, set[int]] = {}   # dir_name -> final host ports

    for idx, dir_path in enumerate(dirs):
        offset = idx * STRIDE
        compose_path = dir_path / "docker-compose.yml"
        content = compose_path.read_text()
        cur_ports = parse_service_ports(compose_path)

        # Build replacement list: (old_host, container_spec, target_host)
        # Keyed on (old_host, container_spec) so duplicates are deduplicated.
        replacements: dict[tuple[str, str], str] = {}

        # 1. Services that exist in the reference → use ref host as base
        for svc, ref_mappings in ref_ports.items():
            if svc not in cur_ports:
                continue
            cur_mappings = cur_ports[svc]
            for i, (ref_host, ref_container) in enumerate(ref_mappings):
                if i >= len(cur_mappings):
                    break
                cur_host, cur_container = cur_mappings[i]
                if cur_container != ref_container:
                    continue           # shape mismatch; leave alone
                target = str(int(ref_host) + offset)
                if target != cur_host:
                    replacements[(cur_host, cur_container)] = target

        # 2. Agent-added services (not in reference) → offset current host
        for svc, cur_mappings in cur_ports.items():
            if svc in ref_ports:
                continue
            for cur_host, cur_container in cur_mappings:
                target = str(int(cur_host) + offset)
                if target != cur_host:
                    replacements[(cur_host, cur_container)] = target

        # Apply replacements sequentially.  Each pattern uses (?<!\d) and (?!\d)
        # guards so "8080:8080" does not match inside "18080:8080", and the
        # replacement preserves whatever quoting style the file uses (quoted,
        # single-quoted, or bare).  Process longer host-port strings first to
        # avoid any theoretical overlap with shorter ones.
        changes: list[str] = []
        for (old_host, container_spec), target_host in sorted(
            replacements.items(), key=lambda item: -len(item[0][0])
        ):
            pat = re.compile(
                r"(?<!\d)" + re.escape(old_host) + ":" + re.escape(container_spec) + r"(?!\d)"
            )
            new_val = f"{target_host}:{container_spec}"
            if pat.search(content):
                content = pat.sub(new_val, content)
                changes.append(f"  {old_host}:{container_spec:>10s}  ->  {new_val}")

        # Strip container_name lines the agent may have added
        content = re.sub(r"^\s*container_name:.*\n", "", content, flags=re.MULTILINE)

        compose_path.write_text(content)

        # .env — namespaces containers, networks, and volumes automatically
        project_name = dir_path.name.replace("-", "_")
        (dir_path / ".env").write_text(f"COMPOSE_PROJECT_NAME={project_name}\n")

        # Collect final ports for collision check
        final = parse_service_ports(compose_path)
        all_host_ports[dir_path.name] = {int(h) for svc_ports in final.values() for h, _ in svc_ports}

        # Print summary
        print(f"{dir_path.name}  (offset +{offset}):")
        if changes:
            for c in changes:
                print(c)
        else:
            print("  (no changes needed)")
        print()

    # ── collision verification ───────────────────────────────────────────
    print("─" * 60)
    print("Collision check:")
    names = list(all_host_ports)
    collisions: list[tuple[str, str, set[int]]] = []
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            shared = all_host_ports[names[i]] & all_host_ports[names[j]]
            if shared:
                collisions.append((names[i], names[j], shared))
    if collisions:
        print("  WARNING — port collisions remain:")
        for a, b, ports in collisions:
            print(f"    {a} <-> {b}  shared ports: {sorted(ports)}")
    else:
        print("  OK — no collisions across directories.")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("root", type=Path, help="Experiment root directory (e.g. exp/socialNetwork)")
    parser.add_argument("--reference", metavar="DIR", default=None,
                        help="Subdirectory to use as port reference (default: first alphabetically)")
    args = parser.parse_args()
    apply_offsets(args.root, args.reference)
