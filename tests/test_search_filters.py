"""I3 — Advanced search filters on /api/search and /api/senders."""

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
    # Article 1: high likes, has media, Gold hashtag, early date
    upsert_article(
        db, "https://x.com/alice/status/10", "ok",
        title="Gold rally early",
        raw_text="Gold is surging today",
        tweet_meta=json.dumps({
            "author_handle": "alice",
            "author_name": "Alice",
            "hashtags": ["Gold", "Markets"],
            "mentioned_handles": ["bob"],
            "text": "Gold is surging #Gold #Markets @bob",
            "favorite_count": 500,
            "retweet_count": 100,
            "media_urls": ["https://pbs.twimg.com/media/img1.jpg"],
        }),
        fetched_at="2024-01-15T08:00:00",
        published_at="2024-01-15T08:00:00",
    )
    # Article 2: low likes, no media, Crypto hashtag, late date
    upsert_article(
        db, "https://x.com/bob/status/20", "ok",
        title="Crypto outlook",
        raw_text="Crypto is volatile",
        tweet_meta=json.dumps({
            "author_handle": "bob",
            "author_name": "Bob",
            "hashtags": ["Crypto"],
            "mentioned_handles": [],
            "text": "Crypto is volatile #Crypto",
            "favorite_count": 10,
            "retweet_count": 2,
            "media_urls": [],
        }),
        fetched_at="2024-06-01T12:00:00",
        published_at="2024-06-01T12:00:00",
    )
    return TestClient(create_app(archive_dir))


# ── /api/senders ─────────────────────────────────────────────────────────────

def test_senders_returns_list(client):
    r = client.get("/api/senders")
    assert r.status_code == 200
    senders = r.json()
    assert isinstance(senders, list)
    assert len(senders) > 0


def test_senders_contains_chat_participants(client):
    r = client.get("/api/senders")
    senders = r.json()
    # mini_chat.txt has Alice and Bob as senders
    assert "Alice" in senders
    assert "Bob" in senders


def test_senders_scoped_to_chat(client):
    # First get the chat id
    chats_r = client.get("/api/chats")
    assert chats_r.status_code == 200
    chats = chats_r.json()
    assert len(chats) == 1
    cid = chats[0]["id"]

    r = client.get(f"/api/senders?chat_id={cid}")
    assert r.status_code == 200
    senders = r.json()
    assert "Alice" in senders


def test_senders_unknown_chat_returns_empty(client):
    r = client.get("/api/senders?chat_id=nonexistent")
    assert r.status_code == 200
    assert r.json() == []


# ── /api/search with filters ──────────────────────────────────────────────────

def test_search_filter_author(client):
    r = client.get("/api/search?q=Gold&author=alice")
    assert r.status_code == 200
    results = r.json()["results"]
    article_results = [x for x in results if x["kind"] == "article"]
    assert all(
        x.get("url", "").startswith("https://x.com/alice") for x in article_results
    )


def test_search_filter_author_excludes_others(client):
    r = client.get("/api/search?q=Gold&author=bob")
    assert r.status_code == 200
    results = r.json()["results"]
    article_results = [x for x in results if x["kind"] == "article"]
    # alice/status/10 has "Gold" but is by alice — should be excluded
    assert not any("alice" in x.get("url", "") for x in article_results)


def test_search_filter_hashtag(client):
    r = client.get("/api/search?q=Gold&hashtag=Markets")
    assert r.status_code == 200
    results = r.json()["results"]
    article_results = [x for x in results if x["kind"] == "article"]
    assert len(article_results) == 1
    assert "alice" in article_results[0]["url"]


def test_search_filter_min_likes(client):
    r = client.get("/api/search?q=Gold&min_likes=200")
    assert r.status_code == 200
    results = r.json()["results"]
    article_results = [x for x in results if x["kind"] == "article"]
    # Only alice's tweet (500 likes) passes
    assert len(article_results) == 1
    assert "alice" in article_results[0]["url"]


def test_search_filter_min_retweets(client):
    r = client.get("/api/search?q=Gold&min_retweets=50")
    assert r.status_code == 200
    results = r.json()["results"]
    article_results = [x for x in results if x["kind"] == "article"]
    assert len(article_results) == 1


def test_search_filter_has_media(client):
    r = client.get("/api/search?q=Gold&has_media=1")
    assert r.status_code == 200
    results = r.json()["results"]
    article_results = [x for x in results if x["kind"] == "article"]
    assert len(article_results) == 1
    assert "alice" in article_results[0]["url"]


def test_search_filter_date_range(client):
    r = client.get("/api/search?q=Gold&from_date=2024-06-01")
    assert r.status_code == 200
    results = r.json()["results"]
    article_results = [x for x in results if x["kind"] == "article"]
    # alice's tweet was 2024-01-15 — should be excluded
    assert not any("alice/status" in x.get("url", "") for x in article_results)


def test_search_filter_combined(client):
    # author=alice AND hashtag=Gold AND min_likes=100
    r = client.get("/api/search?q=Gold&author=alice&hashtag=Gold&min_likes=100")
    assert r.status_code == 200
    results = r.json()["results"]
    article_results = [x for x in results if x["kind"] == "article"]
    assert len(article_results) == 1
    assert "alice" in article_results[0]["url"]


def test_search_filters_applied_field(client):
    r = client.get("/api/search?q=Gold&min_likes=50")
    assert r.status_code == 200
    data = r.json()
    assert data["filters_applied"] is not None
    assert data["filters_applied"]["min_likes"] == 50


def test_search_no_filters_returns_null_applied(client):
    r = client.get("/api/search?q=Gold")
    assert r.status_code == 200
    data = r.json()
    assert data["filters_applied"] is None


def test_search_message_sender_filter(client):
    r = client.get("/api/search?q=Hello&sender=Alice")
    assert r.status_code == 200
    results = r.json()["results"]
    msg_results = [x for x in results if x["kind"] == "message"]
    assert len(msg_results) == 1


def test_search_message_sender_filter_excludes(client):
    r = client.get("/api/search?q=Hello&sender=Bob")
    assert r.status_code == 200
    results = r.json()["results"]
    msg_results = [x for x in results if x["kind"] == "message"]
    assert len(msg_results) == 0
