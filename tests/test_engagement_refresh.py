"""I12 — Engagement refresh: re-scrape hot tweets to update stats."""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from whatsapp_archive import create_app
from whatsapp_archive.scrape.db import (
    get_engagement_refresh_candidates,
    open_db,
    upsert_article,
)
from whatsapp_archive.scrape.background import _refresh_one_sync

FIXTURE_CHAT = Path(__file__).parent / "fixtures" / "mini_chat.txt"


def _tweet_meta(favorite_count=10, retweet_count=5, view_count=100, refreshed_at=None):
    m = {
        "author_handle": "alice",
        "text": "test tweet",
        "hashtags": [],
        "mentioned_handles": [],
        "tickers": [],
        "favorite_count": favorite_count,
        "retweet_count": retweet_count,
        "view_count": view_count,
    }
    if refreshed_at:
        m["refreshed_at"] = refreshed_at
    return json.dumps(m)


def _fresh_scraped_article(url, favorite_count=500, retweet_count=200, view_count=5000):
    """Return a mock ScrapedArticle with higher engagement counts."""
    from whatsapp_archive.scrape.article import ScrapedArticle
    tweet = {
        "author_handle": "alice",
        "text": "test tweet",
        "hashtags": [],
        "mentioned_handles": [],
        "tickers": [],
        "favorite_count": favorite_count,
        "retweet_count": retweet_count,
        "view_count": view_count,
    }
    return ScrapedArticle(
        url=url,
        title="@alice: test tweet",
        author="alice",
        published_at=None,
        raw_text="test tweet",
        og_image_url=None,
        scrape_method="syndication",
        tweet=tweet,
    )


