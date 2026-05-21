"""Q2 — Entity detail article endpoint tests."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from whatsapp_archive import create_app
from whatsapp_archive.scrape.db import open_db, upsert_article, upsert_entity, link_article_entity

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
def db_client(tmp_path: Path):
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    shutil.copy(FIXTURE_CHAT, archive_dir / "mini_chat.txt")
    conn = open_db(archive_dir)

    eid1 = upsert_entity(conn, "Vista Gold", "company")
    eid2 = upsert_entity(conn, "Orphan", "company")

    for i in range(3):
        meta = json.dumps({"author_handle": f"user{i}", "text": f"tweet about Vista Gold #{i}"})
        aid = upsert_article(conn, f"https://x.com/t/{i}", "ok",
                             title=f"Tweet {i}", tweet_meta=meta,
                             published_at=f"2025-0{i+1}-01T10:00:00")
        link_article_entity(conn, aid, eid1, "Vista Gold")

    conn.commit()
    client = TestClient(create_app(archive_dir))
    yield conn, client, eid1, eid2
    conn.close()


def test_entity_articles_returns_linked(db_client):
    conn, client, eid1, _ = db_client
    r = client.get(f"/api/entities/{eid1}/articles")
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 3
    assert len(data["articles"]) == 3


def test_entity_articles_tweet_meta_deserialized(db_client):
    conn, client, eid1, _ = db_client
    r = client.get(f"/api/entities/{eid1}/articles")
    assert r.status_code == 200
    articles = r.json()["articles"]
    for a in articles:
        # tweet_meta should be deserialized into "tweet" key
        assert "tweet" in a
        assert a["tweet"]["author_handle"].startswith("user")


def test_entity_articles_empty_for_no_links(db_client):
    conn, client, _, eid2 = db_client
    r = client.get(f"/api/entities/{eid2}/articles")
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 0
    assert data["articles"] == []


def test_entity_articles_pagination(db_client):
    conn, client, eid1, _ = db_client
    r = client.get(f"/api/entities/{eid1}/articles?page=1&page_size=2")
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 3
    assert len(data["articles"]) == 2

    r2 = client.get(f"/api/entities/{eid1}/articles?page=2&page_size=2")
    assert r2.status_code == 200
    assert len(r2.json()["articles"]) == 1


def test_entity_articles_404_for_nonexistent(db_client):
    _, client, _, _ = db_client
    r = client.get("/api/entities/9999/articles")
    assert r.status_code == 404


def test_entity_articles_order_engagement(db_client):
    conn, client, eid1, _ = db_client
    # Update one article to have high engagement
    conn.execute(
        "UPDATE articles SET tweet_meta=? WHERE title='Tweet 1'",
        (json.dumps({"author_handle": "user1", "text": "popular", "favorite_count": 500, "retweet_count": 100}),)
    )
    conn.commit()
    r = client.get(f"/api/entities/{eid1}/articles?order=engagement")
    assert r.status_code == 200
    articles = r.json()["articles"]
    # The high-engagement tweet should be first
    assert articles[0]["tweet"]["author_handle"] == "user1"
