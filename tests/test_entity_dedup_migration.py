"""Bug #31 — entity dedup migration: collapse multi-kind canonical duplicates."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from whatsapp_archive.scrape.db import open_db, upsert_entity, _ensure_entity_dedup

FIXTURE_CHAT = Path(__file__).parent / "fixtures" / "mini_chat.txt"


@pytest.fixture
def fresh_db(tmp_path):
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    shutil.copy(FIXTURE_CHAT, archive_dir / "mini_chat.txt")
    conn = open_db(archive_dir)
    yield conn
    conn.close()


# ── Partial unique index created ──────────────────────────────────────────────

def test_partial_unique_index_exists(fresh_db):
    row = fresh_db.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name='uq_entities_norm_canonical'"
    ).fetchone()
    assert row is not None, "uq_entities_norm_canonical index not created"


def test_upsert_same_name_different_kind_returns_same_id(fresh_db):
    id1 = upsert_entity(fresh_db, "GoldCorp", "company")
    id2 = upsert_entity(fresh_db, "GoldCorp", "ticker")
    assert id1 == id2, "Different kinds for same name should return the same canonical id"


def test_canonical_rows_unique_normalized_name(fresh_db):
    upsert_entity(fresh_db, "SilverMine", "company")
    upsert_entity(fresh_db, "SilverMine", "other")
    rows = fresh_db.execute(
        "SELECT COUNT(*) AS c FROM entities WHERE normalized_name='silvermine' AND canonical_id IS NULL"
    ).fetchone()
    assert rows["c"] == 1


# ── Migration collapses pre-existing duplicates ───────────────────────────────

def _inject_legacy_duplicates(conn, rows):
    """Drop the dedup unique index, insert duplicate entity rows, recreate nothing.
    Simulates a pre-migration state. Call conn.close() after, then open_db() again."""
    conn.execute("DROP INDEX IF EXISTS uq_entities_norm_canonical")
    for name, kind, norm in rows:
        conn.execute(
            "INSERT INTO entities (name, kind, normalized_name) VALUES (?,?,?)",
            (name, kind, norm),
        )
    conn.commit()


def test_migration_collapses_existing_duplicates(tmp_path):
    """Simulate a pre-Q12 DB where multi-kind rows exist, then run the migration."""
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    shutil.copy(FIXTURE_CHAT, archive_dir / "mini_chat.txt")

    conn = open_db(archive_dir)
    _inject_legacy_duplicates(conn, [
        ("Gold", "other", "gold"),
        ("Gold", "ticker", "gold"),
        ("Gold", "company", "gold"),
    ])
    conn.close()

    conn2 = open_db(archive_dir)
    canonical_count = conn2.execute(
        "SELECT COUNT(*) AS c FROM entities WHERE normalized_name='gold' AND canonical_id IS NULL"
    ).fetchone()["c"]
    assert canonical_count == 1, f"Expected 1 canonical gold, got {canonical_count}"
    conn2.close()


def test_migration_picks_ticker_as_canonical(tmp_path):
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    shutil.copy(FIXTURE_CHAT, archive_dir / "mini_chat.txt")

    conn = open_db(archive_dir)
    _inject_legacy_duplicates(conn, [
        ("Silver", "other", "silver"),
        ("Silver", "ticker", "silver"),
    ])
    conn.close()

    conn2 = open_db(archive_dir)
    canonical = conn2.execute(
        "SELECT kind FROM entities WHERE normalized_name='silver' AND canonical_id IS NULL"
    ).fetchone()
    assert canonical is not None
    assert canonical["kind"] == "ticker", f"Expected ticker canonical, got {canonical['kind']}"
    conn2.close()


def test_no_multi_kind_canonicals_after_migration(fresh_db):
    """The core verify condition from the bug report."""
    rows = fresh_db.execute(
        "SELECT normalized_name, COUNT(DISTINCT kind) AS kinds "
        "FROM entities WHERE canonical_id IS NULL "
        "GROUP BY normalized_name HAVING kinds > 1"
    ).fetchall()
    assert len(rows) == 0, f"Multi-kind canonicals still exist: {[dict(r) for r in rows]}"


def test_article_entities_migrated_to_canonical(tmp_path):
    """Articles tagged to a non-canonical entity are migrated to canonical after dedup."""
    import json

    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    shutil.copy(FIXTURE_CHAT, archive_dir / "mini_chat.txt")

    # Full schema via open_db, insert article
    from whatsapp_archive.scrape.db import upsert_article
    conn = open_db(archive_dir)
    aid = upsert_article(conn, "https://x.com/t/99", "ok", title="Platinum news",
                         tweet_meta=json.dumps({"author_handle": "t", "text": "x"}))

    # Drop the dedup index so we can inject legacy duplicate entities
    conn.execute("DROP INDEX IF EXISTS uq_entities_norm_canonical")
    conn.execute("INSERT INTO entities (name, kind, normalized_name) VALUES (?,?,?)",
                 ("Platinum", "other", "platinum"))
    conn.execute("INSERT INTO entities (name, kind, normalized_name) VALUES (?,?,?)",
                 ("Platinum", "ticker", "platinum"))
    conn.commit()

    # Link article to the lower-rank ("other") entity
    other_id = conn.execute(
        "SELECT id FROM entities WHERE normalized_name='platinum' AND kind='other' AND canonical_id IS NULL"
    ).fetchone()["id"]
    conn.execute("INSERT OR IGNORE INTO article_entities (article_id, entity_id) VALUES (?,?)",
                 (aid, other_id))
    conn.commit()
    conn.close()

    # Re-open triggers _ensure_entity_dedup migration
    conn2 = open_db(archive_dir)
    canonical = conn2.execute(
        "SELECT id FROM entities WHERE normalized_name='platinum' AND canonical_id IS NULL"
    ).fetchone()
    assert canonical is not None
    linked = conn2.execute(
        "SELECT * FROM article_entities WHERE article_id=? AND entity_id=?",
        (aid, canonical["id"])
    ).fetchone()
    assert linked is not None, "Article not migrated to canonical entity after dedup"
    conn2.close()
