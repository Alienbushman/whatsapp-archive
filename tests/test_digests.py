"""I16 — Daily/weekly digest generation."""

from __future__ import annotations

import json
import shutil
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from whatsapp_archive import create_app
from whatsapp_archive.scrape.db import open_db, upsert_article
from whatsapp_archive.enrich.digest import generate_digest, _period_dates

FIXTURE_CHAT = Path(__file__).parent / "fixtures" / "mini_chat.txt"

_TWEET_META = json.dumps({
    "author_handle": "alice",
    "text": "Gold new ATH",
    "hashtags": ["Gold"],
    "mentioned_handles": [],
    "tickers": ["GC"],
})

_MOCK_LLM_RESPONSE = "# Headlines\n- Gold hit ATH\n\n# Top discussions\n- Group 1: Gold\n\n# Notable links\n- alice tweet\n\n# Sentiment summary\nBullish overall."


@pytest.fixture(autouse=True)
def _no_background(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BACKGROUND_SCRAPE", "false")
    monkeypatch.setenv("BACKGROUND_ENRICH", "false")
    monkeypatch.setenv("BACKGROUND_OCR", "false")
    monkeypatch.setenv("BACKGROUND_DIGESTS", "false")


@pytest.fixture
def db(tmp_path: Path):
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    shutil.copy(FIXTURE_CHAT, archive_dir / "mini_chat.txt")
    db = open_db(archive_dir)
    upsert_article(
        db, "https://x.com/alice/status/1", "ok",
        title="Gold ATH",
        tweet_meta=_TWEET_META,
        fetched_at="2024-06-15T10:00:00",
    )
    return db


@pytest.fixture
def client(tmp_path: Path):
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    shutil.copy(FIXTURE_CHAT, archive_dir / "mini_chat.txt")
    db = open_db(archive_dir)
    upsert_article(
        db, "https://x.com/alice/status/2", "ok",
        title="Gold summary",
        tweet_meta=_TWEET_META,
        fetched_at="2024-06-15T10:00:00",
    )
    db.commit()
    db.close()
    app = create_app(archive_dir)
    return TestClient(app)


# ── Unit tests ────────────────────────────────────────────────────────────────

def test_period_dates_daily():
    start, end = _period_dates("daily", date(2024, 6, 15))
    assert start == "2024-06-14"
    assert end == "2024-06-15"


def test_period_dates_weekly():
    start, end = _period_dates("weekly", date(2024, 6, 15))
    assert start == "2024-06-08"
    assert end == "2024-06-15"


def test_generate_digest_creates_row(db):
    with patch("whatsapp_archive.enrich.digest._call_ollama", return_value=_MOCK_LLM_RESPONSE):
        result = generate_digest(
            db, "http://localhost:11434", period="daily",
            chats={}, end_date=date(2024, 6, 15),
        )

    assert result["period"] == "daily"
    assert result["period_start"] == "2024-06-14"
    assert result["period_end"] == "2024-06-15"
    assert result["body_md"] == _MOCK_LLM_RESPONSE
    assert result["id"] is not None


def test_generate_digest_citations_list(db):
    with patch("whatsapp_archive.enrich.digest._call_ollama", return_value=_MOCK_LLM_RESPONSE):
        result = generate_digest(
            db, "http://localhost:11434", period="daily",
            chats={}, end_date=date(2024, 6, 15),
        )

    assert isinstance(result["citations"], str)  # stored as JSON string in row
    citations = json.loads(result["citations"])
    assert isinstance(citations, list)


def test_generate_digest_idempotent(db):
    with patch("whatsapp_archive.enrich.digest._call_ollama", return_value=_MOCK_LLM_RESPONSE) as mock_llm:
        generate_digest(db, "http://localhost:11434", period="daily", chats={}, end_date=date(2024, 6, 15))
        generate_digest(db, "http://localhost:11434", period="daily", chats={}, end_date=date(2024, 6, 15))

    # LLM called exactly once — second call returns cached
    mock_llm.assert_called_once()


def test_generate_digest_weekly(db):
    with patch("whatsapp_archive.enrich.digest._call_ollama", return_value=_MOCK_LLM_RESPONSE):
        result = generate_digest(
            db, "http://localhost:11434", period="weekly",
            chats={}, end_date=date(2024, 6, 15),
        )

    assert result["period"] == "weekly"
    assert result["period_start"] == "2024-06-08"


def test_generate_digest_ollama_failure(db):
    with patch("whatsapp_archive.enrich.digest._call_ollama", side_effect=Exception("Ollama down")):
        result = generate_digest(
            db, "http://localhost:11434", period="daily",
            chats={}, end_date=date(2024, 6, 15),
        )

    assert "failed" in result["body_md"].lower() or "_" in result["body_md"]


# ── API tests ─────────────────────────────────────────────────────────────────

def test_api_generate_daily(client):
    with patch("whatsapp_archive.enrich.digest._call_ollama", return_value=_MOCK_LLM_RESPONSE):
        r = client.post("/api/digests/generate", json={"period": "daily", "end_date": "2024-06-15"})

    assert r.status_code == 200
    data = r.json()
    assert data["period"] == "daily"
    assert data["body_md"] == _MOCK_LLM_RESPONSE


def test_api_generate_weekly(client):
    with patch("whatsapp_archive.enrich.digest._call_ollama", return_value=_MOCK_LLM_RESPONSE):
        r = client.post("/api/digests/generate", json={"period": "weekly", "end_date": "2024-06-15"})

    assert r.status_code == 200
    assert r.json()["period"] == "weekly"


def test_api_generate_invalid_period(client):
    r = client.post("/api/digests/generate", json={"period": "monthly"})
    assert r.status_code == 422


def test_api_digests_list(client):
    with patch("whatsapp_archive.enrich.digest._call_ollama", return_value=_MOCK_LLM_RESPONSE):
        client.post("/api/digests/generate", json={"period": "daily", "end_date": "2024-06-15"})
        client.post("/api/digests/generate", json={"period": "weekly", "end_date": "2024-06-15"})

    r = client.get("/api/digests")
    assert r.status_code == 200
    assert len(r.json()) >= 2


def test_api_digests_list_filter_period(client):
    with patch("whatsapp_archive.enrich.digest._call_ollama", return_value=_MOCK_LLM_RESPONSE):
        client.post("/api/digests/generate", json={"period": "daily", "end_date": "2024-06-15"})
        client.post("/api/digests/generate", json={"period": "weekly", "end_date": "2024-06-15"})

    r = client.get("/api/digests?period=daily")
    assert r.status_code == 200
    for d in r.json():
        assert d["period"] == "daily"


def test_api_digest_get(client):
    with patch("whatsapp_archive.enrich.digest._call_ollama", return_value=_MOCK_LLM_RESPONSE):
        digest = client.post("/api/digests/generate", json={"period": "daily", "end_date": "2024-06-15"}).json()

    r = client.get(f"/api/digests/{digest['id']}")
    assert r.status_code == 200
    assert r.json()["id"] == digest["id"]


def test_api_digest_get_404(client):
    r = client.get("/api/digests/99999")
    assert r.status_code == 404


def test_api_digest_citations_parsed(client):
    with patch("whatsapp_archive.enrich.digest._call_ollama", return_value=_MOCK_LLM_RESPONSE):
        digest = client.post("/api/digests/generate", json={"period": "daily", "end_date": "2024-06-15"}).json()

    assert isinstance(digest["citations"], list)
