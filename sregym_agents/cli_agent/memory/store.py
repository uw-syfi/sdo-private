"""Per-app JSONL lesson store.

One JSONL file per app, one record per verified lesson (schema in §4 of
``docs/cli-agent-memory.md``). Deliberately dumb: no SQLite, no embeddings,
no similarity search — at v1 scale (tens of distinct failure modes per app)
the LLM reads the whole per-app set and does the reasoning. The store only
loads, appends, and merges.

The store is rewritten wholesale on every write (the file is small), so a
write is always: load → modify the in-memory list → rewrite.

Writes are safe under concurrency (e.g. ``parallel`` benchmark workers on one
host writing the same per-app file): each ``append``/``merge`` holds an
exclusive ``flock`` over the whole read-modify-write, and the rewrite is atomic
(temp file + ``os.replace``) so a concurrent reader never sees a torn file.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import logging
import os
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator
    from typing import Any

logger = logging.getLogger(__name__)

# confirmed_by values: a full conductor verdict, autonomous self-verified
# symptom-clearance, or a partial confirmation (diagnosis confirmed but the
# mitigation/fix was not). The last keeps `fix` recorded-but-unconfirmed.
CONFIRMED_VERDICT = "verdict"
CONFIRMED_SELF = "self_verified"
CONFIRMED_DIAGNOSIS_ONLY = "diagnosis_only"


@dataclass
class Lesson:
    """A single verified lesson. ``id`` is unique within its app's store."""

    id: int
    app: str
    situation: str
    root_cause: str
    tell: str
    fix: str
    affected_resource: str
    confirmed_by: str
    seen_count: int = 1
    obvious_guess: str = ""
    created_at: str = ""

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Lesson:
        """Build a Lesson from a stored JSON object, tolerating extra keys.

        Unknown keys (e.g. a future ``role``) are ignored so an older reader
        doesn't choke on records written by a newer schema.
        """
        known = set(cls.__dataclass_fields__)  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in raw.items() if k in known})

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# Reusable empty default for dataclass fields (avoids mutable default).
def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(app: str) -> str:
    """Filesystem-safe slug for an app name (``hotelReservation`` → ``hotelreservation``)."""
    slug = _SLUG_RE.sub("-", app.strip().lower()).strip("-")
    return slug or "unknown"


class LessonStore:
    """JSONL-backed, per-app lesson store rooted at ``store_dir``."""

    def __init__(self, store_dir: str | Path) -> None:
        self.store_dir = Path(store_dir)

    def path_for(self, app: str) -> Path:
        return self.store_dir / f"{slugify(app)}.jsonl"

    @contextlib.contextmanager
    def _locked(self, app: str) -> Iterator[None]:
        """Hold an exclusive cross-process lock for ``app``'s store.

        Serializes the read-modify-write of concurrent writers (e.g. parallel
        benchmark workers on one host) so updates aren't lost. The lock file is
        separate from the data file so it is never truncated/replaced underfoot.
        """
        self.store_dir.mkdir(parents=True, exist_ok=True)
        lock_path = self.path_for(app).with_suffix(".lock")
        fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def load(self, app: str) -> list[Lesson]:
        """Return all lessons stored for ``app`` (empty list if none).

        Malformed lines are skipped with a warning rather than aborting the
        whole load — one corrupt record must not block recall or writes.
        """
        path = self.path_for(app)
        if not path.exists():
            return []
        lessons: list[Lesson] = []
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            line = line.strip()
            if not line:
                continue
            try:
                lessons.append(Lesson.from_dict(json.loads(line)))
            except (json.JSONDecodeError, TypeError) as exc:
                logger.warning("Skipping malformed lesson at %s:%d (%s)", path, lineno, exc)
        return lessons

    def _write_all(self, app: str, lessons: list[Lesson]) -> None:
        path = self.path_for(app)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = "".join(json.dumps(lesson.to_dict()) + "\n" for lesson in lessons)
        # Atomic replace so a concurrent reader never observes a torn file.
        tmp = path.with_suffix(f".tmp.{os.getpid()}")
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, path)

    def append(
        self,
        app: str,
        *,
        situation: str,
        root_cause: str,
        tell: str,
        fix: str,
        affected_resource: str,
        confirmed_by: str,
        obvious_guess: str = "",
    ) -> Lesson:
        """Add a new lesson (``seen_count=1``) and return it with its assigned id."""
        with self._locked(app):
            lessons = self.load(app)
            new_id = max((lesson.id for lesson in lessons), default=0) + 1
            lesson = Lesson(
                id=new_id,
                app=app,
                situation=situation,
                root_cause=root_cause,
                tell=tell,
                fix=fix,
                affected_resource=affected_resource,
                confirmed_by=confirmed_by,
                seen_count=1,
                obvious_guess=obvious_guess,
                created_at=_now(),
            )
            lessons.append(lesson)
            self._write_all(app, lessons)
            return lesson

    def merge(
        self,
        app: str,
        lesson_id: int,
        *,
        situation: str,
        root_cause: str,
        tell: str,
        fix: str,
        affected_resource: str,
        obvious_guess: str | None = None,
    ) -> Lesson:
        """Merge a new presentation into existing lesson ``lesson_id``.

        Replaces the broadenable fields with the LLM-merged versions and
        increments ``seen_count`` (an independent confirmation — see §4). The
        original ``confirmed_by``/``created_at`` are preserved. Raises
        ``KeyError`` if ``lesson_id`` is not present for ``app``.
        """
        with self._locked(app):
            lessons = self.load(app)
            target = next((lesson for lesson in lessons if lesson.id == lesson_id), None)
            if target is None:
                raise KeyError(f"No lesson id={lesson_id} for app {app!r}")
            target.situation = situation
            target.root_cause = root_cause
            target.tell = tell
            target.fix = fix
            target.affected_resource = affected_resource
            if obvious_guess is not None:
                target.obvious_guess = obvious_guess
            target.seen_count += 1
            self._write_all(app, lessons)
            return target
