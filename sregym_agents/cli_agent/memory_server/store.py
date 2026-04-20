"""SQLite-backed incident store with bag-of-words cosine similarity.

No external dependencies beyond the standard library — embeddings are
normalized term-frequency vectors over whitespace-tokenized text.
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS incidents (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    app          TEXT NOT NULL,
    symptoms     TEXT NOT NULL,
    key_checks   TEXT NOT NULL,
    root_causes  TEXT NOT NULL,
    fix          TEXT NOT NULL,
    lesson       TEXT NOT NULL,
    embedding_json TEXT NOT NULL,
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
)
"""


@dataclass
class IncidentCase:
    id: int
    app: str
    symptoms: str
    key_checks: str
    root_causes: str
    fix: str
    lesson: str
    created_at: str


def _tokenize(text: str) -> list[str]:
    return re.findall(r"\b\w+\b", text.lower())


def _embed(text: str) -> dict[str, float]:
    tokens = _tokenize(text)
    if not tokens:
        return {}
    counts = Counter(tokens)
    total = len(tokens)
    return {k: v / total for k, v in counts.items()}


def compute_embedding(app: str, symptoms: str, key_checks: str, root_causes: str) -> dict[str, float]:
    """Build the retrieval embedding for an incident from its diagnostic fields."""
    return _embed(f"{app} {symptoms} {key_checks} {root_causes}")


def _cosine(a: dict[str, float], b: dict[str, float]) -> float:
    if not a or not b:
        return 0.0
    common = set(a.keys()) & set(b.keys())
    dot = sum(a[k] * b[k] for k in common)
    norm_a = math.sqrt(sum(v * v for v in a.values()))
    norm_b = math.sqrt(sum(v * v for v in b.values()))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


class IncidentStore:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(_SCHEMA)

    def store(
        self,
        *,
        app: str,
        symptoms: str,
        key_checks: str,
        root_causes: str,
        fix: str,
        lesson: str,
    ) -> int:
        """Insert a new incident and return its rowid."""
        embedding = compute_embedding(app, symptoms, key_checks, root_causes)
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                "INSERT INTO incidents"
                " (app, symptoms, key_checks, root_causes, fix, lesson, embedding_json)"
                " VALUES (?,?,?,?,?,?,?)",
                (app, symptoms, key_checks, root_causes, fix, lesson, json.dumps(embedding)),
            )
            return int(cur.lastrowid)  # type: ignore[arg-type]

    def find_duplicate(
        self,
        embedding: dict[str, float],
        *,
        threshold: float = 0.7,
    ) -> IncidentCase | None:
        """Return the closest stored incident if similarity >= threshold, else None."""
        if not embedding:
            return None
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT id, app, symptoms, key_checks, root_causes, fix, lesson,"
                " embedding_json, created_at FROM incidents"
            ).fetchall()
        if not rows:
            return None
        best_score = -1.0
        best_row = None
        for row in rows:
            score = _cosine(embedding, json.loads(row[7]))
            if score > best_score:
                best_score = score
                best_row = row
        if best_row is None or best_score < threshold:
            return None
        return IncidentCase(
            id=best_row[0],
            app=best_row[1],
            symptoms=best_row[2],
            key_checks=best_row[3],
            root_causes=best_row[4],
            fix=best_row[5],
            lesson=best_row[6],
            created_at=best_row[8],
        )

    def update(
        self,
        incident_id: int,
        *,
        app: str,
        symptoms: str,
        key_checks: str,
        root_causes: str,
        fix: str,
        lesson: str,
    ) -> None:
        """Replace the mergeable fields of an existing incident and recompute its embedding."""
        embedding = compute_embedding(app, symptoms, key_checks, root_causes)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE incidents SET key_checks=?, root_causes=?, fix=?, lesson=?, embedding_json=? WHERE id=?",
                (key_checks, root_causes, fix, lesson, json.dumps(embedding), incident_id),
            )

    def retrieve(self, query: str, *, threshold: float = 0.05) -> IncidentCase | None:
        """Return the closest stored incident, or None if similarity < threshold."""
        query_emb = _embed(query)
        if not query_emb:
            return None
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT id, app, symptoms, key_checks, root_causes, fix, lesson,"
                " embedding_json, created_at FROM incidents"
            ).fetchall()
        if not rows:
            return None
        best_score = -1.0
        best_row = None
        for row in rows:
            score = _cosine(query_emb, json.loads(row[7]))
            if score > best_score:
                best_score = score
                best_row = row
        if best_row is None or best_score < threshold:
            return None
        return IncidentCase(
            id=best_row[0],
            app=best_row[1],
            symptoms=best_row[2],
            key_checks=best_row[3],
            root_causes=best_row[4],
            fix=best_row[5],
            lesson=best_row[6],
            created_at=best_row[8],
        )
