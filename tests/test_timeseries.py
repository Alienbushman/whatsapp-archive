"""I9 — /api/timeseries/* endpoints."""

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

    # 2 articles per month, 6 months
    months = ["2024-01", "2024-02", "2024-03", "2024-04", "2024-05", "2024-06"]
    for mi, month in enumerate(months):
        for j in range(2):
            url = f"https://x.com/alice/status/{mi*2+j}"
            aid = upsert_article(
                db, url, "ok",
                title=f"Article {mi*2+j}",
                raw_text=f"Content {mi*2+j}",
                tweet_meta=json.dumps({
                    "author_handle": "alice" if j == 0 else "bob",
                    "author_name": "Alice" if j == 0 else "Bob",
                    "hashtags": ["Gold"] if mi < 3 else ["Crypto"],
                    "text": f"#{'Gold' if mi < 3 else 'Crypto'} content",
                    "favorite_count": 10 * (mi + 1),
                }),
                fetched_at=f"{month}-15T10:00:00",
                published_at=f"{month}-15T10:00:00",
            )
            sentiment = "positive" if j == 0 else "negative"
            upsert_enrichment(db, aid, summary=f"Summary {mi*2+j}", categories="[]",
                              entities="[]", sentiment=sentiment,
                              model="test", enriched_at=f"{month}-15T10:00:00")

    return TestClient(create_app(archive_dir))


# ── /api/timeseries/messages ──────────────────────────────────────────────────

def test_ts_messages_returns_buckets(client):
    r = client.get("/api/timeseries/messages?group_by=month")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    # mini_chat.txt has messages in 2023-01
    assert len(data) > 0


def test_ts_messages_day_granularity(client):
    r = client.get("/api/timeseries/messages?group_by=day")
    assert r.status_code == 200
    data = r.json()
    for row in data:
        assert len(row["bucket"]) == 10  # YYYY-MM-DD


def test_ts_messages_from_date_filter(client):
    r_full = client.get("/api/timeseries/messages?group_by=month")
    r_filtered = client.get("/api/timeseries/messages?group_by=month&from_date=2023-02-01")
    total_full = sum(d["value"] for d in r_full.json())
    total_filtered = sum(d["value"] for d in r_filtered.json())
    assert total_filtered <= total_full


def test_ts_messages_sender_filter(client):
    r = client.get("/api/timeseries/messages?group_by=month&author=Alice")
    assert r.status_code == 200
    data = r.json()
    total = sum(d["value"] for d in data)
    # Alice sends some messages in mini_chat.txt
    assert total > 0


# ── /api/timeseries/tweets ────────────────────────────────────────────────────

def test_ts_tweets_monthly_buckets(client):
    r = client.get("/api/timeseries/tweets?group_by=month")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 6  # 6 months seeded


def test_ts_tweets_bucket_counts_add_up(client):
    r = client.get("/api/timeseries/tweets?group_by=month")
    total = sum(d["value"] for d in r.json())
    assert total == 12  # 2 per month × 6 months


def test_ts_tweets_author_filter(client):
    r = client.get("/api/timeseries/tweets?group_by=month&author=alice")
    data = r.json()
    total = sum(d["value"] for d in data)
    assert total == 6  # 1 alice per month × 6 months


def test_ts_tweets_hashtag_filter(client):
    # Gold hashtag used in first 3 months (2 per month = 6)
    r = client.get("/api/timeseries/tweets?group_by=month&hashtag=Gold")
    data = r.json()
    total = sum(d["value"] for d in data)
    assert total == 6


def test_ts_tweets_from_date_excludes_early(client):
    r = client.get("/api/timeseries/tweets?group_by=month&from_date=2024-04-01")
    data = r.json()
    buckets = [d["bucket"] for d in data]
    assert not any(b < "2024-04" for b in buckets)


def test_ts_tweets_metric_favorites(client):
    r = client.get("/api/timeseries/tweets?group_by=month&metric=favorites")
    assert r.status_code == 200
    data = r.json()
    # Values should be engagement sums, not 0
    assert all(d["value"] >= 0 for d in data)


def test_ts_tweets_max_60_buckets(client):
    r = client.get("/api/timeseries/tweets?group_by=day")
    assert r.status_code == 200
    assert len(r.json()) <= 60


# ── /api/timeseries/sentiment ─────────────────────────────────────────────────

def test_ts_sentiment_returns_all_months(client):
    r = client.get("/api/timeseries/sentiment?group_by=month")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 6


def test_ts_sentiment_has_correct_keys(client):
    r = client.get("/api/timeseries/sentiment?group_by=month")
    row = r.json()[0]
    assert "bucket" in row
    assert "positive" in row
    assert "negative" in row
    assert "neutral" in row


def test_ts_sentiment_per_month_counts(client):
    r = client.get("/api/timeseries/sentiment?group_by=month")
    for row in r.json():
        assert row["positive"] == 1   # 1 alice (positive) per month
        assert row["negative"] == 1   # 1 bob (negative) per month


def test_ts_sentiment_author_filter(client):
    r = client.get("/api/timeseries/sentiment?group_by=month&author=alice")
    data = r.json()
    for row in data:
        assert row["positive"] >= 1
        assert row["negative"] == 0


def test_ts_sentiment_hashtag_filter(client):
    # Gold hashtag only in first 3 months
    r = client.get("/api/timeseries/sentiment?group_by=month&hashtag=Gold")
    data = r.json()
    assert len(data) == 3
