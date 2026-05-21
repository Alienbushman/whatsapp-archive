"""Q13 — Topics clustering tests."""
from __future__ import annotations

import math
import shutil
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from whatsapp_archive import create_app
from whatsapp_archive.scrape.db import open_db
from whatsapp_archive.scrape.topics import (
    run_clustering,
    should_recluster,
    get_topic_list,
    get_topic,
)

FIXTURE_CHAT = Path(__file__).parent / "fixtures" / "mini_chat.txt"


@pytest.fixture(autouse=True)
def _no_background(monkeypatch):
    for env in [
        "BACKGROUND_SCRAPE", "BACKGROUND_ENRICH", "BACKGROUND_OCR",
        "BACKGROUND_DIGESTS", "BACKGROUND_ENGAGEMENT_REFRESH", "BACKGROUND_CLUSTERING",
        "BACKGROUND_PROFILES",
    ]:
        monkeypatch.setenv(env, "false")


@pytest.fixture
def db(tmp_path: Path):
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    shutil.copy(FIXTURE_CHAT, archive_dir / "mini_chat.txt")
    conn = open_db(archive_dir)
    yield conn
    conn.close()


@pytest.fixture
def client(tmp_path: Path):
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    shutil.copy(FIXTURE_CHAT, archive_dir / "mini_chat.txt")
    app = create_app(archive_dir)
    return TestClient(app)


def _make_vectors(n: int, dim: int = 4) -> list[tuple[int, list[float]]]:
    """Generate n random (article_id, vector) pairs. IDs start at 1000."""
    rng = np.random.default_rng(42)
    return [(1000 + i, rng.random(dim).tolist()) for i in range(n)]


def _noop_name_fn(articles, ollama_url):
    return "Test topic", "A test description"


# ── DB / clustering unit tests ────────────────────────────────────────────────

def test_run_clustering_skips_fewer_than_10_vectors(db):
    vectors = _make_vectors(5)
    result = run_clustering(db, vectors, "http://localhost:11434", name_fn=_noop_name_fn)
    assert result["skipped"] is True
    assert "not enough vectors" in result["reason"]


def test_run_clustering_produces_topics(db):
    vectors = _make_vectors(20)
    result = run_clustering(db, vectors, "http://localhost:11434", name_fn=_noop_name_fn)
    assert result["skipped"] is False
    assert result["topics"] > 0
    assert result["articles"] == 20


def test_run_clustering_k_formula(db):
    n = 20
    expected_k = max(8, int(math.sqrt(n) * 0.6))
    vectors = _make_vectors(n)
    result = run_clustering(db, vectors, "http://localhost:11434", name_fn=_noop_name_fn)
    assert result["topics"] == expected_k


def test_run_clustering_persists_to_db(db):
    vectors = _make_vectors(15)
    run_clustering(db, vectors, "http://localhost:11434", name_fn=_noop_name_fn)
    topic_count = db.execute("SELECT COUNT(*) AS n FROM topics").fetchone()["n"]
    assert topic_count > 0
    art_count = db.execute("SELECT COUNT(*) AS n FROM topic_articles").fetchone()["n"]
    assert art_count == 15


def test_run_clustering_idempotent(db):
    """Second run replaces topics rather than appending."""
    vectors = _make_vectors(15)
    run_clustering(db, vectors, "http://localhost:11434", name_fn=_noop_name_fn)
    run_clustering(db, vectors, "http://localhost:11434", name_fn=_noop_name_fn)
    art_count = db.execute("SELECT COUNT(*) AS n FROM topic_articles").fetchone()["n"]
    assert art_count == 15  # not 30


def test_should_recluster_when_empty(db):
    assert should_recluster(db) is True


def test_should_recluster_false_when_recent_and_few_new(db):
    vectors = _make_vectors(10)
    run_clustering(db, vectors, "http://localhost:11434", name_fn=_noop_name_fn)
    # 10 articles clustered, 0 new — below threshold of 50
    assert should_recluster(db) is False


def test_get_topic_list_empty(db):
    assert get_topic_list(db) == []


def test_get_topic_list_after_clustering(db):
    vectors = _make_vectors(15)
    run_clustering(db, vectors, "http://localhost:11434", name_fn=_noop_name_fn)
    rows = get_topic_list(db)
    assert len(rows) > 0
    for r in rows:
        assert "name" in r
        assert "article_count" in r


def test_get_topic_includes_detail_fields(db):
    vectors = _make_vectors(15)
    run_clustering(db, vectors, "http://localhost:11434", name_fn=_noop_name_fn)
    topic_id = db.execute("SELECT id FROM topics LIMIT 1").fetchone()["id"]
    detail = get_topic(db, topic_id)
    assert detail is not None
    assert "top_hashtags" in detail
    assert "top_entities" in detail
    assert "activity_arc" in detail


def test_get_topic_returns_none_for_missing(db):
    assert get_topic(db, 9999) is None


# ── API tests ─────────────────────────────────────────────────────────────────

def test_api_topics_list_empty(client):
    resp = client.get("/api/topics")
    assert resp.status_code == 200
    assert resp.json() == []


def test_api_topics_recluster_no_qdrant(client):
    """Without Qdrant, recluster returns 503."""
    resp = client.post("/api/topics/recluster")
    assert resp.status_code == 503


def test_api_topics_recluster_no_vectors(client):
    """With Qdrant but no vectors, covered by unit test (inject mock is hard via TestClient)."""
    # Direct unit-level coverage in test_recluster_with_no_vectors_returns_skipped
    pass


def test_recluster_with_no_vectors_returns_skipped(db):
    """Direct call with empty vectors returns skipped."""
    result = run_clustering(db, [], "http://localhost:11434", name_fn=_noop_name_fn)
    assert result["skipped"] is True


def test_api_topics_status(client):
    resp = client.get("/api/topics/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "phase" in data


def test_api_topic_404(client):
    resp = client.get("/api/topics/9999")
    assert resp.status_code == 404


def test_api_topic_articles_404(client):
    resp = client.get("/api/topics/9999/articles")
    assert resp.status_code == 404
