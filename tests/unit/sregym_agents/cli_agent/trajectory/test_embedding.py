"""Unit tests for the offline embedder + cosine ranking used by RAG retrieval."""

from __future__ import annotations

import numpy as np

from sregym_agents.cli_agent.trajectory.embedding import (
    HashingEmbedder,
    cosine_top_k,
    default_embedder,
)


def test_hashing_embedder_is_deterministic_and_normalized() -> None:
    emb = HashingEmbedder(dim=256)
    a = emb.embed(["frontend 503 profile crashloop"])
    b = emb.embed(["frontend 503 profile crashloop"])
    assert np.allclose(a, b)  # deterministic across calls (no salted hash)
    assert np.isclose(np.linalg.norm(a[0]), 1.0)  # L2-normalized


def test_cosine_ranks_lexically_similar_text_first() -> None:
    emb = HashingEmbedder(dim=512)
    corpus = [
        "frontend 503 profile pod crashloopbackoff after rollout missing db_host",
        "reservation latency cpu throttling raise cpu limit",
        "mongodb containercreating duplicate pvc multi-attach",
    ]
    matrix = emb.embed(corpus)
    query = emb.embed(["frontend 503s and profile pod crash looping after a deploy"])[0]

    ranked = cosine_top_k(query, matrix, k=3)
    assert ranked[0][0] == 0  # the db_host incident ranks first
    assert ranked[0][1] >= ranked[1][1] >= ranked[2][1]


def test_cosine_top_k_empty_matrix() -> None:
    emb = HashingEmbedder(dim=64)
    q = emb.embed(["anything"])[0]
    assert cosine_top_k(q, np.zeros((0, 64), dtype=np.float32), k=5) == []


def test_default_embedder_offline_without_env(monkeypatch) -> None:
    monkeypatch.delenv("SDS_TRAJECTORY_EMBED_MODEL", raising=False)
    assert isinstance(default_embedder(), HashingEmbedder)
