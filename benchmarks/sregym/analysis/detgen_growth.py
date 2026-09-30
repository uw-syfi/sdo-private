"""Incident-detector and playbook counts after every incident, from a chained workspace's git history.

    uv run python -m benchmarks.sregym.analysis.detgen_growth <workspace>

The broker commits `sdo(<incident>): validated operational memory` for each
incident; the last commit per incident is the memory state after it.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

_SUBJECT = re.compile(r"^sdo\((?P<incident>.+)\): validated operational memory$")


def _git(workspace: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(workspace), *args], check=True, capture_output=True, text=True).stdout


def memory_after_each_incident(workspace: Path) -> list[tuple[str, list[str], list[str]]]:
    last_commit: dict[str, str] = {}
    order: list[str] = []
    for line in _git(workspace, "log", "--reverse", "--format=%H %s").splitlines():
        sha, _, subject = line.partition(" ")
        match = _SUBJECT.match(subject)
        if match:
            incident = match.group("incident")
            if incident not in last_commit:
                order.append(incident)
            last_commit[incident] = sha
    rows = []
    for incident in order:
        files = _git(workspace, "ls-tree", "-r", "--name-only", last_commit[incident]).splitlines()
        detectors = sorted(
            {p.split("detectors/incidents/")[1].split("/")[0] for p in files if "detectors/incidents/" in p}
        )
        playbooks = sorted(
            {
                p.split(".sdo/playbooks/")[1].split("/")[0]
                for p in files
                if p.startswith(".sdo/playbooks/") and p.count("/") >= 3
            }
            - {"health-objective"}
        )
        rows.append((incident, detectors, playbooks))
    return rows


def main(argv: list[str] | None = None) -> int:
    workspace = Path((argv or sys.argv[1:])[0])
    for index, (incident, detectors, playbooks) in enumerate(memory_after_each_incident(workspace), 1):
        print(f"{index} {incident[-10:]} detectors={len(detectors)} {detectors} playbooks={len(playbooks)} {playbooks}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
