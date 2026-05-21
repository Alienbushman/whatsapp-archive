"""Q4 — RAG chatbot retrieval tests."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from whatsapp_archive import create_app
from whatsapp_archive.scrape.db import (
    open_db, upsert_article, upsert_entity, link_article_entity,
)
from whatsapp_archive.enrich.rag import build_context, _rag_fts_query

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
def db_client(tmp_path: Path):
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    shutil.copy(FIXTURE_CHAT, archive_dir / "mini_chat.txt")
    conn = open_db(archive_dir)

    eid = upsert_entity(conn, "Vista Gold", "company")
    aid = upsert_article(
        conn, "https://x.com/vg/1", "ok",
        title="@vgcorp: Production update for Vista Gold Q1",
        tweet_meta=json.dumps({
            "author_handle": "vgcorp",
            "text": "Production update for Vista Gold Q1 — very positive results",
        }),
        published_at="2025-01-01T10:00:00",
    )
    link_article_entity(conn, aid, eid, "Vista Gold")
    conn.commit()
    client = TestClient(create_app(archive_dir))
    yield conn, client, aid, eid
    conn.close()


# ── _rag_fts_query ────────────────────────────────────────────────────────────

def test_fts_query_strips_stop_words():
    q = _rag_fts_query("Tell me about the tweets of Vista Gold")
    tokens = q.split()
    assert "tell" not in tokens
    assert "me" not in tokens
    assert "about" not in tokens
    assert "the" not in tokens
    assert "of" not in tokens
    assert "vista" in tokens
    assert "gold" in tokens


def test_fts_query_preserves_entity_name():
    q = _rag_fts_query("What has Vista Gold been doing?")
    assert "vista" in q.lower()
    assert "gold" in q.lower()


def test_fts_query_empty_after_stop_words_falls_back():
    q = _rag_fts_query("tell me about it")
    assert len(q) > 0


# ── build_context tweet formatting ───────────────────────────────────────────

def test_build_context_includes_tweet_author():
    art_hits = [{
        "url": "https://x.com/vg/1",
        "title": "@vgcorp: Vista Gold update",
        "body": "Production update for Vista Gold",
        "tweet_meta": json.dumps({"author_handle": "vgcorp", "text": "Production update for Vista Gold"}),
        "summary": "",
        "score": 10.0,
    }]
    blocks = build_context([], art_hits)
    assert any("vgcorp" in b for b in blocks)


def test_build_context_uses_tweet_text_not_summary():
    art_hits = [{
        "url": "https://x.com/vg/1",
        "title": "Title",
        "body": "Production update for Vista Gold",
        "tweet_meta": json.dumps({"author_handle": "vgcorp", "text": "Production update for Vista Gold"}),
        "summary": "LLM summary irrelevant here",
        "score": 10.0,
    }]
    blocks = build_context([], art_hits)
    full = " ".join(blocks)
    assert "Production update" in full


# ── Entity-aware retrieval via /api/chat/ask ─────────────────────────────────

def test_chat_ask_entity_retrieval_returns_stream(db_client, monkeypatch):
    _, client, aid, _ = db_client

    def _fake_stream(question, context_blocks, *args, **kwargs):
        # Verify entity articles are in context
        full_ctx = " ".join(context_blocks)
        assert "Vista Gold" in full_ctx or "vgcorp" in full_ctx
        yield "data: {\"token\": \"ok\"}\n\n"
        yield "data: [DONE]\n\n"

    monkeypatch.setattr("whatsapp_archive.enrich.rag.stream_answer", _fake_stream)
    r = client.post("/api/chat/ask", json={"question": "Tell me about Vista Gold tweets", "k": 5})
    assert r.status_code == 200


def test_rag_fts_or_query_finds_entity_articles():
    """Keyword OR query finds articles containing any extracted keyword."""
    import sqlite3
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE VIRTUAL TABLE articles_fts USING fts5(title, raw_text, tweet_search_blob);
        INSERT INTO articles_fts VALUES(
            "@vgcorp: Production update for Vista Gold Q1",
            "Production update for Vista Gold Q1 very positive results",
            "@vgcorp"
        );
    """)

    keywords = _rag_fts_query("Tell me about the tweets of Vista Gold")
    assert "OR" in keywords or "vista" in keywords.lower()

    rows_kw = conn.execute(
        "SELECT * FROM articles_fts WHERE articles_fts MATCH ?", (keywords,)
    ).fetchall()

    assert len(rows_kw) >= 1, f"OR keyword query '{keywords}' should find the Vista Gold article"
