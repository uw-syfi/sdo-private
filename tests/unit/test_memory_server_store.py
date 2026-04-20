"""Unit tests for sregym_agents.cli_agent.memory_server.store."""

from pathlib import Path

import pytest

from sregym_agents.cli_agent.memory_server.store import (
    IncidentCase,
    IncidentStore,
    _cosine,
    _embed,
    _tokenize,
    compute_embedding,
)

# --- _tokenize ---


def test_tokenize_basic():
    assert _tokenize("Hello World") == ["hello", "world"]


def test_tokenize_strips_punctuation():
    result = _tokenize("cart 503, checkout OOMKilled")
    assert "cart" in result
    assert "503" in result
    assert "checkout" in result
    assert "oomkilled" in result


def test_tokenize_empty():
    assert _tokenize("") == []


# --- _embed ---


def test_embed_sums_to_one():
    emb = _embed("foo bar foo")
    assert abs(sum(emb.values()) - 1.0) < 1e-9


def test_embed_empty_returns_empty():
    assert _embed("") == {}


def test_embed_term_frequency():
    emb = _embed("a a b")
    assert abs(emb["a"] - 2 / 3) < 1e-9
    assert abs(emb["b"] - 1 / 3) < 1e-9


# --- _cosine ---


def test_cosine_identical():
    a = _embed("valkey cart redis auth")
    assert abs(_cosine(a, a) - 1.0) < 1e-9


def test_cosine_disjoint():
    a = _embed("valkey cart")
    b = _embed("cpu memory oom")
    assert _cosine(a, b) == 0.0


def test_cosine_partial_overlap():
    a = _embed("cart valkey auth")
    b = _embed("cart valkey cpu")
    score = _cosine(a, b)
    assert 0.0 < score < 1.0


def test_cosine_empty():
    assert _cosine({}, {"a": 1.0}) == 0.0
    assert _cosine({"a": 1.0}, {}) == 0.0


# --- IncidentStore ---


@pytest.fixture
def store(tmp_path: Path) -> IncidentStore:
    return IncidentStore(tmp_path / "incidents.db")


def _sample_incident(**overrides) -> dict:
    base = {
        "app": "astronomy-shop",
        "symptoms": "cart returning 503, checkout OOMKilled",
        "key_checks": "kubectl get pods → cart crashlooping; kubectl logs → connection refused on valkey",
        "root_causes": "valkey requirepass set but cart has no REDIS_PASSWORD; memory flood job",
        "fix": "Added REDIS_PASSWORD to cart; deleted flood job; raised memory limit",
        "lesson": "for valkey/cart issues check requirepass and active jobs first",
    }
    base.update(overrides)
    return base


def test_store_returns_rowid(store: IncidentStore):
    rowid = store.store(**_sample_incident())
    assert isinstance(rowid, int)
    assert rowid >= 1


def test_store_multiple_increments(store: IncidentStore):
    id1 = store.store(**_sample_incident())
    id2 = store.store(**_sample_incident(app="social-network"))
    assert id2 > id1


def test_retrieve_empty_returns_none(store: IncidentStore):
    assert store.retrieve("cart 503 valkey") is None


def test_retrieve_returns_closest(store: IncidentStore):
    store.store(**_sample_incident())
    case = store.retrieve("cart returning 503 valkey auth failed")
    assert case is not None
    assert isinstance(case, IncidentCase)
    assert case.app == "astronomy-shop"


def test_retrieve_below_threshold_returns_none(store: IncidentStore):
    store.store(**_sample_incident())
    # Completely unrelated query
    case = store.retrieve("xyzzy frobnicator quux", threshold=0.99)
    assert case is None


def test_retrieve_picks_closer_match(store: IncidentStore):
    store.store(**_sample_incident(app="astronomy-shop"))
    store.store(
        **_sample_incident(
            app="social-network",
            symptoms="nginx 502 upstream timeout cassandra",
            key_checks="kubectl logs → upstream connect error; kubectl get pods → cassandra crashloop",
            root_causes="cassandra memory limit too low",
            fix="raised cassandra memory limit",
            lesson="check cassandra memory limit for nginx 502 errors",
        )
    )
    case = store.retrieve("cart 503 valkey authentication")
    assert case is not None
    assert case.app == "astronomy-shop"


def test_retrieve_returns_dataclass_fields(store: IncidentStore):
    store.store(**_sample_incident())
    case = store.retrieve("valkey cart redis")
    assert case is not None
    assert case.symptoms == "cart returning 503, checkout OOMKilled"
    assert case.lesson == "for valkey/cart issues check requirepass and active jobs first"
    assert case.created_at  # non-empty string


def test_store_creates_parent_dirs(tmp_path: Path):
    deep_path = tmp_path / "a" / "b" / "c" / "incidents.db"
    s = IncidentStore(deep_path)
    s.store(**_sample_incident())
    assert deep_path.exists()


# --- compute_embedding ---


def test_compute_embedding_matches_retrieval_fields():
    emb = compute_embedding("astronomy-shop", "cart 503", "kubectl get pods", "valkey auth")
    assert "astronomy" in emb or "shop" in emb
    assert "cart" in emb
    assert "valkey" in emb


# --- find_duplicate ---


def test_find_duplicate_empty_returns_none(store: IncidentStore):
    emb = compute_embedding("astronomy-shop", "cart 503", "kubectl", "valkey")
    assert store.find_duplicate(emb) is None


def test_find_duplicate_above_threshold(store: IncidentStore):
    store.store(**_sample_incident())
    emb = compute_embedding(
        "astronomy-shop",
        "cart returning 503, checkout OOMKilled",
        "kubectl get pods → cart crashlooping; kubectl logs → connection refused on valkey",
        "valkey requirepass set but cart has no REDIS_PASSWORD; memory flood job",
    )
    case = store.find_duplicate(emb, threshold=0.5)
    assert case is not None
    assert case.app == "astronomy-shop"


def test_find_duplicate_below_threshold_returns_none(store: IncidentStore):
    store.store(**_sample_incident())
    emb = compute_embedding("unrelated-app", "cpu spike", "top", "noisy neighbour")
    assert store.find_duplicate(emb, threshold=0.7) is None


# --- update ---


def test_update_modifies_fields(store: IncidentStore):
    rowid = store.store(**_sample_incident())
    store.update(
        rowid,
        app="astronomy-shop",
        symptoms="cart returning 503, checkout OOMKilled",
        key_checks="updated key checks",
        root_causes="updated root causes",
        fix="updated fix",
        lesson="updated lesson",
    )
    case = store.retrieve("cart valkey astronomy")
    assert case is not None
    assert case.lesson == "updated lesson"
    assert case.key_checks == "updated key checks"


def test_update_recomputes_embedding(store: IncidentStore):
    rowid = store.store(**_sample_incident())
    store.update(
        rowid,
        app="astronomy-shop",
        symptoms="cart returning 503, checkout OOMKilled",
        key_checks="nginx ingress timeout",
        root_causes="ingress misconfiguration",
        fix="fixed ingress",
        lesson="check ingress for timeout errors",
    )
    # Old terms should now score lower; new terms should match
    case = store.retrieve("nginx ingress timeout")
    assert case is not None
    assert case.root_causes == "ingress misconfiguration"
