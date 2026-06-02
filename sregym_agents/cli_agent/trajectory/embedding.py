"""Pluggable text embedder for RAG-based trajectory retrieval.

Two backends behind one ``Embedder`` protocol:

- :class:`LiteLLMEmbedder` — real semantic embeddings via litellm (any provider
  litellm supports, e.g. ``text-embedding-3-small``). Use in production.
- :class:`HashingEmbedder` — dependency-free, deterministic, offline bag-of-words
  hashing into a fixed-dim L2-normalized vector. No network, no API key. It is
  lexical (overlapping words → high similarity), not semantic, but it makes the
  RAG path runnable and testable anywhere.

:func:`default_embedder` picks LiteLLM when ``SDS_TRAJECTORY_EMBED_MODEL`` is set,
else falls back to hashing.
"""

from __future__ import annotations

import logging
import os
import re
from typing import TYPE_CHECKING, Protocol

import numpy as np

if TYPE_CHECKING:
    from collections.abc import Sequence

logger = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall((text or "").lower())


class Embedder(Protocol):
    """Maps a batch of texts to a 2-D float array, one unit row per text."""

    def embed(self, texts: Sequence[str]) -> np.ndarray: ...


def _l2_normalize(mat: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return mat / norms


class HashingEmbedder:
    """Deterministic hashing bag-of-words embedder (offline, no deps)."""

    def __init__(self, dim: int = 2048) -> None:
        self.dim = dim

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for i, text in enumerate(texts):
            for tok in _tokenize(text):
                # Stable per-token bucket; hash() is salted per-process, so use a
                # fixed FNV-style fold instead for run-to-run determinism.
                h = 1469598103934665603
                for ch in tok.encode("utf-8"):
                    h = ((h ^ ch) * 1099511628211) & 0xFFFFFFFFFFFFFFFF
                out[i, h % self.dim] += 1.0
        return _l2_normalize(out)


class LiteLLMEmbedder:
    """Semantic embedder backed by litellm's ``embedding`` endpoint."""

    def __init__(self, model: str) -> None:
        self.model = model

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        import litellm

        resp = litellm.embedding(model=self.model, input=list(texts))
        vecs = [np.asarray(item["embedding"], dtype=np.float32) for item in resp["data"]]
        return _l2_normalize(np.vstack(vecs))


def default_embedder() -> Embedder:
    """LiteLLM when ``SDS_TRAJECTORY_EMBED_MODEL`` is set, else hashing fallback."""
    model = os.getenv("SDS_TRAJECTORY_EMBED_MODEL")
    if model:
        logger.info("trajectory RAG: using LiteLLM embedder model=%s", model)
        return LiteLLMEmbedder(model)
    logger.info("trajectory RAG: using offline HashingEmbedder (set SDS_TRAJECTORY_EMBED_MODEL for semantic)")
    return HashingEmbedder()


def cosine_top_k(query_vec: np.ndarray, matrix: np.ndarray, k: int) -> list[tuple[int, float]]:
    """Return ``(row_index, score)`` for the ``k`` rows most similar to query.

    Rows are assumed L2-normalized, so the dot product is cosine similarity.
    """
    if matrix.size == 0:
        return []
    scores = matrix @ query_vec
    k = min(k, scores.shape[0])
    top = np.argpartition(-scores, k - 1)[:k]
    ranked = sorted(top, key=lambda i: float(scores[i]), reverse=True)
    return [(int(i), float(scores[i])) for i in ranked]
