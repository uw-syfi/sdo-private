"""Filtered-log baseline coding agent for the SDS operator.

Provides a ``CodingAgent`` that replaces the RLM multi-turn loop with
deterministic heuristic log filtering (tail + error grep) followed by a
single-shot LLM call.  This isolates the variable being tested: RLM loop
vs. static filtering.

Register with ``provider = "filtered"`` in ``sds.toml``.
"""

import glob as globmod
import os
import re
from pathlib import Path
from typing import Optional

from app_operator.logger import logger
from app_operator.trajectory import TrajectoryRecorderProtocol

from .base import CodingAgent, register_provider
from .events import AgentEventHandler
from .rlm_agent import _litellm_call_with_retry, _FILE_GEN_RE, _DIRECT_TEXT_RE

# ---------------------------------------------------------------------------
# Error-line patterns (case-insensitive)
# ---------------------------------------------------------------------------
_ERROR_PATTERNS = re.compile(
    r"(?i)"
    r"(?:error|failed|fatal|exception|traceback|panic"
    r"|cannot|not found|denied|timeout|refused"
    r"|exit code [1-9]|exit status [1-9])"
)


# ---------------------------------------------------------------------------
# Public helper
# ---------------------------------------------------------------------------
def filter_log(
    log_text: str,
    tail_lines: int = 200,
    max_error_lines: int = 100,
) -> str:
    """Return a filtered view of *log_text* suitable for a single LLM call.

    The output has two clearly delimited sections:

    1. **Error lines** – lines from the *head* of the log (i.e. lines that
       precede the tail window) matching common error patterns.  Capped at
       *max_error_lines*.
    2. **Tail lines** – the last *tail_lines* lines of the log, included
       verbatim so the model always sees recent context.

    Returns ``""`` for empty / whitespace-only input.
    """
    if not log_text or not log_text.strip():
        return ""

    lines = log_text.splitlines()
    total = len(lines)

    # Tail: always include last `tail_lines` lines
    tail_start = max(0, total - tail_lines)
    tail_section = lines[tail_start:]

    # Error grep: only from lines *before* the tail window
    head_lines = lines[:tail_start]
    error_lines = [ln for ln in head_lines if _ERROR_PATTERNS.search(ln)]
    error_lines = error_lines[:max_error_lines]

    parts: list[str] = []

    if error_lines:
        parts.append("=== ERROR LINES FROM LOG ===")
        parts.extend(error_lines)
        parts.append("")  # blank separator

    parts.append(f"=== LAST {len(tail_section)} LINES OF LOG ===")
    parts.extend(tail_section)

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# FILE: section parser – broader than the RLM variant (allows any path)
# ---------------------------------------------------------------------------
_FILE_SECTION_RE = re.compile(
    r"FILE:\s*(\S+)\s*\n```[^\n]*\n(.*?)```",
    re.DOTALL,
)


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------
@register_provider("filtered")
class FilteredCodingAgent(CodingAgent):
    """Baseline agent: heuristic log filtering + single-shot LLM call.

    Uses the same model and constructor signature as ``RLMCodingAgent`` so
    they can be swapped via ``provider = "filtered"`` in ``sds.toml``.
    """

    def __init__(
        self,
        model: Optional[str] = None,
        recorder: Optional[TrajectoryRecorderProtocol] = None,
        event_handler: Optional[AgentEventHandler] = None,
        location: Optional[str] = None,
    ):
        self.model = model or "vertex_ai/gemini-2.0-flash"
        self.recorder = recorder
        self.event_handler = event_handler
        self.location = location
        self._total_token_usage: dict = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }

    # -- routing ------------------------------------------------------------

    def generate(
        self,
        prompt: str,
        cwd: Optional[str] = None,
        timeout: int = 300,
        silent: bool = False,
    ) -> str:
        repo_path = Path(cwd) if cwd else Path.cwd()
        call_tokens: dict = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }

        if _DIRECT_TEXT_RE.search(prompt):
            result = self._generate_direct(prompt, call_tokens)
        elif _FILE_GEN_RE.search(prompt):
            result = self._generate_files(prompt, repo_path, call_tokens)
        else:
            result = self._generate_fix(prompt, repo_path, call_tokens)

        for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
            self._total_token_usage[k] += call_tokens[k]

        if self.recorder and hasattr(self.recorder, "record_token_usage"):
            self.recorder.record_token_usage(self._total_token_usage.copy())

        return result

    # -- direct text (duplicated from rlm_agent for independence) -----------

    def _generate_direct(
        self, prompt: str, token_acc: Optional[dict] = None
    ) -> str:
        kwargs = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "cache": {"no-cache": True},
        }
        location = self.location or os.environ.get("VERTEX_LOCATION")
        if location:
            kwargs["vertex_location"] = location

        try:
            return _litellm_call_with_retry(
                kwargs, label="direct text generation", token_acc=token_acc
            )
        except Exception as e:
            logger.error(f"[Filtered] Direct text LLM call failed: {e}")
            return f"LLM call failed: {e}"

    # -- file generation (duplicated from rlm_agent for independence) -------

    def _generate_files(
        self,
        prompt: str,
        repo_path: Path,
        token_acc: Optional[dict] = None,
    ) -> str:
        expected_files = re.findall(r"\.sds/[\w._-]+", prompt)

        system_msg = (
            "You are a deployment assistant. The user will ask you to generate "
            "one or more files. For EACH file, output a section in this exact format:\n\n"
            "FILE: .sds/<filename>\n"
            "```\n"
            "<file content here>\n"
            "```\n\n"
            "Output ONLY these sections. Do not add explanations outside the sections."
        )

        kwargs = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_msg},
                {"role": "user", "content": prompt},
            ],
            "cache": {"no-cache": True},
        }
        location = self.location or os.environ.get("VERTEX_LOCATION")
        if location:
            kwargs["vertex_location"] = location

        try:
            raw = _litellm_call_with_retry(
                kwargs, label="file generation", token_acc=token_acc
            )
        except Exception as e:
            logger.error(f"[Filtered] Direct LLM call failed: {e}")
            return f"LLM call failed: {e}"

        written: list[str] = []
        file_sections = re.findall(
            r"FILE:\s*(\.sds/[\w._-]+)\s*\n```[^\n]*\n(.*?)```",
            raw,
            re.DOTALL,
        )
        for rel_path, content in file_sections:
            out_path = repo_path / rel_path
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(content)
            logger.info(f"[Filtered] Wrote {out_path}")
            written.append(rel_path)

        if not written and len(expected_files) == 1:
            out_path = repo_path / expected_files[0]
            content = re.sub(
                r"^```[^\n]*\n|```$", "", raw.strip(), flags=re.MULTILINE
            )
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(content)
            logger.info(f"[Filtered] Wrote {out_path} (fallback)")
            written.append(expected_files[0])

        return raw

    # -- baseline fix path --------------------------------------------------

    def _generate_fix(
        self,
        prompt: str,
        repo_path: Path,
        token_acc: Optional[dict] = None,
    ) -> str:
        """Single-shot fix: filter logs heuristically, call LLM once."""
        sds = repo_path / ".sds"
        deploy_sh = sds / "deploy.sh"
        backup = sds / "deploy.sh.bak"

        # 1. Backup for backtracking (same logic as RLM agent)
        if deploy_sh.exists() and not backup.exists():
            try:
                backup.write_text(deploy_sh.read_text())
                logger.info(
                    f"[Filtered] Saved deploy.sh backup: {backup}"
                )
            except Exception as e:
                logger.warning(
                    f"[Filtered] Could not save deploy.sh backup: {e}"
                )

        # 2. Find and filter latest logs
        logs_dir = sds / "logs"
        deploy_log = self._find_latest_log(logs_dir, "deploy")
        hc_log = self._find_latest_log(logs_dir, "health_check")
        filtered_deploy = filter_log(deploy_log)
        filtered_hc = filter_log(hc_log)

        # 3. Read context files
        context_files = {
            "deploy.sh": self._read(deploy_sh),
            "deploy.sh.bak (original)": self._read(backup),
            "docker-compose.yml": (
                self._read(repo_path / "docker-compose.yml")
                or self._read(repo_path / "docker-compose.yaml")
            ),
            "code_analysis.md": self._read(sds / "code_analysis.md"),
            "fix_summary.md": self._read(sds / "fix_summary.md"),
        }

        # 4. Build augmented prompt
        augmented_parts = [prompt, ""]

        if filtered_deploy:
            augmented_parts.append("--- FILTERED DEPLOYMENT LOG ---")
            augmented_parts.append(filtered_deploy)
            augmented_parts.append("")

        if filtered_hc:
            augmented_parts.append("--- FILTERED HEALTH CHECK LOG ---")
            augmented_parts.append(filtered_hc)
            augmented_parts.append("")

        for label, content in context_files.items():
            if content:
                augmented_parts.append(f"--- {label} ---")
                augmented_parts.append(content)
                augmented_parts.append("")

        augmented_prompt = "\n".join(augmented_parts)

        # 5. System message
        system_msg = (
            "You are a deployment-fixing assistant. You are given a deployment "
            "error description, filtered log output, and relevant context files.\n\n"
            "Analyze the errors and produce fixes. For EACH file you want to "
            "create or modify, output a section in this exact format:\n\n"
            "FILE: <relative-path>\n"
            "```\n"
            "<complete file content>\n"
            "```\n\n"
            "After all FILE sections, provide a brief summary of changes "
            "inside <summary> tags:\n"
            "<summary>\n"
            "- description of fix 1\n"
            "- description of fix 2\n"
            "</summary>\n\n"
            "IMPORTANT:\n"
            "- Include the COMPLETE file content, not just the changed parts.\n"
            "- You may fix files outside .sds/ (e.g. docker-compose.yml).\n"
            "- If deploy.sh.bak is provided, compare it with the current "
            "deploy.sh to avoid repeating previous failed changes."
        )

        kwargs = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_msg},
                {"role": "user", "content": augmented_prompt},
            ],
            "cache": {"no-cache": True},
        }
        location = self.location or os.environ.get("VERTEX_LOCATION")
        if location:
            kwargs["vertex_location"] = location

        # 6. Single LLM call
        try:
            raw = _litellm_call_with_retry(
                kwargs, label="filtered fix", token_acc=token_acc
            )
        except Exception as e:
            logger.error(f"[Filtered] Fix LLM call failed: {e}")
            return f"LLM call failed: {e}"

        # 7. Parse FILE: sections and write to disk
        for rel_path, content in _FILE_SECTION_RE.findall(raw):
            out_path = repo_path / rel_path
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(content)
            logger.info(f"[Filtered] Wrote {out_path}")

        return raw

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _find_latest_log(logs_dir: Path, prefix: str) -> str:
        """Return the content of the most recently modified log matching *prefix*.

        Returns ``""`` if no matching file is found or *logs_dir* does not exist.
        """
        if not logs_dir.is_dir():
            return ""

        candidates = sorted(
            globmod.glob(str(logs_dir / f"{prefix}*")),
            key=os.path.getmtime,
        )
        if not candidates:
            return ""

        try:
            return Path(candidates[-1]).read_text()
        except Exception:
            return ""

    @staticmethod
    def _read(path: Path) -> str:
        """Read *path* and return its text, or ``""`` on any error."""
        try:
            if path.exists():
                return path.read_text()
        except Exception:
            pass
        return ""
