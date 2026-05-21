"""Q16 — search results include tweet_meta for full TweetCard rendering."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from whatsapp_archive import create_app
from whatsapp_archive.scrape.db import open_db, upsert_article

FIXTURE_CHAT = Path(__file__).parent / "fixtures" / "mini_chat.txt"


@pytest.fixture(autouse=True)
def _no_background(monkeypatch):
    for env in ["BACKGROUND_SCRAPE", "BACKGROUND_ENRICH", "BACKGROUND_OCR",
                "BACKGROUND_DIGESTS", "BACKGROUND_ENGAGEMENT_REFRESH",
                "BACKGROUND_CLUSTERING", "BACKGROUND_PROFILES"]:
        monkeypatch.setenv(env, "false")


@pytest.fixture
def client(tmp_path):
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    shutil.copy(FIXTURE_CHAT, archive_dir / "mini_chat.txt")
    conn = open_db(archive_dir)

    upsert_article(
        conn, "https://x.com/fintech/1", "ok",
        title="FinTech quarterly results",
        raw_text="FinTech results beat consensus expectations across the board",
        tweet_meta=json.dumps({
            "author_handle": "fintechanalyst",
            "text": "FinTech quarterly results beat expectations — strong buy",
            "favorite_count": 230,
            "retweet_count": 45,
            "hashtags": ["FinTech", "earnings"],
        }),
        published_at="2025-03-01T10:00:00",
    )
    # non-tweet article (no tweet_meta)
    upsert_article(
        conn, "https://blog.example.com/post", "ok",
        title="FinTech industry overview",
        raw_text="FinTech sector has seen rapid adoption of AI-driven tools",
        tweet_meta=None,
        published_at="2025-03-02T10:00:00",
    )
    conn.commit()

    # Rebuild FTS so seeded rows are searchable
    conn.execute("INSERT INTO articles_fts(articles_fts) VALUES('rebuild')")
    conn.commit()
    conn.close()

    yield TestClient(create_app(archive_dir))


def _article_hits(r):
    return [h for h in r.json().get("results", []) if h.get("kind") == "article"]


# ── tweet_meta included in results ────────────────────────────────────────────

def test_tweet_article_hit_has_tweet_meta(client):
    r = client.get("/api/search?q=FinTech")
    assert r.status_code == 200
    hits = _article_hits(r)
    tweet_hits = [h for h in hits if h.get("tweet_meta")]
    assert len(tweet_hits) >= 1


def test_tweet_meta_is_parseable_json(client):
    r = client.get("/api/search?q=FinTech")
    hits = _article_hits(r)
    for h in hits:
        if h.get("tweet_meta"):
            parsed = json.loads(h["tweet_meta"])
            assert "author_handle" in parsed or "text" in parsed


def test_tweet_meta_contains_author(client):
    r = client.get("/api/search?q=FinTech")
    hits = _article_hits(r)
    tweet_hits = [h for h in hits if h.get("tweet_meta")]
    assert any(
        json.loads(h["tweet_meta"]).get("author_handle") == "fintechanalyst"
        for h in tweet_hits
    )


def test_non_tweet_article_tweet_meta_is_null(client):
    r = client.get("/api/search?q=FinTech")
    hits = _article_hits(r)
    non_tweet = [h for h in hits if "blog.example.com" in (h.get("url") or "")]
    if non_tweet:
        assert non_tweet[0].get("tweet_meta") is None


def test_article_id_present_in_tweet_hit(client):
    r = client.get("/api/search?q=FinTech")
    hits = _article_hits(r)
    for h in hits:
        assert "article_id" in h
        assert isinstance(h["article_id"], int)
