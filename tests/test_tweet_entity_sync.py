"""E1 — sync_tweet_entities tests."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from whatsapp_archive.scrape.db import open_db, sync_tweet_entities, upsert_article

FIXTURE_CHAT = Path(__file__).parent / "fixtures" / "mini_chat.txt"

_TWEET_META = {
    "author_handle": "alice",
    "author_name": "Alice",
    "mentioned_handles": ["bob", "charlie"],
    "hashtags": ["gold", "mining"],
    "tickers": ["VGZ"],
    "favorite_count": 10,
}


@pytest.fixture
def db(tmp_path: Path):
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    shutil.copy(FIXTURE_CHAT, archive_dir / "mini_chat.txt")
    conn = open_db(archive_dir)
    yield conn
    conn.close()


def _insert_article(conn, url: str, meta: dict) -> int:
    return upsert_article(conn, url, "ok", tweet_meta=json.dumps(meta))


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_six_entities_from_one_article(db):
    art_id = _insert_article(db, "https://x.com/1", _TWEET_META)
    counts = sync_tweet_entities(db, art_id, _TWEET_META)

    assert counts["authors"] == 1
    assert counts["mentions"] == 2
    assert counts["hashtags"] == 2
    assert counts["tickers"] == 1

    rows = db.execute("SELECT COUNT(*) AS n FROM entities").fetchone()
    assert rows["n"] == 6


def test_entities_have_correct_kinds(db):
    art_id = _insert_article(db, "https://x.com/1", _TWEET_META)
    sync_tweet_entities(db, art_id, _TWEET_META)

    kinds = {r["kind"] for r in db.execute("SELECT kind FROM entities").fetchall()}
    assert kinds == {"author", "mention", "hashtag", "ticker"}


def test_entities_have_correct_normalized_names(db):
    art_id = _insert_article(db, "https://x.com/1", _TWEET_META)
    sync_tweet_entities(db, art_id, _TWEET_META)

    names = {r["normalized_name"] for r in db.execute("SELECT normalized_name FROM entities").fetchall()}
    assert "alice" in names
    assert "bob" in names
    assert "charlie" in names
    assert "gold" in names
    assert "mining" in names
    assert "vgz" in names


def test_all_linked_to_article(db):
    art_id = _insert_article(db, "https://x.com/1", _TWEET_META)
    sync_tweet_entities(db, art_id, _TWEET_META)

    linked = db.execute(
        "SELECT COUNT(*) AS n FROM article_entities WHERE article_id=? AND assigned_by='tweet_meta'",
        (art_id,),
    ).fetchone()
    assert linked["n"] == 6


def test_second_call_is_noop(db):
    art_id = _insert_article(db, "https://x.com/1", _TWEET_META)
    sync_tweet_entities(db, art_id, _TWEET_META)
    counts2 = sync_tweet_entities(db, art_id, _TWEET_META)

    # Returns same counts (new article links attempted)
    assert counts2["authors"] == 1
    # But no duplicate entity rows created
    rows = db.execute("SELECT COUNT(*) AS n FROM entities").fetchone()
    assert rows["n"] == 6
    linked = db.execute("SELECT COUNT(*) AS n FROM article_entities WHERE article_id=?", (art_id,)).fetchone()
    assert linked["n"] == 6


def test_two_articles_share_hashtag_entity(db):
    meta1 = {**_TWEET_META, "author_handle": "user1", "mentioned_handles": [], "tickers": []}
    meta2 = {**_TWEET_META, "author_handle": "user2", "mentioned_handles": [], "tickers": []}

    id1 = _insert_article(db, "https://x.com/1", meta1)
    id2 = _insert_article(db, "https://x.com/2", meta2)
    sync_tweet_entities(db, id1, meta1)
    sync_tweet_entities(db, id2, meta2)

    # Hashtag entities created only once each (shared)
    gold_rows = db.execute(
        "SELECT COUNT(*) AS n FROM entities WHERE normalized_name='gold' AND kind='hashtag'"
    ).fetchone()
    assert gold_rows["n"] == 1

    # But both articles are linked
    article_links = db.execute(
        "SELECT COUNT(*) AS n FROM article_entities ae"
        " JOIN entities e ON e.id = ae.entity_id"
        " WHERE e.normalized_name='gold' AND e.kind='hashtag'"
    ).fetchone()
    assert article_links["n"] == 2


def test_canonical_id_not_overwritten(db):
    # Pre-create entity with canonical_id set (simulating a merge)
    db.execute("INSERT INTO entities (name, kind, normalized_name) VALUES ('Alice', 'author', 'alice')")
    db.execute("INSERT INTO entities (name, kind, normalized_name) VALUES ('AliceAlt', 'author', 'alicealt')")
    db.commit()
    alice_id = db.execute("SELECT id FROM entities WHERE normalized_name='alice' AND kind='author'").fetchone()["id"]
    alt_id = db.execute("SELECT id FROM entities WHERE normalized_name='alicealt' AND kind='author'").fetchone()["id"]
    # Mark alice as alias of alt
    db.execute("UPDATE entities SET canonical_id=? WHERE id=?", (alt_id, alice_id))
    db.commit()

    art_id = _insert_article(db, "https://x.com/1", _TWEET_META)
    sync_tweet_entities(db, art_id, _TWEET_META)

    # canonical_id on alice must still point to alt (not cleared)
    row = db.execute("SELECT canonical_id FROM entities WHERE id=?", (alice_id,)).fetchone()
    assert row["canonical_id"] == alt_id


def test_empty_tweet_meta_returns_zeros(db):
    art_id = _insert_article(db, "https://x.com/1", {})
    counts = sync_tweet_entities(db, art_id, {})
    assert counts == {"authors": 0, "mentions": 0, "hashtags": 0, "tickers": 0}


def test_none_fields_are_skipped(db):
    meta = {"author_handle": None, "mentioned_handles": None, "hashtags": None, "tickers": None}
    art_id = _insert_article(db, "https://x.com/1", meta)
    counts = sync_tweet_entities(db, art_id, meta)
    assert counts == {"authors": 0, "mentions": 0, "hashtags": 0, "tickers": 0}
    n = db.execute("SELECT COUNT(*) AS n FROM entities").fetchone()["n"]
    assert n == 0
