"""G3 — Group export endpoint tests."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from whatsapp_archive import create_app
from whatsapp_archive.scrape.db import open_db, upsert_article, upsert_enrichment
from datetime import datetime

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
def db_and_client(tmp_path: Path):
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    shutil.copy(FIXTURE_CHAT, archive_dir / "mini_chat.txt")
    conn = open_db(archive_dir)
    tweet_metas = [
        {"author_handle": "alice", "text": "Alice says buy Gold", "hashtags": ["Gold"], "tickers": ["GLD"], "favorite_count": 10, "retweet_count": 2},
        {"author_handle": "bob", "text": "Bob disagrees: sell now", "hashtags": [], "tickers": ["GLD"], "favorite_count": 5, "retweet_count": 1},
        {"author_handle": "carol", "text": "Carol sees a breakout pattern", "hashtags": ["TA"], "tickers": [], "favorite_count": 20, "retweet_count": 8},
        {"author_handle": "dave", "text": "Dave thinks this is a trap", "hashtags": [], "tickers": [], "favorite_count": 3, "retweet_count": 0},
        {"author_handle": "eve", "text": "Eve has no opinion yet", "hashtags": [], "tickers": [], "favorite_count": 0, "retweet_count": 0},
    ]
    ids = []
    for i, meta in enumerate(tweet_metas, start=1):
        aid = upsert_article(
            conn,
            f"https://x.com/u/tweet{i}",
            "ok",
            title=f"Tweet {i}",
            tweet_meta=json.dumps(meta),
            published_at=f"2025-0{i}-01T10:00:00",
        )
        ids.append(aid)
    conn.commit()
    client = TestClient(create_app(archive_dir))
    yield conn, client, ids
    conn.close()


# ── Format: md ───────────────────────────────────────────────────────────────

def test_export_md_basic(db_and_client):
    conn, client, ids = db_and_client
    r = client.post("/api/export/group", json={"article_ids": ids[:3], "format": "md"})
    assert r.status_code == 200
    assert "text/markdown" in r.headers["content-type"]
    body = r.text
    assert "Group bundle: 3 tweets" in body
    assert "@alice" in body
    assert "@bob" in body
    assert "@carol" in body


def test_export_md_inline(db_and_client):
    conn, client, ids = db_and_client
    r = client.post("/api/export/group?inline=1", json={"article_ids": ids[:3], "format": "md"})
    assert r.status_code == 200
    data = r.json()
    assert "body" in data
    assert "Group bundle: 3 tweets" in data["body"]


# ── Format: prompt ────────────────────────────────────────────────────────────

def test_export_prompt_analyse(db_and_client):
    conn, client, ids = db_and_client
    r = client.post(
        "/api/export/group?inline=1",
        json={"article_ids": ids[:3], "format": "prompt", "prompt_template": "analyse"},
    )
    assert r.status_code == 200
    body = r.json()["body"]
    assert "You are an analyst" in body
    assert "[1]" in body
    assert "[2]" in body
    assert "[3]" in body
    assert "Constraints:" in body
    assert "@alice" in body


def test_export_prompt_custom(db_and_client):
    conn, client, ids = db_and_client
    r = client.post(
        "/api/export/group?inline=1",
        json={
            "article_ids": ids[:2],
            "format": "prompt",
            "prompt_template": "custom",
            "custom_prompt": "List all price targets mentioned.",
        },
    )
    assert r.status_code == 200
    body = r.json()["body"]
    assert "List all price targets mentioned." in body


def test_export_prompt_summarise(db_and_client):
    conn, client, ids = db_and_client
    r = client.post(
        "/api/export/group?inline=1",
        json={"article_ids": ids[:3], "format": "prompt", "prompt_template": "summarise"},
    )
    assert r.status_code == 200
    body = r.json()["body"]
    assert "3-5 bullets" in body or "bullets" in body.lower()


# ── Format: json ──────────────────────────────────────────────────────────────

def test_export_json(db_and_client):
    conn, client, ids = db_and_client
    r = client.post(
        "/api/export/group?inline=1",
        json={"article_ids": ids[:3], "format": "json"},
    )
    assert r.status_code == 200
    body_str = r.json()["body"]
    data = json.loads(body_str)
    assert data["count"] == 3
    assert len(data["tweets"]) == 3
    handles = [t["tweet_meta"]["author_handle"] for t in data["tweets"] if isinstance(t.get("tweet_meta"), dict)]
    assert "alice" in handles


# ── Format: txt ───────────────────────────────────────────────────────────────

def test_export_txt(db_and_client):
    conn, client, ids = db_and_client
    r = client.post(
        "/api/export/group?inline=1",
        json={"article_ids": ids[:2], "format": "txt"},
    )
    assert r.status_code == 200
    body = r.json()["body"]
    assert "Group bundle: 2 tweets" in body
    assert "[1]" in body


# ── include_similar_context (mocked Qdrant) ───────────────────────────────────

def test_export_include_similar_context_renderer():
    """Test that render_group_bundle includes RELATED CONTEXT when similar_context provided."""
    from whatsapp_archive.export.group import render_group_bundle

    articles = [
        {"id": 1, "url": "https://x.com/u/1", "published_at": "2025-01-01T10:00:00",
         "tweet": {"author_handle": "alice", "text": "Gold is rising", "hashtags": [], "tickers": []}},
        {"id": 2, "url": "https://x.com/u/2", "published_at": "2025-02-01T10:00:00",
         "tweet": {"author_handle": "bob", "text": "I agree", "hashtags": [], "tickers": []}},
    ]
    similar = [
        {"id": 10, "url": "https://x.com/u/10", "published_at": "2025-01-15T10:00:00",
         "tweet": {"author_handle": "carol", "text": "Related context tweet", "hashtags": [], "tickers": []},
         "similarity_score": 0.88},
    ]

    body = render_group_bundle(
        articles,
        fmt="prompt",
        prompt_template="analyse",
        similar_context=similar,
    )
    assert "RELATED CONTEXT" in body
    assert "@carol" in body
    assert "@alice" in body


# ── Validation ────────────────────────────────────────────────────────────────

def test_export_empty_ids_rejected(db_and_client):
    conn, client, ids = db_and_client
    r = client.post("/api/export/group", json={"article_ids": []})
    assert r.status_code == 422


def test_export_bad_format_rejected(db_and_client):
    conn, client, ids = db_and_client
    r = client.post("/api/export/group", json={"article_ids": ids[:1], "format": "yaml"})
    assert r.status_code == 422


def test_export_download_has_content_disposition(db_and_client):
    conn, client, ids = db_and_client
    r = client.post("/api/export/group", json={"article_ids": ids[:2], "format": "md"})
    assert r.status_code == 200
    cd = r.headers.get("content-disposition", "")
    assert "attachment" in cd
    assert ".md" in cd
