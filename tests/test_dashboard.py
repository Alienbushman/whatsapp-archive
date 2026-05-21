"""I6 — Dashboard overview endpoint."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from whatsapp_archive import create_app
from whatsapp_archive.scrape.db import open_db, upsert_article, upsert_enrichment

FIXTURE_CHAT = Path(__file__).parent / "fixtures" / "mini_chat.txt"


@pytest.fixture(autouse=True)
def _no_background_loops(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BACKGROUND_SCRAPE", "false")
    monkeypatch.setenv("BACKGROUND_ENRICH", "false")


@pytest.fixture
def seeded_client(tmp_path: Path):
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    shutil.copy(FIXTURE_CHAT, archive_dir / "mini_chat.txt")
    db = open_db(archive_dir)

    aid1 = upsert_article(
        db, "https://x.com/alice/status/1", "ok",
        title="Tweet 1",
        raw_text="Hello",
        tweet_meta=json.dumps({
            "author_handle": "alice",
            "text": "Hello",
            "hashtags": ["Gold"],
            "mentioned_handles": [],
            "tickers": [],
        }),
        fetched_at="2024-01-10T10:00:00",
    )
    aid2 = upsert_article(
        db, "https://x.com/bob/status/2", "ok",
        title="Tweet 2",
        raw_text="World",
        tweet_meta=json.dumps({
            "author_handle": "bob",
            "text": "World",
            "hashtags": ["Tech"],
            "mentioned_handles": [],
            "tickers": [],
        }),
        fetched_at="2024-02-15T12:00:00",
    )
    # Add enrichment for aid1
    upsert_enrichment(db, aid1, summary="Alice summary", categories='["Finance"]',
                      entities="[]", sentiment="positive", model="test",
                      enriched_at="2024-01-10T11:00:00")

    app = create_app(archive_dir)
    return TestClient(app)


# ── Basic structure ───────────────────────────────────────────────────────────

def test_dashboard_returns_200(seeded_client):
    r = seeded_client.get("/api/dashboard")
    assert r.status_code == 200


def test_dashboard_has_required_keys(seeded_client):
    data = seeded_client.get("/api/dashboard").json()
    required = [
        "total_chats", "total_messages", "total_articles", "total_tweets",
        "distinct_authors", "distinct_hashtags", "date_range",
        "scrape", "enrich", "top_authors", "top_hashtags",
    ]
    for key in required:
        assert key in data, f"Missing key: {key}"


# ── Counts ────────────────────────────────────────────────────────────────────

def test_dashboard_counts(seeded_client):
    data = seeded_client.get("/api/dashboard").json()
    assert data["total_chats"] >= 1
    assert data["total_articles"] == 2
    assert data["total_tweets"] == 2
    assert data["distinct_authors"] == 2
    assert data["distinct_hashtags"] == 2


def test_dashboard_enrichment_count(seeded_client):
    data = seeded_client.get("/api/dashboard").json()
    assert data["enrich"]["enriched"] == 1
    assert data["enrich"]["total"] == 2


# ── Date range ────────────────────────────────────────────────────────────────

def test_dashboard_date_range_not_null(seeded_client):
    data = seeded_client.get("/api/dashboard").json()
    # Mini chat has messages so date_range should have values
    assert data["date_range"]["from"] is not None or data["total_messages"] == 0


# ── Top lists ─────────────────────────────────────────────────────────────────

def test_dashboard_top_authors(seeded_client):
    data = seeded_client.get("/api/dashboard").json()
    handles = [a["handle"] for a in data["top_authors"]]
    assert "alice" in handles
    assert "bob" in handles


def test_dashboard_top_hashtags(seeded_client):
    data = seeded_client.get("/api/dashboard").json()
    tags = [h["tag"] for h in data["top_hashtags"]]
    assert "Gold" in tags or "Tech" in tags


# ── Empty archive ─────────────────────────────────────────────────────────────

def test_dashboard_empty_archive(tmp_path: Path):
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    shutil.copy(FIXTURE_CHAT, archive_dir / "mini_chat.txt")
    # No articles in DB
    app = create_app(archive_dir)
    client = TestClient(app)
    r = client.get("/api/dashboard")
    assert r.status_code == 200
    data = r.json()
    assert data["total_articles"] == 0
    assert data["total_tweets"] == 0
    assert data["distinct_authors"] == 0
    assert data["top_authors"] == []
    assert data["top_hashtags"] == []
    assert data["latest_tweet"] is None
