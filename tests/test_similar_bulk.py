"""G1 — similar_bulk endpoint tests."""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from whatsapp_archive import create_app
from whatsapp_archive.scrape.db import open_db, upsert_article

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
def client(tmp_path: Path):
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    shutil.copy(FIXTURE_CHAT, archive_dir / "mini_chat.txt")
    return TestClient(create_app(archive_dir))


@pytest.fixture
def db_client(tmp_path: Path):
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    shutil.copy(FIXTURE_CHAT, archive_dir / "mini_chat.txt")
    conn = open_db(archive_dir)
    # Seed 3 articles
    for i in range(1, 4):
        upsert_article(conn, f"https://x.com/t/{i}", "ok", title=f"Tweet {i}")
    conn.commit()
    app_client = TestClient(create_app(archive_dir))
    yield conn, app_client
    conn.close()


# ── Tests ────────────────────────────────────────────────────────────────────

def test_similar_bulk_no_qdrant_returns_empty_map(client):
    r = client.post("/api/articles/similar_bulk", json={"ids": [1, 2, 3]})
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, dict)
    # All keys present, all values are []
    for v in data.values():
        assert v == []


def test_similar_bulk_response_shape(client):
    r = client.post("/api/articles/similar_bulk", json={"ids": [1, 2]})
    assert r.status_code == 200
    data = r.json()
    assert "1" in data
    assert "2" in data


def test_similar_bulk_empty_ids(client):
    r = client.post("/api/articles/similar_bulk", json={"ids": []})
    assert r.status_code == 200
    assert r.json() == {}


def test_similar_bulk_caps_at_50(client):
    ids = list(range(1, 60))
    r = client.post("/api/articles/similar_bulk", json={"ids": ids})
    assert r.status_code == 200
    # Should only process first 50
    data = r.json()
    assert len(data) <= 50


def test_similar_bulk_with_mock_qdrant(db_client):
    conn, app_client = db_client
    art_ids = [r["id"] for r in conn.execute("SELECT id FROM articles").fetchall()]
    assert len(art_ids) >= 2

    # Mock find_similar_articles to return a hit for the first article only
    def fake_find_similar(qdrant_client, article_id, limit):
        if article_id == art_ids[0]:
            return [{"id": art_ids[1], "score": 0.85}]
        return None  # no vector

    with patch("whatsapp_archive.scrape.vector.find_similar_articles", side_effect=fake_find_similar):
        with patch("whatsapp_archive.api.find_similar_articles", side_effect=fake_find_similar):
            r = app_client.post(
                "/api/articles/similar_bulk",
                json={"ids": art_ids, "min_score": 0.0},
            )
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, dict)


def test_similar_single_min_score_param(client):
    r = client.get("/api/articles/1/similar?min_score=0.7")
    # No Qdrant → 503 or 404
    assert r.status_code in (404, 503)
