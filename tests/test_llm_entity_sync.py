"""E2 — sync_llm_entities tests."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from whatsapp_archive.enrich.entity_sync import sync_llm_entities
from whatsapp_archive.scrape.db import open_db, upsert_article, upsert_enrichment

FIXTURE_CHAT = Path(__file__).parent / "fixtures" / "mini_chat.txt"

_ENTITIES = [
    {"name": "Vista Gold", "kind": "company", "mention_text": "Vista Gold"},
    {"name": "$NDX", "kind": "ticker", "mention_text": "$NDX"},
]


@pytest.fixture
def db(tmp_path: Path):
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    shutil.copy(FIXTURE_CHAT, archive_dir / "mini_chat.txt")
    conn = open_db(archive_dir)
    yield conn
    conn.close()


def _seed_article(conn, url: str) -> int:
    return upsert_article(conn, url, "ok", title="Test article")


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_two_entities_created(db):
    art_id = _seed_article(db, "https://x.com/1")
    counts = sync_llm_entities(db, art_id, _ENTITIES)

    assert counts["company"] == 1
    assert counts["ticker"] == 1
    assert counts["total"] == 2

    n = db.execute("SELECT COUNT(*) AS n FROM entities").fetchone()["n"]
    assert n == 2


def test_entity_kinds_correct(db):
    art_id = _seed_article(db, "https://x.com/1")
    sync_llm_entities(db, art_id, _ENTITIES)

    kinds = {r["kind"] for r in db.execute("SELECT kind FROM entities").fetchall()}
    assert "company" in kinds
    assert "ticker" in kinds


def test_article_entities_linked_with_llm(db):
    art_id = _seed_article(db, "https://x.com/1")
    sync_llm_entities(db, art_id, _ENTITIES)

    linked = db.execute(
        "SELECT COUNT(*) AS n FROM article_entities WHERE article_id=? AND assigned_by='llm'",
        (art_id,),
    ).fetchone()
    assert linked["n"] == 2


def test_second_call_is_noop(db):
    art_id = _seed_article(db, "https://x.com/1")
    sync_llm_entities(db, art_id, _ENTITIES)
    counts2 = sync_llm_entities(db, art_id, _ENTITIES)

    # Still returns the same counts (idempotent)
    assert counts2["total"] == 2
    # No duplicate entity rows
    n = db.execute("SELECT COUNT(*) AS n FROM entities").fetchone()["n"]
    assert n == 2
    # No duplicate article_entities rows
    links = db.execute(
        "SELECT COUNT(*) AS n FROM article_entities WHERE article_id=?", (art_id,)
    ).fetchone()
    assert links["n"] == 2


def test_company_and_mention_stay_distinct(db):
    """LLM company 'Apple' and tweet_meta mention '@apple' are different kinds — no auto-merge."""
    from whatsapp_archive.scrape.db import sync_tweet_entities

    art_id = _seed_article(db, "https://x.com/1")
    sync_llm_entities(db, art_id, [{"name": "Apple", "kind": "company"}])
    sync_tweet_entities(db, art_id, {"author_handle": "apple", "mentioned_handles": ["apple"]})

    # company 'apple' and mention 'apple' — distinct entities by kind
    company_row = db.execute(
        "SELECT id FROM entities WHERE normalized_name='apple' AND kind='company'"
    ).fetchone()
    mention_row = db.execute(
        "SELECT id FROM entities WHERE normalized_name='apple' AND kind='mention'"
    ).fetchone()
    assert company_row is not None
    assert mention_row is not None
    assert company_row["id"] != mention_row["id"]


def test_ticker_dedup_across_llm_and_tweet_meta(db):
    """$NDX from LLM (kind=ticker) and $NDX from tweet_meta (kind=ticker) — same entity row."""
    from whatsapp_archive.scrape.db import sync_tweet_entities

    art_id = _seed_article(db, "https://x.com/1")
    sync_llm_entities(db, art_id, [{"name": "NDX", "kind": "ticker"}])
    sync_tweet_entities(db, art_id, {"tickers": ["NDX"]})

    # Both use kind='ticker' and normalized_name='ndx' — should be ONE entity
    ticker_rows = db.execute(
        "SELECT COUNT(*) AS n FROM entities WHERE normalized_name='ndx' AND kind='ticker'"
    ).fetchone()
    assert ticker_rows["n"] == 1


def test_empty_list_returns_zeros(db):
    art_id = _seed_article(db, "https://x.com/1")
    counts = sync_llm_entities(db, art_id, [])
    assert counts["total"] == 0
    n = db.execute("SELECT COUNT(*) AS n FROM entities").fetchone()["n"]
    assert n == 0


def test_entries_without_name_skipped(db):
    art_id = _seed_article(db, "https://x.com/1")
    counts = sync_llm_entities(db, art_id, [{"name": "", "kind": "company"}, {"kind": "ticker"}])
    assert counts["total"] == 0


def test_mention_text_preserved(db):
    art_id = _seed_article(db, "https://x.com/1")
    sync_llm_entities(db, art_id, [{"name": "Vista Gold", "kind": "company", "mention_text": "Vista Gold Corp"}])

    row = db.execute(
        "SELECT mention_text FROM article_entities ae"
        " JOIN entities e ON e.id = ae.entity_id"
        " WHERE e.normalized_name='vista gold' AND ae.article_id=?",
        (art_id,),
    ).fetchone()
    assert row is not None
    assert row["mention_text"] == "Vista Gold Corp"
