"""F2 — /api/search returns hits from scraped article + enrichment summary.

Spins up create_app() against a temp archive dir, seeds the SQLite `articles` +
`article_enrichments` tables directly (skipping the actual scraper), then
exercises the search endpoint via FastAPI's TestClient.
"""

from __future__ import annotations

import os
import shutil
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from whatsapp_archive import create_app
from whatsapp_archive.scrape.db import (
    open_db,
    upsert_article,
    upsert_enrichment,
)


FIXTURE_CHAT = Path(__file__).parent / "fixtures" / "mini_chat.txt"


@pytest.fixture(autouse=True)
def _no_background_loops(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep tests offline — the app would otherwise spawn scrape + enrich tasks."""
    monkeypatch.setenv("BACKGROUND_SCRAPE", "false")
    monkeypatch.setenv("BACKGROUND_ENRICH", "false")


@pytest.fixture
def app_with_articles(tmp_path: Path) -> TestClient:
    """Create app over a temp archive dir with one chat + two seeded articles."""
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    shutil.copy(FIXTURE_CHAT, archive_dir / "WhatsApp Chat with Test.txt")

    # Seed the DB BEFORE create_app — open_db will pick up the existing rows
    # (open_db is idempotent thanks to CREATE TABLE IF NOT EXISTS).
    db = open_db(archive_dir)

    # Article #1 — content matching FTS; URL is one that mini_chat references.
    aid1 = upsert_article(
        db,
        "https://example.com/article?q=test",
        "ok",
        title="Gold rally and silver",
        raw_text=(
            "The gold rally continued as investors moved into precious metals. "
            "Silver also gained on hopes of an industrial demand bump."
        ),
        scrape_method="trafilatura",
        fetched_at=datetime.utcnow().isoformat(),
    )
    # Rebuild FTS so the seeded rows are searchable.
    db.execute("INSERT INTO articles_fts(articles_fts) VALUES('rebuild')")
    db.commit()

    # Article #2 — not linked from any chat; tests the null-chat path.
    aid2 = upsert_article(
        db,
        "https://orphan.example.com/standalone",
        "ok",
        title="Standalone article",
        raw_text="Lorem ipsum dolor sit amet, consectetur adipiscing elit.",
        scrape_method="trafilatura",
        fetched_at=datetime.utcnow().isoformat(),
    )
    db.execute("INSERT INTO articles_fts(articles_fts) VALUES('rebuild')")
    db.commit()

    # Enrichment on article #2 only — sentinel phrase only in the summary.
    upsert_enrichment(
        db,
        article_id=aid2,
        summary="A summary describing zircon mining trends in 2025.",
        categories='["mining"]',
        entities="[]",
        sentiment="neutral",
        model="qwen2.5:3b-instruct",
        enriched_at=datetime.utcnow().isoformat(),
    )

    db.close()

    # Now create the FastAPI app — its open_db re-uses the existing DB file.
    app = create_app(archive_dir, ollama_url="http://localhost:11434")
    return TestClient(app)


def test_search_message_only(app_with_articles: TestClient) -> None:
    r = app_with_articles.get("/api/search", params={"q": "Hello", "kind": "message"})
    assert r.status_code == 200
    body = r.json()
    assert all(hit["kind"] == "message" for hit in body["results"])
    assert any("hello" in hit["entry"]["body"].lower() for hit in body["results"])


def test_search_article_fts_hit(app_with_articles: TestClient) -> None:
    """A word that appears in raw_text of a seeded article surfaces as kind=article."""
    r = app_with_articles.get("/api/search", params={"q": "rally", "kind": "all"})
    assert r.status_code == 200
    body = r.json()
    article_hits = [h for h in body["results"] if h["kind"] == "article"]
    assert article_hits, f"expected an article hit for 'rally'; got {body['results']}"
    hit = article_hits[0]
    assert hit["title"] == "Gold rally and silver"
    assert "<mark>" in hit["snippet"]  # FTS snippet wraps the match
    # mini_chat.txt links this URL from the "Test" chat → attribution must fill.
    assert hit["chat_id"] == "whatsapp-chat-with-test"
    assert hit["chat_name"] == "WhatsApp Chat with Test"


def test_search_article_summary_hit(app_with_articles: TestClient) -> None:
    """A phrase only in enrichment.summary surfaces via the LIKE pass."""
    r = app_with_articles.get("/api/search", params={"q": "zircon", "kind": "article"})
    assert r.status_code == 200
    article_hits = r.json()["results"]
    assert article_hits, "expected an article hit from the summary scan"
    hit = article_hits[0]
    assert hit["url"] == "https://orphan.example.com/standalone"
    assert "zircon" in hit["snippet"].lower()
    # Orphan article — not linked from any chat.
    assert hit["chat_id"] is None
    assert hit["chat_name"] is None


def test_search_kind_filter_messages_excludes_articles(app_with_articles: TestClient) -> None:
    r = app_with_articles.get("/api/search", params={"q": "rally", "kind": "message"})
    assert r.status_code == 200
    assert all(hit["kind"] == "message" for hit in r.json()["results"])


def test_search_kind_filter_articles_excludes_messages(app_with_articles: TestClient) -> None:
    r = app_with_articles.get("/api/search", params={"q": "Hello", "kind": "article"})
    assert r.status_code == 200
    # No article contains "Hello", so this should be empty.
    assert r.json()["results"] == []


def test_search_hit_shape(app_with_articles: TestClient) -> None:
    r = app_with_articles.get("/api/search", params={"q": "rally", "kind": "article"})
    hit = next(h for h in r.json()["results"] if h["kind"] == "article")
    for key in ("kind", "article_id", "url", "title", "snippet", "chat_id", "chat_name", "score"):
        assert key in hit, f"missing key {key} in article hit: {hit}"
