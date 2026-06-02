"""JSONL trajectory store + the recorder that feeds it.

One JSONL file per run. Line 0 is a ``meta`` record (app / problem_id / model /
task), the body is one ``event`` record per agent event in arrival order, and an
optional trailing ``summary`` record holds the outcome. The format is
append-only and torn-write tolerant: a run that crashes mid-stream still leaves
a readable prefix, and a reader skips any unparseable line.

The recorder implements the ``agentshim`` event-handler protocol
(``on_thinking`` / ``on_tool_call`` / ``on_tool_result`` / ``on_usage``) so it
can be attached to a ``CodingAgent`` and capture the live run with no changes to
the agent itself. It never raises into the agent: a recording failure is logged
and swallowed.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any

logger = logging.getLogger(__name__)

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(name: str) -> str:
    """Filesystem-safe slug (``hotelReservation`` → ``hotelreservation``)."""
    slug = _SLUG_RE.sub("-", (name or "").strip().lower()).strip("-")
    return slug or "unknown"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _run_id(problem_id: str, ts: str, pid: int) -> str:
    """Short, non-identifying id for a run (problem slugs would leak the answer)."""
    import hashlib

    digest = hashlib.sha1(f"{problem_id}|{ts}|{pid}".encode()).hexdigest()
    return digest[:16]


@dataclass
class TrajectoryMeta:
    """Header record identifying a single run.

    ``problem_id`` is the benchmark's ground-truth label and is **answer-leaking**
    (e.g. ``wrong_service_selector_...``); together with ``provider`` / ``model``
    it is written only to the analysis-only sidecar index, never into the
    agent-readable trajectory file. The file carries only :meth:`redacted` fields
    (``app`` — observable from the cluster anyway — plus ``ts`` and a ``run_id``
    hash). ``run_id`` is assigned by the store at :meth:`TrajectoryStore.open_run`.
    """

    app: str
    ts: str
    problem_id: str = ""
    model: str = ""
    provider: str = ""
    task: str = ""
    run_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def redacted(self) -> dict[str, Any]:
        """The agent-safe subset written into the trajectory file (no answer leak)."""
        return {"app": self.app, "ts": self.ts, "run_id": self.run_id}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> TrajectoryMeta:
        known = set(cls.__dataclass_fields__)  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in raw.items() if k in known})


@dataclass
class TrajectoryEvent:
    """One recorded agent event.

    ``kind`` is one of ``thinking`` | ``tool_call`` | ``tool_result`` | ``usage``.
    ``seq`` is a per-run monotonic counter. The remaining fields are populated
    per kind (a ``thinking`` event uses ``text``; a ``tool_call`` uses ``tool`` /
    ``args``; etc.).
    """

    seq: int
    kind: str
    text: str = ""
    tool: str = ""
    args: str = ""
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    usage: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> TrajectoryEvent:
        known = set(cls.__dataclass_fields__)  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in raw.items() if k in known})


@dataclass
class TrajectoryFindings:
    """Agent-authored structured summary of a run, recorded via the
    ``record_findings`` tool near the end of an investigation.

    These symptom-register fields (mirroring ``memory.extract``'s lesson schema)
    become the retrieval-facing :attr:`TrajectoryDigest.text`, so a future
    symptom-shaped query matches them — unlike the raw tool mechanics the digest
    falls back to. Every field is the agent's own words: no ``problem_id`` enters
    here, so findings are leak-safe like the rest of the file.
    """

    situation: str = ""
    root_cause: str = ""
    tell: str = ""
    fix: str = ""
    affected_resource: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def has_content(self) -> bool:
        return any(str(v).strip() for v in self.to_dict().values())

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> TrajectoryFindings:
        known = set(cls.__dataclass_fields__)  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in raw.items() if k in known})


@dataclass
class TrajectoryDigest:
    """Compact, retrieval-facing view of one trajectory.

    Both retrieval backends operate on digests, never the raw file: the LLM
    searcher ranks over ``text`` and RAG embeds ``text``. ``read_full`` on the
    store fetches the unedited trajectory once a digest is selected.
    """

    app: str
    run_id: str
    ts: str
    path: str
    outcome: str
    tool_calls: list[str]
    text: str


# Per-kind cap so one chatty run can't bloat the store / a digest.
_MAX_TEXT = 4000
_MAX_TOOL_OUT = 2000


def _truncate(s: str, limit: int) -> str:
    if s is None:
        return ""
    s = str(s)
    return s if len(s) <= limit else s[:limit] + "…[truncated]"


class TrajectoryWriter:
    """Append-only writer for one run's JSONL file. Thread-safe.

    The run is written to a ``.jsonl.partial`` staging file and atomically
    committed to its final ``.jsonl`` path only on :meth:`close`. Readers glob
    ``*.jsonl``, so an in-flight run is invisible to retrieval / directory
    browsing — an agent never sees its own partial run, and a hard crash leaves
    a ``.partial`` that is simply ignored rather than a torn ``.jsonl``.
    """

    def __init__(self, path: Path, meta: TrajectoryMeta) -> None:
        self._path = path
        self._partial = Path(str(path) + ".partial")
        self._lock = threading.Lock()
        self._seq = 0
        self._closed = False
        self._partial.parent.mkdir(parents=True, exist_ok=True)
        # Truncate any stale partial and write the REDACTED meta header — the
        # answer-leaking problem_id / provider / model never enter the file.
        with self._partial.open("w", encoding="utf-8") as fh:
            fh.write(json.dumps({"type": "meta", **meta.redacted()}) + "\n")

    @property
    def path(self) -> Path:
        """The final (committed) path — valid as a stable id even before close."""
        return self._path

    def _append(self, record: dict[str, Any]) -> None:
        if self._closed:
            return
        try:
            line = json.dumps(record, default=str)
        except (TypeError, ValueError):
            logger.exception("trajectory: failed to serialize event; skipping")
            return
        with self._lock, self._partial.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    def event(self, event: TrajectoryEvent) -> None:
        self._append({"type": "event", **event.to_dict()})

    def findings(self, findings: TrajectoryFindings) -> None:
        """Append the agent's structured findings for this run (last write wins).

        Written mid-run via the ``record_findings`` tool and made durable on
        :meth:`close` like every other record. Per-field truncation keeps a
        verbose agent from bloating the digest."""
        record = {k: _truncate(v, _MAX_TEXT) for k, v in findings.to_dict().items()}
        self._append({"type": "findings", **record})

    def next_seq(self) -> int:
        with self._lock:
            self._seq += 1
            return self._seq

    def close(self, *, outcome: str = "", elapsed_s: float | None = None) -> None:
        if self._closed:
            return
        self._append(
            {
                "type": "summary",
                "outcome": outcome,
                "elapsed_s": elapsed_s,
                "events": self._seq,
                "closed_at": _now(),
            }
        )
        self._closed = True
        # Atomically publish the completed run into the browseable store.
        try:
            os.replace(self._partial, self._path)
        except OSError:
            logger.exception("trajectory: failed to commit %s", self._path)


class TrajectoryRecorder:
    """``agentshim`` event handler that streams a run into a ``TrajectoryWriter``.

    Attach via ``CodingAgent(event_handler=recorder, ...)``. Every callback is
    best-effort: an exception while recording is logged and swallowed so it can
    never abort the agent run it is observing.
    """

    def __init__(self, writer: TrajectoryWriter) -> None:
        self._writer = writer

    def _safe(self, event: TrajectoryEvent) -> None:
        try:
            self._writer.event(event)
        except Exception:  # never propagate into the agent
            logger.exception("trajectory: dropping event after recorder error")

    def on_thinking(self, text: str) -> None:
        if not text:
            return
        self._safe(TrajectoryEvent(seq=self._writer.next_seq(), kind="thinking", text=_truncate(text, _MAX_TEXT)))

    def on_tool_call(self, tool: str, args: dict[str, Any] | str | None = None) -> None:
        if isinstance(args, (dict, list)):
            args_text = json.dumps(args, default=str)
        else:
            args_text = "" if args is None else str(args)
        self._safe(
            TrajectoryEvent(
                seq=self._writer.next_seq(),
                kind="tool_call",
                tool=str(tool),
                args=_truncate(args_text, _MAX_TOOL_OUT),
            )
        )

    def on_tool_result(
        self,
        tool: str,
        stdout: str = "",
        stderr: str = "",
        exit_code: int | None = None,
        duration: float | None = None,
    ) -> None:
        self._safe(
            TrajectoryEvent(
                seq=self._writer.next_seq(),
                kind="tool_result",
                tool=str(tool),
                stdout=_truncate(stdout, _MAX_TOOL_OUT),
                stderr=_truncate(stderr, _MAX_TOOL_OUT),
                exit_code=exit_code,
            )
        )

    def on_usage(self, usage: dict[str, Any]) -> None:
        if not usage:
            return
        self._safe(TrajectoryEvent(seq=self._writer.next_seq(), kind="usage", usage=dict(usage)))


class TrajectoryStore:
    """JSONL-backed trajectory store rooted at ``store_dir``.

    Layout: ``{store_dir}/{app-slug}/{problem-slug}__{ts}.jsonl``. The store
    accumulates across runs — every run appends a new file — so it must be
    rooted outside any ephemeral per-problem workdir.
    """

    def __init__(self, store_dir: str | Path) -> None:
        self.store_dir = Path(store_dir)

    def app_dir(self, app: str) -> Path:
        """The directory holding one app's recorded runs (for filesystem browse)."""
        return self.store_dir / slugify(app)

    def _index_path(self, app: str) -> Path:
        """Analysis-only sidecar (run_id → problem_id/provider/model), kept OUTSIDE
        the browseable app dir and with a non-``.jsonl`` suffix so it is never
        listed, read, or retrieved as a trajectory."""
        return self.store_dir / ".index" / f"{slugify(app)}.index"

    def open_run(self, meta: TrajectoryMeta) -> TrajectoryWriter:
        """Begin recording a run; returns its writer.

        The agent-facing filename is a non-identifying ``run_id`` hash (problem id
        slugs would leak the answer to a retrieving agent), and the leaking
        identity is recorded only in the sidecar index.
        """
        run_id = _run_id(meta.problem_id, meta.ts, os.getpid())
        meta.run_id = run_id
        writer = TrajectoryWriter(self.app_dir(meta.app) / f"{run_id}.jsonl", meta)
        self._append_index(meta, writer.path.name)
        return writer

    def _append_index(self, meta: TrajectoryMeta, filename: str) -> None:
        path = self._index_path(meta.app)
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {**meta.to_dict(), "file": filename}
        try:
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, default=str) + "\n")
        except OSError:
            logger.exception("trajectory: failed to append sidecar index for %s", filename)

    def _iter_files(self, app: str | None, exclude: set[str] | None = None) -> list[Path]:
        if not self.store_dir.exists():
            return []
        root = self.store_dir / slugify(app) if app else self.store_dir
        if not root.exists():
            return []
        files = sorted(root.rglob("*.jsonl"))
        if exclude:
            files = [p for p in files if str(p) not in exclude and str(p.resolve()) not in exclude]
        return files

    @staticmethod
    def _read_records(
        path: Path,
    ) -> tuple[TrajectoryMeta | None, list[TrajectoryEvent], dict[str, Any], dict[str, Any] | None]:
        meta: TrajectoryMeta | None = None
        events: list[TrajectoryEvent] = []
        summary: dict[str, Any] = {}
        findings: dict[str, Any] | None = None
        try:
            with path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue  # torn / partial trailing line
                    rtype = rec.get("type")
                    if rtype == "meta":
                        meta = TrajectoryMeta.from_dict(rec)
                    elif rtype == "event":
                        events.append(TrajectoryEvent.from_dict(rec))
                    elif rtype == "summary":
                        summary = rec
                    elif rtype == "findings":
                        findings = rec  # last write wins
        except OSError:
            logger.warning("trajectory: could not read %s", path)
        return meta, events, summary, findings

    @staticmethod
    def _digest_text(
        meta: TrajectoryMeta,
        findings: dict[str, Any] | None,
        outcome: str,
        tool_calls: list[str],
        thinking: list[str],
    ) -> str:
        """The retrieval text. Prefer the agent's structured findings — their
        symptom register matches a symptom-shaped query — and fall back to run
        mechanics (outcome + tool sequence + a window of the agent's narration)
        when no findings were recorded. Either way no ``problem_id`` / ``task``
        enters (that would leak the answer); the signal comes from the recorded
        investigation itself. Kept compact so the embedder gets a focused vector
        and many digests fit at once."""
        if findings is not None:
            f = TrajectoryFindings.from_dict(findings)
            if f.has_content():
                parts = [
                    f"app: {meta.app}",
                    f"situation: {f.situation}" if f.situation else "",
                    f"tell: {f.tell}" if f.tell else "",
                    f"root_cause: {f.root_cause}" if f.root_cause else "",
                    f"fix: {f.fix}" if f.fix else "",
                    f"affected_resource: {f.affected_resource}" if f.affected_resource else "",
                ]
                return _truncate("\n".join(p for p in parts if p), _MAX_TEXT)

        parts = [
            f"app: {meta.app}",
            f"outcome: {outcome}" if outcome else "",
            f"tools: {', '.join(tool_calls)}" if tool_calls else "",
        ]
        if thinking:
            head = thinking[0]
            tail = thinking[-1] if len(thinking) > 1 else ""
            parts.append(f"reasoning: {head} … {tail}")
        return _truncate("\n".join(p for p in parts if p), _MAX_TEXT)

    def digest(self, path: Path) -> TrajectoryDigest | None:
        """Build the compact retrieval view for one trajectory file."""
        meta, events, summary, findings = self._read_records(path)
        if meta is None:
            return None
        tool_calls = [e.tool for e in events if e.kind == "tool_call" and e.tool]
        thinking = [e.text for e in events if e.kind == "thinking" and e.text]
        outcome = str(summary.get("outcome", "")) if summary else ""

        text = self._digest_text(meta, findings, outcome, tool_calls, thinking)

        return TrajectoryDigest(
            app=meta.app,
            run_id=meta.run_id,
            ts=meta.ts,
            path=str(path),
            outcome=outcome,
            tool_calls=tool_calls,
            text=text,
        )

    def digests(self, app: str | None = None, *, exclude: set[str] | None = None) -> list[TrajectoryDigest]:
        """All digests, optionally scoped to one app, newest last.

        ``exclude`` is a set of file paths to skip — used to hide the current,
        in-flight run from a retrieval server reading the same store.
        """
        out: list[TrajectoryDigest] = []
        for path in self._iter_files(app, exclude=exclude):
            d = self.digest(path)
            if d is not None:
                out.append(d)
        return out

    def narrative(self, path: str | Path, *, budget: int = 3000) -> str:
        """A medium-detail view for the LLM searcher: the agent's reasoning, the
        tool sequence, and the outcome — no raw tool output, no leaking metadata.
        Bounded to ``budget`` chars so many candidates fit one search prompt."""
        meta, events, summary, findings = self._read_records(Path(path))
        if meta is None:
            return ""
        lines: list[str] = []
        if findings is not None:
            f = TrajectoryFindings.from_dict(findings)
            if f.has_content():
                lines.append(
                    f"- findings: situation={f.situation!r}; tell={f.tell!r}; "
                    f"root_cause={f.root_cause!r}; fix={f.fix!r}; "
                    f"affected_resource={f.affected_resource!r}"
                )
        for e in events:
            if e.kind == "thinking" and e.text:
                lines.append(f"- reasoning: {e.text}")
            elif e.kind == "tool_call" and e.tool:
                detail = f" {e.args}" if e.args else ""
                lines.append(f"- ran: {e.tool}{detail}")
        if summary:
            lines.append(f"- outcome: {summary.get('outcome', '')}")
        return _truncate("\n".join(lines), budget)

    def read_full(self, path: str | Path) -> str:
        """Return the full, human-readable transcript of one trajectory."""
        meta, events, summary, findings = self._read_records(Path(path))
        if meta is None:
            return ""
        lines = [f"# trajectory: {meta.app} / run {meta.run_id} ({meta.ts})"]
        for e in events:
            if e.kind == "thinking":
                lines.append(f"\n[think] {e.text}")
            elif e.kind == "tool_call":
                lines.append(f"\n[tool] {e.tool} {e.args}".rstrip())
            elif e.kind == "tool_result":
                body = e.stdout or e.stderr or ""
                code = "" if e.exit_code is None else f" (exit={e.exit_code})"
                lines.append(f"[result{code}] {body}".rstrip())
        if summary:
            lines.append(f"\n[outcome] {summary.get('outcome', '')}")
        if findings is not None:
            f = TrajectoryFindings.from_dict(findings)
            if f.has_content():
                lines.append(
                    f"\n[findings]\nsituation: {f.situation}\ntell: {f.tell}\n"
                    f"root_cause: {f.root_cause}\nfix: {f.fix}\n"
                    f"affected_resource: {f.affected_resource}"
                )
        return "\n".join(lines)
