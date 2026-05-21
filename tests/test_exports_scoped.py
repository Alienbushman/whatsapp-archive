"""I14 — Scoped bundle exports: author / hashtag / mention / ticker / timerange / chat."""

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


def _seed_db(archive_dir: Path) -> tuple:
    """Seed DB with two tweets by different authors/hashtags/tickers."""
    db = open_db(archive_dir)

    aid1 = upsert_article(
        db, "https://x.com/alice/status/1", "ok",
        title="Alice tweet",
        raw_text="Hello $AAPL",
        tweet_meta=json.dumps({
            "author_handle": "alice",
            "author_name": "Alice",
            "text": "Hello $AAPL",
            "hashtags": ["Gold"],
            "mentioned_handles": ["bob"],
            "tickers": ["AAPL"],
        }),
        fetched_at="2024-01-15T10:00:00",
    )

    aid2 = upsert_article(
        db, "https://x.com/bob/status/2", "ok",
        title="Bob tweet",
        raw_text="Something about $TSLA",
        tweet_meta=json.dumps({
            "author_handle": "bob",
            "author_name": "Bob",
            "text": "Something about $TSLA",
            "hashtags": ["Tech"],
            "mentioned_handles": ["charlie"],
            "tickers": ["TSLA"],
        }),
        fetched_at="2024-02-10T12:00:00",
    )

    return db, aid1, aid2


@pytest.fixture
def client(tmp_path: Path):
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    shutil.copy(FIXTURE_CHAT, archive_dir / "mini_chat.txt")
    _seed_db(archive_dir)
    app = create_app(archive_dir)
    return TestClient(app)


# ── Author exports ────────────────────────────────────────────────────────────

def test_export_author_md(client):
    r = client.get("/api/export/author/alice?format=md")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/markdown")
    body = r.text
    assert "# Export:" in body
    assert "@alice" in body or "alice" in body


def test_export_author_csv(client):
    r = client.get("/api/export/author/alice?format=csv")
    assert r.status_code == 200
    assert "text/csv" in r.headers["content-type"]
    lines = r.text.splitlines()
    assert lines[0] == "timestamp,chat,sender,body,url,article_title,article_summary"
    assert len(lines) > 1


def test_export_author_json(client):
    r = client.get("/api/export/author/alice?format=json")
    assert r.status_code == 200
    assert "application/json" in r.headers["content-type"]
    data = r.json()
    assert "label" in data
    assert "articles" in data
    assert "messages" in data
    assert any("alice" in str(a) for a in data["articles"])


def test_export_author_unknown_404(client):
    r = client.get("/api/export/author/nobody?format=md")
    assert r.status_code == 404


# ── Hashtag exports ───────────────────────────────────────────────────────────

def test_export_hashtag_md(client):
    r = client.get("/api/export/hashtag/Gold?format=md")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/markdown")
    assert "#Gold" in r.text


def test_export_hashtag_json(client):
    r = client.get("/api/export/hashtag/Gold?format=json")
    assert r.status_code == 200
    data = r.json()
    assert "#Gold" in data["label"]
    assert len(data["articles"]) == 1


def test_export_hashtag_unknown_404(client):
    r = client.get("/api/export/hashtag/NeverUsed?format=md")
    assert r.status_code == 404


# ── Mention exports ───────────────────────────────────────────────────────────

def test_export_mention_md(client):
    r = client.get("/api/export/mention/bob?format=md")
    assert r.status_code == 200
    assert "bob" in r.text


def test_export_mention_unknown_404(client):
    r = client.get("/api/export/mention/nobody?format=md")
    assert r.status_code == 404


# ── Ticker exports ────────────────────────────────────────────────────────────

def test_export_ticker_md(client):
    r = client.get("/api/export/ticker/AAPL?format=md")
    assert r.status_code == 200
    assert "$AAPL" in r.text or "AAPL" in r.text


def test_export_ticker_csv(client):
    r = client.get("/api/export/ticker/TSLA?format=csv")
    assert r.status_code == 200
    lines = r.text.splitlines()
    assert lines[0] == "timestamp,chat,sender,body,url,article_title,article_summary"


def test_export_ticker_unknown_404(client):
    r = client.get("/api/export/ticker/UNKNOWN?format=md")
    assert r.status_code == 404


# ── Timerange exports ─────────────────────────────────────────────────────────

def test_export_timerange_md(client):
    r = client.get("/api/export/timerange?from_date=2024-01-01&to_date=2024-12-31&format=md")
    assert r.status_code == 200
    assert "# Export: Time range" in r.text


def test_export_timerange_json(client):
    r = client.get("/api/export/timerange?from_date=2024-01-01&format=json")
    assert r.status_code == 200
    data = r.json()
    assert "articles" in data


# ── Chat exports ──────────────────────────────────────────────────────────────

def test_export_chat_md(tmp_path: Path):
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    shutil.copy(FIXTURE_CHAT, archive_dir / "mini_chat.txt")
    _seed_db(archive_dir)
    app = create_app(archive_dir)
    client = TestClient(app)

    chats_r = client.get("/api/chats")
    assert chats_r.status_code == 200
    chats = chats_r.json()
    assert len(chats) > 0
    chat_id = chats[0]["id"]

    r = client.get(f"/api/export/chat/{chat_id}?format=md")
    assert r.status_code == 200
    assert "# Export: Chat:" in r.text


def test_export_chat_unknown_404(client):
    r = client.get("/api/export/chat/nonexistent-chat?format=md")
    assert r.status_code == 404
