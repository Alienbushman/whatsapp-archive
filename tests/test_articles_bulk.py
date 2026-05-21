"""U3 — POST /api/articles/bulk returns article payloads (with tweet) for known URLs."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from whatsapp_archive import create_app
from whatsapp_archive.scrape.db import open_db, upsert_article

FIXTURE_CHAT = Path(__file__).parent / "fixtures" / "mini_chat.txt"

_TWEET_META = {
    "author_name": "Test User",
    "author_handle": "testuser",
    "hashtags": ["BTC"],
    "mentioned_handles": [],
    "embedded_urls": [],
    "text": "Bitcoin is pumping today #BTC",
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
        db,
        "https://x.com/testuser/status/999",
        "ok",
        title="@testuser: Bitcoin is pumping today #BTC",
        raw_text="Bitcoin is pumping today #BTC",
        tweet_meta=json.dumps(_TWEET_META),
    )
    upsert_article(
        db,
        "https://example.com/plain",
        "ok",
        title="Plain article",
        raw_text="Just a plain article with no tweet metadata.",
    )
    return TestClient(create_app(archive_dir))


def test_bulk_known_and_unknown(client):
    r = client.post(
        "/api/articles/bulk",
        json={"urls": ["https://x.com/testuser/status/999", "https://unknown.example.com/nope"]},
    )
    assert r.status_code == 200
    data = r.json()
    assert "https://x.com/testuser/status/999" in data
    assert "https://unknown.example.com/nope" in data
    assert data["https://unknown.example.com/nope"] is None


def test_bulk_empty_urls(client):
    r = client.post("/api/articles/bulk", json={"urls": []})
    assert r.status_code == 200
    assert r.json() == {}


def test_bulk_too_many_urls(client):
    urls = [f"https://example.com/{i}" for i in range(101)]
    r = client.post("/api/articles/bulk", json={"urls": urls})
    assert r.status_code == 413


def test_bulk_tweet_meta_deserialized(client):
    r = client.post(
        "/api/articles/bulk",
        json={"urls": ["https://x.com/testuser/status/999"]},
    )
    assert r.status_code == 200
    article = r.json()["https://x.com/testuser/status/999"]
    assert article is not None
    assert "tweet" in article
    assert article["tweet"]["author_handle"] == "testuser"
    assert "BTC" in article["tweet"]["hashtags"]
    # Both `tweet` (structured) and `tweet_meta` (raw JSON string) are returned
    # for backward compat — see _row_to_dict in api.py (Q15 fix). Earlier this
    # test asserted `tweet_meta` should be removed; it's deliberately kept.
    assert "tweet_meta" in article


def test_bulk_plain_article_no_tweet(client):
    r = client.post(
        "/api/articles/bulk",
        json={"urls": ["https://example.com/plain"]},
    )
    assert r.status_code == 200
    article = r.json()["https://example.com/plain"]
    assert article is not None
    assert article.get("tweet") is None