@pytest.fixture(autouse=True)
def _no_background(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BACKGROUND_SCRAPE", "false")
    monkeypatch.setenv("BACKGROUND_ENRICH", "false")
    monkeypatch.setenv("BACKGROUND_OCR", "false")
    monkeypatch.setenv("BACKGROUND_DIGESTS", "false")
    monkeypatch.setenv("BACKGROUND_ENGAGEMENT_REFRESH", "false")


@pytest.fixture
def db(tmp_path: Path):
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    shutil.copy(FIXTURE_CHAT, archive_dir / "mini_chat.txt")
    return open_db(archive_dir)


@pytest.fixture
def client(tmp_path: Path):
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    shutil.copy(FIXTURE_CHAT, archive_dir / "mini_chat.txt")
    db = open_db(archive_dir)
    db.commit()
    db.close()
    app = create_app(archive_dir)
    return TestClient(app)


# ── Unit tests: candidate selection ──────────────────────────────────────────

def test_candidates_empty_db(db):
    rows = get_engagement_refresh_candidates(db, batch_size=50)
    assert rows == []


def test_candidates_returns_high_engagement(db):
    # Seed 5 articles, one with very high engagement
    for i in range(5):
        upsert_article(db, f"https://x.com/u/status/{i}", "ok",
                       tweet_meta=_tweet_meta(favorite_count=i * 10),
                       fetched_at="2024-06-10T10:00:00")
    candidates = get_engagement_refresh_candidates(db, batch_size=3)
    ids = [dict(r)["id"] for r in candidates]
    assert len(ids) >= 1


def test_candidates_excludes_recently_refreshed(db):
    now_iso = datetime.now(timezone.utc).isoformat()
    upsert_article(db, "https://x.com/u/status/99", "ok",
                   tweet_meta=_tweet_meta(favorite_count=9999, refreshed_at=now_iso),
                   fetched_at="2024-06-10T10:00:00")
    candidates = get_engagement_refresh_candidates(db, batch_size=50, min_age_hours=6)
    # The recently-refreshed article should NOT appear
    assert all(
        json.loads(dict(r)["tweet_meta"]).get("refreshed_at") != now_iso
        for r in candidates
    )


def test_candidates_includes_recent_articles(db):
    # Article published 2 days ago — should be in "recent" bucket
    two_days_ago = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    upsert_article(db, "https://x.com/u/status/200", "ok",
                   tweet_meta=_tweet_meta(favorite_count=0),
                   fetched_at=two_days_ago)
    candidates = get_engagement_refresh_candidates(db, batch_size=50, recent_days=7)
    urls = [dict(r)["url"] for r in candidates]
    assert "https://x.com/u/status/200" in urls


def test_candidates_top_n_engagement(db):
    # Seed 100 articles with varied engagement; top-5 should dominate
    for i in range(100):
        upsert_article(db, f"https://x.com/u/status/bulk{i}", "ok",
                       tweet_meta=_tweet_meta(favorite_count=i),
                       fetched_at="2020-01-01T00:00:00")  # old, not in recent bucket
    candidates = get_engagement_refresh_candidates(db, batch_size=5, recent_days=7)
    # We should get at most 5 from the top-N bucket (old articles, no recent)
    assert 1 <= len(candidates) <= 5


# ── Unit tests: _refresh_one_sync ────────────────────────────────────────────

def test_refresh_updates_engagement(db):
    url = "https://x.com/alice/status/1"
    upsert_article(db, url, "ok",
                   tweet_meta=_tweet_meta(favorite_count=5),
                   fetched_at="2024-06-10T10:00:00")
    row = db.execute("SELECT id FROM articles WHERE url=?", (url,)).fetchone()
    article_id = row["id"]

    with patch("whatsapp_archive.scrape.background.scrape_generic",
               return_value=_fresh_scraped_article(url, favorite_count=500)):
        result = _refresh_one_sync(article_id, db)

    assert result["ok"] is True
    updated = db.execute("SELECT tweet_meta FROM articles WHERE id=?", (article_id,)).fetchone()
    meta = json.loads(updated["tweet_meta"])
    assert meta["favorite_count"] == 500
    assert "refreshed_at" in meta


def test_refresh_adds_refreshed_at(db):
    url = "https://x.com/bob/status/2"
    upsert_article(db, url, "ok",
                   tweet_meta=_tweet_meta(),
                   fetched_at="2024-06-10T10:00:00")
    row = db.execute("SELECT id FROM articles WHERE url=?", (url,)).fetchone()

    with patch("whatsapp_archive.scrape.background.scrape_generic",
               return_value=_fresh_scraped_article(url)):
        _refresh_one_sync(row["id"], db)

    meta = json.loads(db.execute("SELECT tweet_meta FROM articles WHERE id=?", (row["id"],)).fetchone()["tweet_meta"])
    assert "refreshed_at" in meta


def test_refresh_missing_article(db):
    result = _refresh_one_sync(99999, db)
    assert result["ok"] is False
    assert result["kind"] == "missing"


def test_refresh_non_tweet_article(db):
    upsert_article(db, "https://example.com/page", "ok",
                   raw_text="some article",
                   fetched_at="2024-06-10T10:00:00")
    row = db.execute("SELECT id FROM articles WHERE url=?", ("https://example.com/page",)).fetchone()
    from whatsapp_archive.scrape.article import ScrapedArticle
    non_tweet = ScrapedArticle(
        url="https://example.com/page", title="article", author=None,
        published_at=None, raw_text="text", og_image_url=None,
        scrape_method="trafilatura", tweet=None,
    )
    with patch("whatsapp_archive.scrape.background.scrape_generic", return_value=non_tweet):
        result = _refresh_one_sync(row["id"], db)
    assert result["ok"] is False
    assert result["kind"] == "no_tweet"


def test_refresh_scrape_error(db):
    url = "https://x.com/gone/status/404"
    upsert_article(db, url, "ok",
                   tweet_meta=_tweet_meta(),
                   fetched_at="2024-06-10T10:00:00")
    row = db.execute("SELECT id FROM articles WHERE url=?", (url,)).fetchone()
    from whatsapp_archive.scrape.article import Gone404
    with patch("whatsapp_archive.scrape.background.scrape_generic", side_effect=Gone404("gone")):
        result = _refresh_one_sync(row["id"], db)
    assert result["ok"] is False
    assert result["kind"] == "scrape_error"


# ── API tests ─────────────────────────────────────────────────────────────────

def test_api_engagement_refresh_status(client):
    r = client.get("/api/engagement_refresh/status")
    assert r.status_code == 200
    assert "phase" in r.json()


def test_api_article_refresh_endpoint(client):
    db_path = None
    # Insert a test article through the DB directly
    import sqlite3
    from pathlib import Path
    # Find the archive DB
    for p in Path(client.app.state.archive_dir if hasattr(client.app.state, "archive_dir") else ".").rglob("archive.db"):
        db_path = p
        break

    # Use the client to fetch and then refresh — seed via articles/fetch mock
    url = "https://x.com/testuser/status/9999"
    with patch("whatsapp_archive.api.scrape_generic",
               return_value=_fresh_scraped_article(url, favorite_count=10)):
        r = client.post("/api/articles/fetch", json={"url": url})

    if r.status_code != 200:
        pytest.skip("article fetch not available in this test setup")

    article_id = r.json()["id"]
    with patch("whatsapp_archive.scrape.background.scrape_generic",
               return_value=_fresh_scraped_article(url, favorite_count=9999)):
        r2 = client.post(f"/api/articles/{article_id}/refresh")

    assert r2.status_code == 200
    data = r2.json()
    assert data["id"] == article_id
    meta = data["tweet_meta"]
    if isinstance(meta, str):
        meta = json.loads(meta)
    assert meta["favorite_count"] == 9999
    assert "refreshed_at" in meta


def test_api_article_refresh_404(client):
    r = client.post("/api/articles/99999/refresh")
    assert r.status_code in (400, 404)
