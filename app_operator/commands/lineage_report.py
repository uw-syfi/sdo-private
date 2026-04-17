"""Render and validate GEPA-style lineage metadata for optimized prompts."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    import argparse

_CANDIDATE_DIR_RE = re.compile(r"^eval_\d+_c\d+$")


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Add arguments for lineage-report command."""
    default_optimized_dir = Path(__file__).resolve().parent.parent / "prompts" / "optimized"
    parser.add_argument(
        "--optimized-dir",
        type=Path,
        default=default_optimized_dir,
        help=f"Root optimized directory (default: {default_optimized_dir})",
    )
    parser.add_argument(
        "--family",
        type=str,
        required=True,
        help="Experiment family under optimized dir (e.g., simple_LLM)",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Return non-zero when validation issues are found",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print JSON report instead of text",
    )


def _sha256_file(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _candidate_id(family: str, candidate_ref: str) -> str:
    if "/" in candidate_ref:
        return candidate_ref
    return f"{family}/{candidate_ref}"


def _load_json(path: Path) -> dict[str, Any]:
    with open(path) as f:
        return json.load(f)


def _extract_selected_candidate_id(family: str, metadata: dict[str, Any]) -> str | None:
    lineage: Any = metadata.get("lineage", {})
    if isinstance(lineage, dict):
        lineage_dict = cast("dict[str, Any]", lineage)
        selected = str(lineage_dict.get("selected_candidate_id") or "").strip()
        if selected:
            return _candidate_id(family, selected)

    iteration = metadata.get("iteration")
    best_idx = metadata.get("best_candidate_index")
    if isinstance(iteration, int) and isinstance(best_idx, int):
        return _candidate_id(family, f"eval_{iteration}_c{best_idx}")
    return None


def _extract_parent_ids(family: str, metadata: dict[str, Any]) -> list[str]:
    lineage: Any = metadata.get("lineage", {})
    parent_ids: list[str] = []
    if isinstance(lineage, dict):
        lineage_dict = cast("dict[str, Any]", lineage)
        raw_parents: Any = lineage_dict.get("parent_candidate_ids", [])
        if isinstance(raw_parents, list):
            raw_parents_list = cast("list[Any]", raw_parents)
            parent_ids.extend(str(p) for p in raw_parents_list if str(p).strip())
    if not parent_ids:
        recombination: Any = metadata.get("recombination", {})
        if isinstance(recombination, dict):
            recombination_dict = cast("dict[str, Any]", recombination)
            raw_parents = recombination_dict.get("parent_candidates", [])
            if isinstance(raw_parents, list):
                raw_parents_list = cast("list[Any]", raw_parents)
                parent_ids.extend(str(p) for p in raw_parents_list if str(p).strip())
    return [_candidate_id(family, parent) for parent in parent_ids]


def run_command(args: argparse.Namespace) -> int:
    """Execute lineage report generation and validation."""
    optimized_dir = Path(args.optimized_dir)
    family = args.family.strip()
    family_dir = optimized_dir / family
    issues: list[str] = []

    if not family_dir.exists():
        issues.append(f"Family directory not found: {family_dir}")
        report = {"family": family, "issues": issues, "versions": [], "events": 0}
        if args.json:
            print(json.dumps(report, indent=2))
        else:
            print(f"Family: {family}\nIssues:\n- " + "\n- ".join(issues))
        return 1 if args.strict else 0

    candidate_dirs = {d.name: d for d in family_dir.iterdir() if d.is_dir() and _CANDIDATE_DIR_RE.match(d.name)}

    lineage_file = family_dir / "lineage.jsonl"
    events = 0
    if lineage_file.exists():
        with open(lineage_file) as f:
            for line in f:
                if line.strip():
                    events += 1

    version_reports: list[dict[str, Any]] = []
    for metadata_file in sorted(family_dir.glob("v*/metadata.json")):
        version_dir = metadata_file.parent
        metadata = _load_json(metadata_file)
        selected_candidate_id = _extract_selected_candidate_id(family, metadata)
        parent_ids = _extract_parent_ids(family, metadata)
        version_issues: list[str] = []

        selected_candidate_dir = None
        selected_candidate_leaf = ""
        if selected_candidate_id:
            selected_candidate_leaf = selected_candidate_id.rsplit("/", 1)[-1]
            selected_candidate_dir = family_dir / selected_candidate_leaf
            if selected_candidate_leaf not in candidate_dirs:
                version_issues.append(
                    f"{version_dir.name}: selected candidate missing directory ({selected_candidate_id})"
                )
        else:
            version_issues.append(f"{version_dir.name}: missing selected candidate lineage information")

        for parent_id in parent_ids:
            parent_leaf = parent_id.rsplit("/", 1)[-1]
            if parent_leaf not in candidate_dirs:
                version_issues.append(f"{version_dir.name}: parent candidate missing directory ({parent_id})")

        module_hashes: dict[str, dict[str, str]] = {}
        prompts: Any = metadata.get("prompts", {})
        if isinstance(prompts, dict) and selected_candidate_dir is not None and selected_candidate_dir.exists():
            prompts_dict = cast("dict[str, Any]", prompts)
            for prompt_name in prompts_dict:
                candidate_module = selected_candidate_dir / f"{prompt_name}.dspy.json"
                promoted_module = version_dir / f"{prompt_name}.dspy.json"
                if not candidate_module.exists():
                    version_issues.append(f"{version_dir.name}: candidate module missing ({candidate_module.name})")
                    continue
                if not promoted_module.exists():
                    version_issues.append(f"{version_dir.name}: promoted module missing ({promoted_module.name})")
                    continue
                candidate_sha = _sha256_file(candidate_module)
                promoted_sha = _sha256_file(promoted_module)
                module_hashes[prompt_name] = {
                    "candidate_sha256": candidate_sha,
                    "promoted_sha256": promoted_sha,
                }
                if candidate_sha != promoted_sha:
                    version_issues.append(
                        f"{version_dir.name}: hash mismatch for {prompt_name} "
                        f"({selected_candidate_id} -> {version_dir.name})"
                    )

        version_reports.append(
            {
                "version": version_dir.name,
                "iteration": metadata.get("iteration"),
                "selected_candidate_id": selected_candidate_id,
                "parent_candidate_ids": parent_ids,
                "best_score": metadata.get("best_score"),
                "module_hashes": module_hashes,
                "issues": version_issues,
            }
        )
        issues.extend(version_issues)

    report = {
        "family": family,
        "lineage_file": str(lineage_file),
        "events": events,
        "candidate_count": len(candidate_dirs),
        "version_count": len(version_reports),
        "versions": version_reports,
        "issues": issues,
    }

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"Family: {family}")
        print(f"Lineage events: {events}")
        print(f"Candidates: {len(candidate_dirs)}")
        print(f"Versions: {len(version_reports)}")
        for version in version_reports:
            print(
                f"- {version['version']}: selected={version['selected_candidate_id']} "
                f"parents={version['parent_candidate_ids']} score={version['best_score']}"
            )
        if issues:
            print("Issues:")
            for issue in issues:
                print(f"- {issue}")
        else:
            print("Issues: none")

    if args.strict and issues:
        return 1
    return 0
