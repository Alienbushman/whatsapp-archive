"""R3 — Topic clustering tests."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from whatsapp_archive import create_app
from whatsapp_archive.scrape.db import open_db, upsert_article
from whatsapp_archive.scrape.topics import run_clustering, should_recluster

FIXTURE_CHAT = Path(__file__).parent / "fixtures" / "mini_chat.txt"


@pytest.fixture(autouse=True)
def _no_background(monkeypatch):
    monkeypatch.setenv("BACKGROUND_SCRAPE", "false")
    monkeypatch.setenv("BACKGROUND_ENRICH", "false")
    monkeypatch.setenv("BACKGROUND_OCR", "false")
    monkeypatch.setenv("BACKGROUND_DIGESTS", "false")
    monkeypatch.setenv("BACKGROUND_ENGAGEMENT_REFRESH", "false")
    monkeypatch.setenv("BACKGROUND_CLUSTERING", "false")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _build_vectors(n_clusters: int, per_cluster: int, dim: int = 16) -> list[tuple[int, list[float]]]:
    """Build well-separated vectors: each cluster occupies a different axis."""
    rng = np.random.default_rng(42)
    vectors = []
    article_id = 1
    for c in range(n_clusters):
        centre = np.zeros(dim)
        centre[c % dim] = 10.0
        for _ in range(per_cluster):
            vec = centre + rng.normal(0, 0.01, dim)
            vectors.append((article_id, vec.tolist()))
            article_id += 1
    return vectors


def _fake_name_fn(articles, ollama_url):
    return f"Cluster {len(articles)}", "Auto-detected theme"


@pytest.fixture
def db(tmp_path: Path):
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    shutil.copy(FIXTURE_CHAT, archive_dir / "mini_chat.txt")
    conn = open_db(archive_dir)
    # Seed 30 articles
    for i in range(30):
        upsert_article(conn, f"https://x.com/article/{i}", "ok", title=f"Article {i}")
    yield conn
    conn.close()


@pytest.fixture
def client(tmp_path: Path):
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    shutil.copy(FIXTURE_CHAT, archive_dir / "mini_chat.txt")
    return TestClient(create_app(archive_dir))


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_run_clustering_creates_topics(db):
    vectors = _build_vectors(3, 10)  # 30 articles in 3 clusters
    result = run_clustering(db, vectors, "http://localhost:11434", name_fn=_fake_name_fn)
    assert result["skipped"] is False
    assert result["topics"] >= 3  # may be more due to k formula, but at least 3
    assert result["articles"] == 30


def test_run_clustering_topic_rows_in_db(db):
    vectors = _build_vectors(3, 10)
    run_clustering(db, vectors, "http://localhost:11434", name_fn=_fake_name_fn)
    topic_count = db.execute("SELECT COUNT(*) AS n FROM topics").fetchone()["n"]
    assert topic_count >= 3


def test_run_clustering_article_assignments(db):
    vectors = _build_vectors(3, 10)
    run_clustering(db, vectors, "http://localhost:11434", name_fn=_fake_name_fn)
    assigned = db.execute("SELECT COUNT(DISTINCT article_id) AS n FROM topic_articles").fetchone()["n"]
    assert assigned == 30


def test_centroid_distance_ordering(db):
    vectors = _build_vectors(3, 10)
    run_clustering(db, vectors, "http://localhost:11434", name_fn=_fake_name_fn)
    topic_id = db.execute("SELECT id FROM topics LIMIT 1").fetchone()["id"]
    dists = [
        r["distance_to_centroid"]
        for r in db.execute(
            "SELECT distance_to_centroid FROM topic_articles WHERE topic_id=? ORDER BY distance_to_centroid ASC",
            (topic_id,),
        ).fetchall()
    ]
    assert dists == sorted(dists)


def test_idempotent_recluster(db):
    vectors = _build_vectors(3, 10)
    run_clustering(db, vectors, "http://localhost:11434", name_fn=_fake_name_fn)
    count1 = db.execute("SELECT COUNT(*) AS n FROM topics").fetchone()["n"]
    run_clustering(db, vectors, "http://localhost:11434", name_fn=_fake_name_fn)
    count2 = db.execute("SELECT COUNT(*) AS n FROM topics").fetchone()["n"]
    assert count1 == count2
    total_assigned = db.execute("SELECT COUNT(*) AS n FROM topic_articles").fetchone()["n"]
    assert total_assigned == 30


def test_should_recluster_empty(db):
    assert should_recluster(db) is True


def test_should_not_recluster_after_fresh_run(db):
    vectors = _build_vectors(3, 10)
    run_clustering(db, vectors, "http://localhost:11434", name_fn=_fake_name_fn)
    assert should_recluster(db, new_article_threshold=50) is False


def test_too_few_vectors_returns_skipped(db):
    vectors = _build_vectors(1, 5)  # only 5 vectors
    result = run_clustering(db, vectors, "http://localhost:11434", name_fn=_fake_name_fn)
    assert result["skipped"] is True


# ── API endpoint tests ────────────────────────────────────────────────────────

def test_topics_list_empty(client):
    r = client.get("/api/topics")
    assert r.status_code == 200
    assert r.json() == []


def test_topics_detail_not_found(client):
    r = client.get("/api/topics/9999")
    assert r.status_code == 404
