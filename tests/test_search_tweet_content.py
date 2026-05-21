"""Q7 — Global search finds tweet content tests."""
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
    for env in [
        "BACKGROUND_SCRAPE", "BACKGROUND_ENRICH", "BACKGROUND_OCR",
        "BACKGROUND_DIGESTS", "BACKGROUND_ENGAGEMENT_REFRESH", "BACKGROUND_CLUSTERING",
        "BACKGROUND_PROFILES",
    ]:
        monkeypatch.setenv(env, "false")


@pytest.fixture
def db_with_tweet(tmp_path: Path):
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    shutil.copy(FIXTURE_CHAT, archive_dir / "mini_chat.txt")
    conn = open_db(archive_dir)
    upsert_article(
        conn,
        "https://x.com/federalreserve/status/1",
        "ok",
        title="@federalreserve: Fed raises rates",
        raw_text="Federal Reserve raises interest rates by 50 basis points amid inflation concerns",
        tweet_meta=json.dumps({
            "author_handle": "federalreserve",
            "text": "Federal Reserve raises interest rates by 50 basis points amid inflation concerns",
            "hashtags": ["Fed", "InterestRates"],
        }),
        published_at="2025-03-01T10:00:00",
    )
    conn.commit()
    yield conn, archive_dir
    conn.close()


# ── FTS sync: articles added post-startup are searchable ──────────────────────

def test_fts_finds_tweet_body_text(db_with_tweet):
    conn, _ = db_with_tweet
    from whatsapp_archive.scrape.db import keyword_search_with_snippet
    rows = keyword_search_with_snippet(conn, "Federal Reserve", limit=10)
    assert any("federal" in (r["title"] or "").lower() or "federal" in (r["raw_text"] or "").lower() for r in rows)


def test_fts_finds_tweet_by_unique_phrase(db_with_tweet):
    conn, _ = db_with_tweet
    from whatsapp_archive.scrape.db import keyword_search_with_snippet
    rows = keyword_search_with_snippet(conn, "basis points", limit=10)
    assert len(rows) >= 1


def test_fts_finds_tweet_body_after_second_upsert(tmp_path):
    """Re-upserting an article keeps it findable in FTS (no duplicate / ghost entries)."""
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    shutil.copy(FIXTURE_CHAT, archive_dir / "mini_chat.txt")
    conn = open_db(archive_dir)

    upsert_article(conn, "https://x.com/test/status/99", "ok",
                   title="First title", raw_text="unique phrase alpha")
    upsert_article(conn, "https://x.com/test/status/99", "ok",
                   title="Updated title", raw_text="unique phrase beta updated")

    from whatsapp_archive.scrape.db import keyword_search_with_snippet
    rows_beta = keyword_search_with_snippet(conn, "beta updated", limit=10)
    assert len(rows_beta) >= 1
    # Old unique phrase should NOT appear (old FTS entry was deleted)
    rows_alpha = keyword_search_with_snippet(conn, "alpha", limit=10)
    assert len(rows_alpha) == 0
    conn.close()


# ── /api/search returns tweet body hits ──────────────────────────────────────

def test_search_api_finds_tweet_body(db_with_tweet):
    conn, archive_dir = db_with_tweet
    app = create_app(archive_dir)
    client = TestClient(app)
    resp = client.get("/api/search?q=Federal+Reserve")
    assert resp.status_code == 200
    data = resp.json()
    article_hits = [r for r in data["results"] if r["kind"] == "article"]
    assert len(article_hits) >= 1


def test_search_api_match_source_tweet_body(db_with_tweet):
    conn, archive_dir = db_with_tweet
    app = create_app(archive_dir)
    client = TestClient(app)
    resp = client.get("/api/search?q=basis+points")
    assert resp.status_code == 200
    data = resp.json()
    article_hits = [r for r in data["results"] if r["kind"] == "article"]
    assert len(article_hits) >= 1
    hit = article_hits[0]
    assert hit.get("match_source") in ("tweet_body", "tweet_title")


def test_search_api_match_source_tweet_meta(db_with_tweet):
    """Hashtag prefix query sets match_source=tweet_meta."""
    conn, archive_dir = db_with_tweet
    app = create_app(archive_dir)
    client = TestClient(app)
    resp = client.get("/api/search?q=%23Fed")
    assert resp.status_code == 200
    data = resp.json()
    article_hits = [r for r in data["results"] if r["kind"] == "article"]
    assert len(article_hits) >= 1
    assert all(h.get("match_source") == "tweet_meta" for h in article_hits)


def test_search_api_non_tweet_article_body_source(tmp_path):
    """Regular article body match reports match_source=article_body."""
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    shutil.copy(FIXTURE_CHAT, archive_dir / "mini_chat.txt")
    conn = open_db(archive_dir)
    upsert_article(
        conn, "https://example.com/news/42", "ok",
        title="Central bank news",
        raw_text="The central bank announced quantitative easing measures today.",
    )
    conn.commit()
    conn.close()

    app = create_app(archive_dir)
    client = TestClient(app)
    resp = client.get("/api/search?q=quantitative+easing")
    assert resp.status_code == 200
    data = resp.json()
    article_hits = [r for r in data["results"] if r["kind"] == "article"]
    assert len(article_hits) >= 1
    assert article_hits[0].get("match_source") == "article_body"
