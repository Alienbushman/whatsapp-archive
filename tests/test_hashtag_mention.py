"""I2 — /api/hashtags and /api/mentions endpoints."""

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
        title="Gold + Macro post",
        raw_text="Gold is up",
        tweet_meta=json.dumps({
            "author_handle": "alice",
            "author_name": "Alice",
            "hashtags": ["Gold", "Macro"],
            "mentioned_handles": ["charlie"],
            "text": "Gold is up #Gold #Macro @charlie",
            "favorite_count": 100,
        }),
        fetched_at="2024-03-01T10:00:00",
    )
    upsert_article(
        db, "https://x.com/bob/status/1", "ok",
        title="Second Gold post",
        raw_text="More Gold",
        tweet_meta=json.dumps({
            "author_handle": "bob",
            "author_name": "Bob",
            "hashtags": ["Gold"],
            "mentioned_handles": ["alice", "charlie"],
            "text": "More Gold #Gold @alice @charlie",
            "favorite_count": 50,
        }),
        fetched_at="2024-03-02T10:00:00",
    )
    return TestClient(create_app(archive_dir))


def test_list_hashtags(client):
    r = client.get("/api/hashtags")
    assert r.status_code == 200
    tags = {h["tag"]: h["count"] for h in r.json()}
    assert tags.get("Gold") == 2
    assert tags.get("Macro") == 1


def test_list_hashtags_sorted_by_count(client):
    r = client.get("/api/hashtags")
    counts = [h["count"] for h in r.json()]
    assert counts == sorted(counts, reverse=True)


def test_hashtag_tweets(client):
    r = client.get("/api/hashtags/Gold/tweets")
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 2
    assert len(data["tweets"]) == 2
    urls = {t["url"] for t in data["tweets"]}
    assert "https://x.com/alice/status/1" in urls
    assert "https://x.com/bob/status/1" in urls


def test_hashtag_tweets_sorted_by_likes(client):
    r = client.get("/api/hashtags/Gold/tweets?order=likes")
    tweets = r.json()["tweets"]
    fav_counts = [t["tweet"]["favorite_count"] for t in tweets]
    assert fav_counts[0] >= fav_counts[1]


def test_hashtag_single_tweet(client):
    r = client.get("/api/hashtags/Macro/tweets")
    assert r.status_code == 200
    assert r.json()["total"] == 1


def test_hashtag_not_found(client):
    r = client.get("/api/hashtags/Unknown/tweets")
    assert r.status_code == 404


def test_hashtag_summary(client):
    r = client.get("/api/hashtags/Gold")
    assert r.status_code == 200
    data = r.json()
    assert data["tag"] == "Gold"
    assert data["count"] == 2
    handles = {a["handle"] for a in data["top_authors"]}
    assert "alice" in handles
    assert "bob" in handles


def test_list_mentions(client):
    r = client.get("/api/mentions")
    assert r.status_code == 200
    mentions = {m["handle"]: m["count"] for m in r.json()}
    assert mentions.get("charlie") == 2
    assert mentions.get("alice") == 1


def test_mention_tweets(client):
    r = client.get("/api/mentions/charlie/tweets")
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 2


def test_mention_not_found(client):
    r = client.get("/api/mentions/nobody/tweets")
    assert r.status_code == 404


def test_mention_summary(client):
    r = client.get("/api/mentions/charlie")
    assert r.status_code == 200
    data = r.json()
    assert data["handle"] == "charlie"
    assert data["count"] == 2
    mentioners = {m["handle"] for m in data["top_mentioners"]}
    assert "alice" in mentioners
    assert "bob" in mentioners
