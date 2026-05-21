"""I1 — /api/authors endpoints return per-author stats and tweet lists."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from whatsapp_archive import create_app
from whatsapp_archive.scrape.db import open_db, upsert_article

FIXTURE_CHAT = Path(__file__).parent / "fixtures" / "mini_chat.txt"

_ALICE_META = {
    "author_name": "Alice",
    "author_handle": "alice",
    "hashtags": ["Gold", "Macro"],
    "mentioned_handles": ["bob"],
    "embedded_urls": [],
    "text": "Gold is up today #Gold #Macro",
    "favorite_count": 100,
    "retweet_count": 20,
    "view_count": 5000,
}

_BOB_META = {
    "author_name": "Bob",
    "author_handle": "bob",
    "hashtags": ["Silver"],
    "mentioned_handles": [],
    "embedded_urls": [],
    "text": "Silver is rising #Silver",
    "favorite_count": 50,
    "retweet_count": 10,
    "view_count": 2000,
}


@pytest.fixture(autouse=True)
def _no_background_loops(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BACKGROUND_SCRAPE", "false")
    monkeypatch.setenv("BACKGROUND_ENRICH", "false")


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    shutil.copy(FIXTURE_CHAT, archive_dir / "mini_chat.txt")

    db = open_db(archive_dir)
    upsert_article(
        db, "https://x.com/alice/status/1", "ok",
        title="Alice tweet 1", raw_text=_ALICE_META["text"],
        tweet_meta=json.dumps(_ALICE_META),
        fetched_at="2024-03-01T10:00:00",
    )
    upsert_article(
        db, "https://x.com/alice/status/2", "ok",
        title="Alice tweet 2", raw_text="Another gold post #Gold",
        tweet_meta=json.dumps({**_ALICE_META, "text": "Another gold post #Gold", "favorite_count": 200}),
        fetched_at="2024-03-02T10:00:00",
    )
    upsert_article(
        db, "https://x.com/bob/status/1", "ok",
        title="Bob tweet 1", raw_text=_BOB_META["text"],
        tweet_meta=json.dumps(_BOB_META),
        fetched_at="2024-03-01T11:00:00",
    )
    return TestClient(create_app(archive_dir))


def test_list_authors_returns_both(client):
    r = client.get("/api/authors")
    assert r.status_code == 200
    data = r.json()
    handles = [a["handle"] for a in data["authors"]]
    assert "alice" in handles
    assert "bob" in handles


def test_list_authors_sorted_by_tweet_count(client):
    r = client.get("/api/authors")
    authors = r.json()["authors"]
    assert authors[0]["handle"] == "alice"
    assert authors[0]["tweet_count"] == 2
    assert authors[1]["tweet_count"] == 1


def test_author_summary(client):
    r = client.get("/api/authors/alice")
    assert r.status_code == 200
    data = r.json()
    assert data["handle"] == "alice"
    assert data["tweet_count"] == 2
    assert data["total_favorites"] == 300  # 100 + 200
    assert data["total_retweets"] == 40    # 20 + 20


def test_author_summary_top_hashtags(client):
    r = client.get("/api/authors/alice")
    hashtags = {h["tag"]: h["count"] for h in r.json()["top_hashtags"]}
    assert hashtags.get("Gold") == 2


def test_author_summary_top_mentioned(client):
    r = client.get("/api/authors/alice")
    mentions = {m["handle"]: m["count"] for m in r.json()["top_mentioned"]}
    assert mentions.get("bob") == 2


def test_author_not_found(client):
    r = client.get("/api/authors/nobody")
    assert r.status_code == 404


def test_author_tweets_sorted_by_likes(client):
    r = client.get("/api/authors/alice/tweets?order=likes")
    assert r.status_code == 200
    tweets = r.json()["tweets"]
    assert len(tweets) == 2
    fav_counts = [t["tweet"]["favorite_count"] for t in tweets]
    assert fav_counts[0] >= fav_counts[1]


def test_author_tweets_pagination(client):
    r = client.get("/api/authors/alice/tweets?page=1&page_size=1")
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 2
    assert len(data["tweets"]) == 1


def test_author_tweets_not_found(client):
    r = client.get("/api/authors/nobody/tweets")
    assert r.status_code == 404
