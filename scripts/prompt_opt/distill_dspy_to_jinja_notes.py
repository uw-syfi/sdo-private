#!/usr/bin/env python3
"""Summarize DSPy prompt artifacts into Jinja2 distillation notes.

This script is intentionally read-only with respect to source prompt templates.
It inspects optimized ``*.dspy.json`` artifacts and writes a Markdown report
that a human can use when deciding what, if anything, to fold back into Jinja2.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jinja2 import Environment, StrictUndefined

_GENERIC_LINE_PATTERNS = (
    re.compile(r"^perform the .+ task\.?$", re.IGNORECASE),
    re.compile(r"^be (careful|thorough|concise)\.?$", re.IGNORECASE),
    re.compile(r"^analyze .+ and fix .+\.?$", re.IGNORECASE),
)


@dataclass(frozen=True)
class CandidateRecord:
    """One DSPy candidate instruction found in an optimized prompt family."""

    version: str
    candidate_id: str
    operator: str
    score: float | None
    success_rate: float | None
    classification_counts: dict[str, int]
    instruction: str
    selected: bool


@dataclass(frozen=True)
class ProposalArtifact:
    """Files produced for one proposed Jinja2 prompt variant."""

    name: str
    template_path: Path
    dspy_path: Path
    metadata_path: Path


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def _module_instruction(path: Path) -> str:
    if not path.exists():
        return ""
    state = _load_json(path)
    instruction = state.get("optimized_instruction")
    return instruction.strip() if isinstance(instruction, str) else ""


def _candidate_leaf(candidate_id: str, fallback: str) -> str:
    if not candidate_id:
        return fallback
    return candidate_id.rsplit("/", 1)[-1]


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    return None


def _as_counts(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    counts: dict[str, int] = {}
    for key, count in value.items():
        if isinstance(count, bool):
            continue
        if isinstance(count, int | float):
            counts[str(key)] = int(count)
    return counts


def collect_candidate_records(family_dir: Path, prompt_name: str) -> list[CandidateRecord]:
    """Collect candidate instructions from a versioned optimized prompt family."""

    records: list[CandidateRecord] = []
    seen: set[tuple[str, str]] = set()

    for metadata_path in sorted(family_dir.glob("v*/metadata.json")):
        metadata = _load_json(metadata_path)
        version = metadata_path.parent.name
        lineage = metadata.get("lineage") if isinstance(metadata.get("lineage"), dict) else {}
        selected_candidate_id = str(lineage.get("selected_candidate_id") or "")
        candidate_nodes = lineage.get("candidate_nodes")

        if isinstance(candidate_nodes, list):
            for node in candidate_nodes:
                if not isinstance(node, dict):
                    continue
                candidate_id = str(node.get("candidate_id") or "")
                candidate_tag = _candidate_leaf(candidate_id, str(node.get("candidate_tag") or ""))
                instruction = _module_instruction(family_dir / candidate_tag / f"{prompt_name}.dspy.json")
                if not instruction:
                    continue

                key = (candidate_id or f"{version}/{candidate_tag}", instruction)
                if key in seen:
                    continue
                seen.add(key)
                records.append(
                    CandidateRecord(
                        version=version,
                        candidate_id=candidate_id or f"{version}/{candidate_tag}",
                        operator=str(node.get("operator") or ""),
                        score=_as_float(node.get("score")),
                        success_rate=_as_float(node.get("success_rate")),
                        classification_counts=_as_counts(node.get("classification_counts")),
                        instruction=instruction,
                        selected=candidate_id == selected_candidate_id,
                    )
                )

        version_instruction = _module_instruction(metadata_path.parent / f"{prompt_name}.dspy.json")
        if version_instruction:
            key = (f"{version}/promoted", version_instruction)
            if key not in seen:
                seen.add(key)
                records.append(
                    CandidateRecord(
                        version=version,
                        candidate_id=f"{version}/promoted",
                        operator="promoted",
                        score=_as_float(metadata.get("best_score")),
                        success_rate=None,
                        classification_counts={},
                        instruction=version_instruction,
                        selected=True,
                    )
                )

    return records


def _instruction_fragments(instruction: str) -> list[str]:
    raw_fragments = re.split(r"(?:\n+|(?<=[.!?])\s+|^\s*[-*]\s+|\s+\d+\.\s+)", instruction)
    fragments: list[str] = []
    for fragment in raw_fragments:
        normalized = re.sub(r"\s+", " ", fragment.strip(" -\t\r\n"))
        if len(normalized) < 25:
            continue
        if any(pattern.match(normalized) for pattern in _GENERIC_LINE_PATTERNS):
            continue
        fragments.append(normalized)
    return fragments


def rank_reusable_fragments(records: list[CandidateRecord], limit: int = 15) -> list[tuple[str, int, float | None]]:
    """Rank repeated or high-scoring instruction fragments for human review."""

    stats: dict[str, tuple[str, int, float | None]] = {}
    for record in records:
        for fragment in _instruction_fragments(record.instruction):
            key = fragment.lower()
            current = stats.get(key)
            if current is None:
                stats[key] = (fragment, 1, record.score)
                continue
            display, count, best_score = current
            score = record.score
            if score is not None and (best_score is None or score > best_score):
                best_score = score
                display = fragment
            stats[key] = (display, count + 1, best_score)

    ranked = sorted(
        stats.values(),
        key=lambda item: (item[1], item[2] if item[2] is not None else -1.0, item[0]),
        reverse=True,
    )
    return ranked[:limit]


def _format_score(score: float | None) -> str:
    return "n/a" if score is None else f"{score:.5f}"


def _format_counts(counts: dict[str, int]) -> str:
    if not counts:
        return "{}"
    return ", ".join(f"{key}={value}" for key, value in sorted(counts.items()))


def _signature_name(prompt_name: str) -> str:
    try:
        from app_operator.dspy_integration.signatures import get_signature

        return get_signature(prompt_name).__name__
    except Exception:
        return prompt_name


def _generalize_fragment(fragment: str) -> str:
    """Lightly reduce run-specific wording while preserving concrete guidance."""

    generalized = re.sub(r",?\s+as seen in previous failures like `[^`]+`", "", fragment, flags=re.IGNORECASE)
    generalized = re.sub(r"\brecurring `[^`]+` error\b", "observed deployment error", generalized, flags=re.IGNORECASE)
    generalized = re.sub(r"\bGiven the repeated `[^`]+` errors,?\s*", "", generalized, flags=re.IGNORECASE)
    generalized = generalized.replace("like `.env`", "such as required environment/configuration files")
    generalized = re.sub(r"\s+", " ", generalized).strip()
    return generalized


def build_proposal_texts(seed_text: str, records: list[CandidateRecord], count: int) -> list[tuple[str, str]]:
    """Build proposed Jinja2 prompt bodies from ranked DSPy instructions."""

    seed = seed_text.strip()
    proposals: list[tuple[str, str]] = []

    reusable_fragments = []
    for fragment, _count, _score in rank_reusable_fragments(records, limit=6):
        generalized = _generalize_fragment(fragment)
        if generalized and generalized not in reusable_fragments:
            reusable_fragments.append(generalized)

    if reusable_fragments:
        distilled_lines = [
            seed,
            "",
            "Distilled guidance from optimized candidates:",
            *[f"- {fragment}" for fragment in reusable_fragments[:4]],
        ]
        proposals.append(("distilled", "\n".join(distilled_lines).strip() + "\n"))

    ranked_records = sorted(
        records,
        key=lambda r: (
            r.selected,
            r.score if r.score is not None else -1.0,
            r.success_rate if r.success_rate is not None else -1.0,
        ),
        reverse=True,
    )
    for record in ranked_records:
        if len(proposals) >= count:
            break
        if any(pattern.match(record.instruction.strip()) for pattern in _GENERIC_LINE_PATTERNS):
            continue
        guidance = _generalize_fragment(record.instruction.strip())
        proposal_text = f"{seed}\n\nCandidate-derived guidance:\n- {guidance}\n"
        if proposal_text not in [existing for _name, existing in proposals]:
            proposals.append((f"candidate_{len(proposals) + 1}", proposal_text))

    return proposals[:count]


def _render_static_jinja(template_text: str) -> str:
    env = Environment(undefined=StrictUndefined, trim_blocks=True, lstrip_blocks=True, keep_trailing_newline=True)
    return env.from_string(template_text).render()


def write_proposals(
    *,
    proposals_dir: Path,
    prompt_name: str,
    seed_template: Path,
    family_dir: Path,
    records: list[CandidateRecord],
    count: int,
) -> list[ProposalArtifact]:
    """Write proposed Jinja2 prompts and equivalent DSPy module states."""

    seed_text = seed_template.read_text()
    proposal_texts = build_proposal_texts(seed_text, records, count)
    signature = _signature_name(prompt_name)
    artifacts: list[ProposalArtifact] = []

    for index, (proposal_name, template_text) in enumerate(proposal_texts, start=1):
        artifact_name = f"proposal_{index:03d}_{proposal_name}"
        artifact_dir = proposals_dir / artifact_name
        artifact_dir.mkdir(parents=True, exist_ok=True)

        template_path = artifact_dir / f"{prompt_name}.jinja2"
        dspy_path = artifact_dir / f"{prompt_name}.dspy.json"
        metadata_path = artifact_dir / "metadata.json"

        rendered_instruction = _render_static_jinja(template_text).strip()
        template_path.write_text(template_text)
        dspy_path.write_text(
            json.dumps(
                {
                    "prompt_name": prompt_name,
                    "signature": signature,
                    "demos": [],
                    "optimized_instruction": rendered_instruction,
                },
                indent=2,
            )
            + "\n"
        )
        metadata_path.write_text(
            json.dumps(
                {
                    "method": "dspy_to_jinja_proposal",
                    "prompt_name": prompt_name,
                    "source_family_dir": str(family_dir),
                    "source_seed_template": str(seed_template),
                    "proposal_name": proposal_name,
                    "template_file": template_path.name,
                    "dspy_file": dspy_path.name,
                },
                indent=2,
            )
            + "\n"
        )
        artifacts.append(
            ProposalArtifact(
                name=artifact_name,
                template_path=template_path,
                dspy_path=dspy_path,
                metadata_path=metadata_path,
            )
        )

    return artifacts


def render_report(
    *,
    family_dir: Path,
    prompt_name: str,
    seed_template: Path | None,
    records: list[CandidateRecord],
) -> str:
    lines: list[str] = [
        f"# DSPy-to-Jinja Distillation Notes: `{prompt_name}`",
        "",
        f"- Optimized family: `{family_dir}`",
        "- Source seed template was not modified.",
    ]
    if seed_template is not None:
        lines.append(f"- Seed template: `{seed_template}`")
    lines.extend(
        [
            "",
            "## Candidate Summary",
            "",
            "| Version | Candidate | Operator | Selected | Score | Success Rate | Classifications |",
            "| --- | --- | --- | --- | ---: | ---: | --- |",
        ]
    )

    for record in sorted(records, key=lambda r: (r.version, r.candidate_id)):
        success_rate = "n/a" if record.success_rate is None else f"{record.success_rate:.3f}"
        lines.append(
            "| "
            f"{record.version} | `{record.candidate_id}` | {record.operator or 'n/a'} | "
            f"{'yes' if record.selected else 'no'} | {_format_score(record.score)} | "
            f"{success_rate} | {_format_counts(record.classification_counts)} |"
        )

    lines.extend(["", "## Reusable Candidate Fragments", ""])
    fragments = rank_reusable_fragments(records)
    if fragments:
        for fragment, count, best_score in fragments:
            lines.append(f"- ({count}x, best score {_format_score(best_score)}) {fragment}")
    else:
        lines.append("- No non-generic reusable fragments found.")

    lines.extend(["", "## Instructions For Review", ""])
    for record in sorted(records, key=lambda r: (r.score if r.score is not None else -1.0), reverse=True):
        lines.extend(
            [
                f"### {record.candidate_id}",
                "",
                f"- Version: `{record.version}`",
                f"- Operator: `{record.operator or 'n/a'}`",
                f"- Score: `{_format_score(record.score)}`",
                f"- Selected: `{'yes' if record.selected else 'no'}`",
                "",
                "```text",
                record.instruction,
                "```",
                "",
            ]
        )

    return "\n".join(lines).rstrip() + "\n"


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Read DSPy optimized prompt artifacts and emit Markdown notes for "
            "manual Jinja2 distillation. Source templates are never edited."
        )
    )
    parser.add_argument("--family-dir", required=True, type=Path, help="Optimized prompt family directory")
    parser.add_argument("--prompt", required=True, help="Prompt name, e.g. rlm_deployer_fix_error")
    parser.add_argument("--seed-template", type=Path, help="Optional seed template path to mention in the report")
    parser.add_argument("--output", type=Path, help="Optional Markdown output path. Defaults to stdout.")
    parser.add_argument(
        "--proposals-dir",
        type=Path,
        help=(
            "Optional directory for proposed Jinja2 prompts and equivalent DSPy "
            "module states. Source templates are not modified."
        ),
    )
    parser.add_argument("--proposal-count", type=int, default=3, help="Number of proposal variants to write")
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()
    family_dir = args.family_dir.resolve()
    if not family_dir.exists():
        raise SystemExit(f"family dir does not exist: {family_dir}")

    seed_template = args.seed_template.resolve() if args.seed_template else None
    if seed_template is not None and not seed_template.exists():
        raise SystemExit(f"seed template does not exist: {seed_template}")

    records = collect_candidate_records(family_dir, args.prompt)
    if not records:
        raise SystemExit(f"no records found for prompt {args.prompt!r} in {family_dir}")

    proposal_artifacts: list[ProposalArtifact] = []
    if args.proposals_dir:
        if seed_template is None:
            raise SystemExit("--seed-template is required when --proposals-dir is set")
        if args.proposal_count < 1:
            raise SystemExit("--proposal-count must be >= 1")
        proposal_artifacts = write_proposals(
            proposals_dir=args.proposals_dir.resolve(),
            prompt_name=args.prompt,
            seed_template=seed_template,
            family_dir=family_dir,
            records=records,
            count=args.proposal_count,
        )

    report = render_report(
        family_dir=family_dir,
        prompt_name=args.prompt,
        seed_template=seed_template,
        records=records,
    )
    if args.output:
        output = args.output.resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(report)
        print(output)
    else:
        print(report, end="")
    for artifact in proposal_artifacts:
        print(f"{artifact.name}: {artifact.template_path} -> {artifact.dspy_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
