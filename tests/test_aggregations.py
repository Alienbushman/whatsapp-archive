"""I4 — /api/aggregations/* endpoints."""

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
def client(tmp_path: Path) -> TestClient:
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    shutil.copy(FIXTURE_CHAT, archive_dir / "mini_chat.txt")

    db = open_db(archive_dir)

    # 5 articles in January (recent) by alice — high likes
    for i in range(5):
        aid = upsert_article(
            db, f"https://x.com/alice/status/{i}", "ok",
            title=f"Alice post {i}",
            raw_text=f"Alice content {i}",
            tweet_meta=json.dumps({
                "author_handle": "alice",
                "author_name": "Alice",
                "hashtags": ["Gold", "Finance"],
                "mentioned_handles": ["bob"],
                "text": f"Alice #{i} #Gold #Finance @bob",
                "favorite_count": 100 + i * 10,
                "retweet_count": 20 + i,
                "media_urls": ["https://pbs.twimg.com/media/img.jpg"],
            }),
            fetched_at=f"2024-01-{10+i:02d}T10:00:00",
            published_at=f"2024-01-{10+i:02d}T10:00:00",
        )
        upsert_enrichment(db, aid, summary=f"Alice summary {i}", categories="[]",
                          entities=json.dumps([{"name": "Gold", "kind": "commodity"}]),
                          sentiment="positive", model="test", enriched_at="2024-01-15T00:00:00")

    # 3 articles in July (old) by bob — low likes
    for i in range(3):
        aid = upsert_article(
            db, f"https://x.com/bob/status/{i}", "ok",
            title=f"Bob post {i}",
            raw_text=f"Bob content {i}",
            tweet_meta=json.dumps({
                "author_handle": "bob",
                "author_name": "Bob",
                "hashtags": ["Crypto"],
                "mentioned_handles": [],
                "text": f"Bob #{i} #Crypto",
                "favorite_count": 5 + i,
                "retweet_count": 1,
            }),
            fetched_at=f"2023-07-{1+i:02d}T10:00:00",
            published_at=f"2023-07-{1+i:02d}T10:00:00",
        )
        upsert_enrichment(db, aid, summary=f"Bob summary {i}", categories="[]",
                          entities=json.dumps([{"name": "Bitcoin", "kind": "asset"}]),
                          sentiment="negative", model="test", enriched_at="2023-07-05T00:00:00")

    # 2 articles in March by charlie — medium likes
    for i in range(2):
        aid = upsert_article(
            db, f"https://x.com/charlie/status/{i}", "ok",
            title=f"Charlie post {i}",
            raw_text=f"Charlie content {i}",
            tweet_meta=json.dumps({
                "author_handle": "charlie",
                "author_name": "Charlie",
                "hashtags": ["Gold", "Macro"],
                "mentioned_handles": ["alice"],
                "text": f"Charlie #{i} #Gold #Macro @alice",
                "favorite_count": 50 + i * 5,
                "retweet_count": 10 + i,
            }),
            fetched_at=f"2024-03-{1+i:02d}T10:00:00",
            published_at=f"2024-03-{1+i:02d}T10:00:00",
        )

    return TestClient(create_app(archive_dir))


# ── top-authors ────────────────────────────────────────────────────────────────

def test_top_authors_all(client):
    r = client.get("/api/aggregations/top-authors?window=all")
    assert r.status_code == 200
    authors = r.json()
    handles = [a["handle"] for a in authors]
    assert "alice" in handles
    assert "bob" in handles


def test_top_authors_sorted_by_tweet_count(client):
    r = client.get("/api/aggregations/top-authors?window=all")
    counts = [a["tweet_count"] for a in r.json()]
    assert counts == sorted(counts, reverse=True)


def test_top_authors_window_excludes_old(client):
    # window from 2024-01-01 forward (use now=2024-04-01 + 90d window)
    r = client.get("/api/aggregations/top-authors?window=90d&now=2024-04-01")
    assert r.status_code == 200
    handles = [a["handle"] for a in r.json()]
    # bob's tweets are from 2023-07 — should be excluded from the 90-day window
    assert "bob" not in handles
    assert "alice" in handles


