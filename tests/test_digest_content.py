"""Q5 — Digest shows tweet content tests."""
from __future__ import annotations

import json
import shutil
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from whatsapp_archive import create_app
from whatsapp_archive.scrape.db import open_db, upsert_article
from whatsapp_archive.enrich.digest import generate_digest, _build_context

FIXTURE_CHAT = Path(__file__).parent / "fixtures" / "mini_chat.txt"


@pytest.fixture(autouse=True)
def _no_background(monkeypatch):
    for env in [
        "BACKGROUND_SCRAPE", "BACKGROUND_ENRICH", "BACKGROUND_OCR",
        "BACKGROUND_DIGESTS", "BACKGROUND_ENGAGEMENT_REFRESH", "BACKGROUND_CLUSTERING",
        "BACKGROUND_PROFILES",
    ]:
        monkeypatch.setenv(env, "false")


@pytest.fixture
def db_with_tweets(tmp_path: Path):
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    shutil.copy(FIXTURE_CHAT, archive_dir / "mini_chat.txt")
    conn = open_db(archive_dir)

    for i in range(3):
        upsert_article(
            conn,
            f"https://x.com/t/{i}",
            "ok",
            title=f"@miner{i}: Gold production Q{i+1}",
            tweet_meta=json.dumps({
                "author_handle": f"miner{i}",
                "text": f"Gold production up {i+1}0% in Q{i+1} — very exciting results",
                "favorite_count": (i + 1) * 100,
                "retweet_count": (i + 1) * 10,
                "hashtags": ["Gold", "Mining"],
            }),
            published_at=f"2025-01-0{i+1}T10:00:00",
        )
    conn.commit()
    yield conn, archive_dir
    conn.close()


# ── _build_context ─────────────────────────────────────────────────────────────

def test_build_context_includes_tweet_text(db_with_tweets):
    conn, archive_dir = db_with_tweets
    context, citations = _build_context(conn, {}, "2025-01-01", "2025-01-03", None)
    assert "Gold production up" in context
    assert "miner0" in context or "miner1" in context


def test_build_context_includes_author_handle(db_with_tweets):
    conn, _ = db_with_tweets
    context, citations = _build_context(conn, {}, "2025-01-01", "2025-01-03", None)
    assert "@miner" in context


def test_build_context_includes_hashtags(db_with_tweets):
    conn, _ = db_with_tweets
    context, citations = _build_context(conn, {}, "2025-01-01", "2025-01-03", None)
    assert "#Gold" in context or "#Mining" in context


def test_citations_include_tweet_text(db_with_tweets):
    conn, _ = db_with_tweets
    _, citations = _build_context(conn, {}, "2025-01-01", "2025-01-03", None)
    assert len(citations) >= 1
    tweet_citations = [c for c in citations if c.get("tweet_text")]
    assert len(tweet_citations) >= 1
    assert "Gold production" in tweet_citations[0]["tweet_text"]


def test_citations_include_author(db_with_tweets):
    conn, _ = db_with_tweets
    _, citations = _build_context(conn, {}, "2025-01-01", "2025-01-03", None)
    authors = [c.get("author") for c in citations if c.get("author")]
    assert len(authors) >= 1
    assert any("miner" in a for a in authors)


def test_citations_include_hashtags(db_with_tweets):
    conn, _ = db_with_tweets
    _, citations = _build_context(conn, {}, "2025-01-01", "2025-01-03", None)
    tags = [tag for c in citations for tag in (c.get("hashtags") or [])]
    assert "Gold" in tags or "Mining" in tags


# ── generate_digest with mocked LLM ──────────────────────────────────────────

def test_generate_digest_body_contains_tweet_text(db_with_tweets):
    conn, archive_dir = db_with_tweets
    from whatsapp_archive import create_app as _create_app
    # Mock the LLM call so we can see what context was passed
    captured_context = {}

    def mock_call_ollama(ollama_url, context, period_start, period_end, model):
        captured_context["context"] = context
        return "# Headlines\n- Gold production rising\n\n# Top discussions\n- See context.\n\n# Notable links\n- See tweets.\n\n# Sentiment summary\nBullish."

    with patch("whatsapp_archive.enrich.digest._call_ollama", mock_call_ollama):
        result = generate_digest(
            conn,
            "http://localhost:11434",
            "daily",
            {},
            end_date=date(2025, 1, 3),
        )

    assert result["body_md"] is not None
    assert "Gold production" in captured_context.get("context", "")


def test_generate_digest_citations_serialized_as_json(db_with_tweets):
    conn, _ = db_with_tweets

    def mock_call_ollama(*args, **kwargs):
        return "# Headlines\n- Test.\n\n# Top discussions\n- Test.\n\n# Notable links\n- Test.\n\n# Sentiment summary\nNeutral."

    with patch("whatsapp_archive.enrich.digest._call_ollama", mock_call_ollama):
        result = generate_digest(
            conn, "http://localhost:11434", "daily", {},
            end_date=date(2025, 1, 4),
        )

    citations = result.get("citations")
    if isinstance(citations, str):
        citations = json.loads(citations)
    assert isinstance(citations, list)
    if citations:
        assert "tweet_text" in citations[0] or "url" in citations[0]
