"""Q18 — Digest source cards: verify citation structure for frontend inline cards."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from whatsapp_archive.scrape.db import open_db, upsert_article
from whatsapp_archive.enrich.digest import _build_context

FIXTURE_CHAT = Path(__file__).parent / "fixtures" / "mini_chat.txt"


@pytest.fixture
def db_with_tweets(tmp_path: Path):
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    shutil.copy(FIXTURE_CHAT, archive_dir / "mini_chat.txt")
    conn = open_db(archive_dir)
    for i in range(3):
        upsert_article(
            conn,
            f"https://x.com/srctest/{i}",
            "ok",
            title=f"Source test {i}",
            tweet_meta=json.dumps({
                "author_handle": f"user{i}",
                "text": f"Source tweet {i} body text",
                "favorite_count": i * 50,
                "retweet_count": i * 5,
                "hashtags": [f"tag{i}"],
            }),
            published_at=f"2025-02-0{i+1}T10:00:00",
        )
    conn.commit()
    yield conn
    conn.close()


# ── Context contains [source ↗] markers ───────────────────────────────────────

def test_context_contains_source_link(db_with_tweets):
    context, _ = _build_context(db_with_tweets, {}, "2025-02-01", "2025-02-03", None)
    assert "[source ↗]" in context


def test_source_link_contains_url(db_with_tweets):
    context, _ = _build_context(db_with_tweets, {}, "2025-02-01", "2025-02-03", None)
    assert "https://x.com/srctest/" in context


# ── Citations have article_id field ───────────────────────────────────────────

def test_citations_have_article_id(db_with_tweets):
    _, citations = _build_context(db_with_tweets, {}, "2025-02-01", "2025-02-03", None)
    assert len(citations) >= 1
    for c in citations:
        assert "article_id" in c
        assert isinstance(c["article_id"], int)


def test_citations_article_id_is_nonzero(db_with_tweets):
    _, citations = _build_context(db_with_tweets, {}, "2025-02-01", "2025-02-03", None)
    for c in citations:
        assert c["article_id"] > 0


def test_citations_url_matches_article(db_with_tweets):
    _, citations = _build_context(db_with_tweets, {}, "2025-02-01", "2025-02-03", None)
    urls = [c["url"] for c in citations]
    assert any("srctest" in u for u in urls)


# ── Source link url matches citation url ──────────────────────────────────────

def test_source_link_url_in_citations(db_with_tweets):
    context, citations = _build_context(db_with_tweets, {}, "2025-02-01", "2025-02-03", None)
    citation_urls = {c["url"] for c in citations}
    # Every [source ↗](url) in context should have a matching citation
    import re
    found = re.findall(r'\[source ↗\]\(([^)]+)\)', context)
    assert found, "No source links found in context"
    for url in found:
        assert url in citation_urls, f"Source URL {url!r} not in citations"


# ── Non-tweet articles: citation has no tweet_text ────────────────────────────

def test_non_tweet_citation_has_no_tweet_text(tmp_path):
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    shutil.copy(FIXTURE_CHAT, archive_dir / "mini_chat.txt")
    conn = open_db(archive_dir)
    upsert_article(
        conn, "https://example.com/article", "ok",
        title="A non-tweet article",
        tweet_meta=None,
        published_at="2025-03-01T10:00:00",
    )
    conn.commit()
    _, citations = _build_context(conn, {}, "2025-03-01", "2025-03-01", None)
    non_tweet = [c for c in citations if not c.get("tweet_text")]
    assert len(non_tweet) >= 1
    conn.close()