def test_top_authors_limit(client):
    r = client.get("/api/aggregations/top-authors?window=all&limit=2")
    assert r.status_code == 200
    assert len(r.json()) <= 2


# ── top-hashtags ───────────────────────────────────────────────────────────────

def test_top_hashtags_all(client):
    r = client.get("/api/aggregations/top-hashtags?window=all")
    assert r.status_code == 200
    tags = {h["tag"]: h["count"] for h in r.json()}
    # alice: 5 Gold + charlie: 2 Gold = 7
    assert tags.get("Gold") == 7
    # alice: 5 Finance
    assert tags.get("Finance") == 5
    # bob: 3 Crypto
    assert tags.get("Crypto") == 3


def test_top_hashtags_window_filters(client):
    # 90-day window anchored to 2024-04-01 excludes bob's 2023-07 tweets
    r = client.get("/api/aggregations/top-hashtags?window=90d&now=2024-04-01")
    tags = {h["tag"]: h["count"] for h in r.json()}
    assert "Crypto" not in tags


def test_top_hashtags_sorted_by_count(client):
    r = client.get("/api/aggregations/top-hashtags?window=all")
    counts = [h["count"] for h in r.json()]
    assert counts == sorted(counts, reverse=True)


# ── top-tweets ─────────────────────────────────────────────────────────────────

def test_top_tweets_by_likes(client):
    r = client.get("/api/aggregations/top-tweets?by=favorite_count&window=all")
    assert r.status_code == 200
    tweets = r.json()
    assert len(tweets) > 0
    # First tweet should have the highest favorite_count
    counts = [t["tweet"]["favorite_count"] for t in tweets if t.get("tweet")]
    assert counts == sorted(counts, reverse=True)


def test_top_tweets_by_retweets(client):
    r = client.get("/api/aggregations/top-tweets?by=retweet_count&window=all")
    assert r.status_code == 200
    tweets = r.json()
    counts = [t["tweet"]["retweet_count"] for t in tweets if t.get("tweet")]
    assert counts == sorted(counts, reverse=True)


def test_top_tweets_window(client):
    # window anchored to 2024-04-01, 90 days — excludes bob's 2023-07 articles
    r = client.get("/api/aggregations/top-tweets?by=favorite_count&window=90d&now=2024-04-01")
    tweets = r.json()
    urls = [t["url"] for t in tweets]
    assert not any("bob" in u for u in urls)


# ── top-entities ──────────────────────────────────────────────────────────────

def test_top_entities_all(client):
    r = client.get("/api/aggregations/top-entities?window=all")
    assert r.status_code == 200
    entities = r.json()
    names = {e["entity_name"]: e["count"] for e in entities}
    # 5 alice articles each contribute "Gold" entity
    assert names.get("Gold") == 5
    # 3 bob articles each contribute "Bitcoin" entity
    assert names.get("Bitcoin") == 3


def test_top_entities_sorted(client):
    r = client.get("/api/aggregations/top-entities?window=all")
    counts = [e["count"] for e in r.json()]
    assert counts == sorted(counts, reverse=True)


# ── sentiment ─────────────────────────────────────────────────────────────────

def test_sentiment_by_author(client):
    r = client.get("/api/aggregations/sentiment?group_by=author&window=all")
    assert r.status_code == 200
    data = r.json()
    by_key = {g["key"]: g for g in data}
    # alice has 5 positive articles
    assert by_key["alice"]["positive"] == 5
    # bob has 3 negative articles
    assert by_key["bob"]["negative"] == 3


def test_sentiment_by_hashtag(client):
    r = client.get("/api/aggregations/sentiment?group_by=hashtag&window=all")
    assert r.status_code == 200
    data = r.json()
    by_key = {g["key"]: g for g in data}
    # Finance hashtag — only alice's articles are enriched
    assert by_key.get("Finance", {}).get("positive", 0) == 5


def test_sentiment_window(client):
    # Only 2024 articles (exclude bob's 2023-07 negatives)
    r = client.get("/api/aggregations/sentiment?group_by=author&window=90d&now=2024-04-01")
    data = r.json()
    by_key = {g["key"]: g for g in data}
    assert "bob" not in by_key
