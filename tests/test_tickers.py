"""I7 — Stock ticker extraction."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient

from whatsapp_archive import create_app
from whatsapp_archive.enrich.tickers import extract_tickers
from whatsapp_archive.scrape.db import open_db, upsert_article, backfill_tickers

FIXTURE_CHAT = Path(__file__).parent / "fixtures" / "mini_chat.txt"


# ── Unit tests for extract_tickers ───────────────────────────────────────────

def test_basic_extraction():
    result = extract_tickers("$NDX is up 5%, also $BTC")
    assert result == ["NDX", "BTC"]


def test_lowercase_ignored():
    result = extract_tickers("$NDX is up 5%, also $BTC and $eth")
    assert result == ["NDX", "BTC"]


def test_numeric_amount_excluded():
    assert extract_tickers("Cost $100 today") == []


def test_deduplication():
    assert extract_tickers("$AAPL and $AAPL again") == ["AAPL"]


def test_empty_string():
    assert extract_tickers("") == []


def test_none_safe():
    assert extract_tickers(None) == []


def test_max_six_letters():
    assert extract_tickers("$TOOLONG") == []  # 7 chars
    assert extract_tickers("$ABCDEF") == ["ABCDEF"]  # exactly 6


def test_preserves_order():
    result = extract_tickers("$SPX then $NDX then $GOLD")
    assert result == ["SPX", "NDX", "GOLD"]


def test_mixed_text():
    result = extract_tickers("I'm long $AAPL and short $TSLA, watching $BTC")
    assert result == ["AAPL", "TSLA", "BTC"]


# ── Backfill test ─────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _no_background_loops(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BACKGROUND_SCRAPE", "false")
    monkeypatch.setenv("BACKGROUND_ENRICH", "false")


def test_backfill_tickers(tmp_path):
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    db = open_db(archive_dir)

    # Insert article without tickers field
    upsert_article(
        db, "https://x.com/alice/status/1", "ok",
        title="Gold & NDX",
        raw_text="$NDX breaks out, $GOLD holds",
        tweet_meta=json.dumps({
            "author_handle": "alice",
            "text": "$NDX breaks out, $GOLD holds",
            "hashtags": [],
        }),
        fetched_at="2024-01-01T00:00:00",
    )

    count = backfill_tickers(db)
    assert count == 1

    row = db.execute("SELECT tweet_meta FROM articles WHERE url = ?",
                     ("https://x.com/alice/status/1",)).fetchone()
    meta = json.loads(row["tweet_meta"])
    assert "NDX" in meta["tickers"]
    assert "GOLD" in meta["tickers"]


def test_backfill_idempotent(tmp_path):
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    db = open_db(archive_dir)

    upsert_article(
        db, "https://x.com/bob/status/1", "ok",
        title="BTC",
        raw_text="$BTC to the moon",
        tweet_meta=json.dumps({
            "author_handle": "bob",
            "text": "$BTC to the moon",
            "hashtags": [],
            "tickers": ["BTC"],  # already backfilled
        }),
        fetched_at="2024-01-01T00:00:00",
    )

    count = backfill_tickers(db)
    assert count == 0  # nothing updated (already has correct tickers)


# ── Integration: tweet_meta.tickers persisted via to_jsonable ────────────────

def test_scraped_tweet_tickers():
    """ScrapedTweet.tickers is populated and survives to_jsonable."""
    from whatsapp_archive.scrape.syndication import _parse_tweet_payload, to_jsonable

    payload = {
        "id_str": "999",
        "text": "$NDX just broke out of resistance. Watching $AAPL too.",
        "user": {"screen_name": "testuser", "name": "Test User"},
        "entities": {"hashtags": [], "user_mentions": [], "urls": []},
        "favorite_count": 10,
        "retweet_count": 2,
    }
    tweet = _parse_tweet_payload(payload)
    assert "NDX" in tweet.tickers
    assert "AAPL" in tweet.tickers

    d = to_jsonable(tweet)
    assert "NDX" in d["tickers"]
    assert "AAPL" in d["tickers"]


# ── API: $TICKER search via tweet_search_blob ─────────────────────────────────

def test_ticker_search_via_api(tmp_path):
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    shutil.copy(FIXTURE_CHAT, archive_dir / "mini_chat.txt")
    db = open_db(archive_dir)

    upsert_article(
        db, "https://x.com/alice/status/10", "ok",
        title="NDX rally",
        raw_text="$NDX rally today",
        tweet_meta=json.dumps({
            "author_handle": "alice",
            "text": "$NDX rally today",
            "hashtags": [],
            "tickers": ["NDX"],
        }),
        fetched_at="2024-01-01T00:00:00",
    )

    client = TestClient(create_app(archive_dir))
    r = client.get("/api/search?q=%24NDX&kind=article")
    assert r.status_code == 200
    results = r.json()["results"]
    article_results = [x for x in results if x["kind"] == "article"]
    assert len(article_results) >= 1
    assert any("alice/status/10" in x["url"] for x in article_results)
