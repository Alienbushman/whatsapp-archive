"""E4 — Tweet quote/reply link tests."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from whatsapp_archive import create_app
from whatsapp_archive.scrape.db import (
    open_db,
    upsert_article,
    sync_tweet_links,
    resolve_dangling_tweet_links,
    get_quoted_by,
    get_replied_by,
    get_article_tweet_links,
)

FIXTURE_CHAT = Path(__file__).parent / "fixtures" / "mini_chat.txt"


@pytest.fixture(autouse=True)
def _no_background(monkeypatch):
    for env in [
        "BACKGROUND_SCRAPE", "BACKGROUND_ENRICH", "BACKGROUND_OCR",
        "BACKGROUND_DIGESTS", "BACKGROUND_ENGAGEMENT_REFRESH", "BACKGROUND_CLUSTERING",
    ]:
        monkeypatch.setenv(env, "false")


@pytest.fixture
def db(tmp_path: Path):
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    shutil.copy(FIXTURE_CHAT, archive_dir / "mini_chat.txt")
    conn = open_db(archive_dir)
    yield conn
    conn.close()


@pytest.fixture
def client(tmp_path: Path):
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    shutil.copy(FIXTURE_CHAT, archive_dir / "mini_chat.txt")
    return TestClient(create_app(archive_dir))


def _seed_tweet(conn, url: str, tweet_id: str, meta_extras: dict | None = None) -> int:
    meta = {"tweet_id": tweet_id, "author_handle": "user", "text": f"Tweet {tweet_id}"}
    if meta_extras:
        meta.update(meta_extras)
    upsert_article(conn, url, "ok", title=f"Tweet {tweet_id}")
    art_id = conn.execute("SELECT id FROM articles WHERE url=?", (url,)).fetchone()["id"]
    conn.execute("UPDATE articles SET tweet_meta=? WHERE id=?", (json.dumps(meta), art_id))
    conn.commit()
    return art_id


# ── Unit tests ────────────────────────────────────────────────────────────────

def test_sync_tweet_links_quoted(db):
    a_id = _seed_tweet(db, "https://x.com/a/1", "tweet_A")
    b_id = _seed_tweet(db, "https://x.com/b/2", "tweet_B",
                       {"quoted_tweet": {"tweet_id": "tweet_A", "text": "original"}})
    sync_tweet_links(db, b_id, json.loads(
        db.execute("SELECT tweet_meta FROM articles WHERE id=?", (b_id,)).fetchone()["tweet_meta"]
        if False else json.dumps({"quoted_tweet": {"tweet_id": "tweet_A"}})))
    rows = db.execute("SELECT * FROM tweet_links WHERE src_article_id=?", (b_id,)).fetchall()
    assert len(rows) == 1
    assert rows[0]["kind"] == "quoted"
    assert rows[0]["dst_tweet_id"] == "tweet_A"
    assert rows[0]["dst_article_id"] == a_id


def test_sync_tweet_links_reply(db):
    a_id = _seed_tweet(db, "https://x.com/a/1", "tweet_A")
    b_id = _seed_tweet(db, "https://x.com/b/2", "tweet_B")
    sync_tweet_links(db, b_id, {"in_reply_to_status_id": "tweet_A"})
    rows = db.execute("SELECT * FROM tweet_links WHERE src_article_id=?", (b_id,)).fetchall()
    assert len(rows) == 1
    assert rows[0]["kind"] == "reply"
    assert rows[0]["dst_tweet_id"] == "tweet_A"
    assert rows[0]["dst_article_id"] == a_id


def test_sync_tweet_links_dangling(db):
    b_id = _seed_tweet(db, "https://x.com/b/2", "tweet_B")
    sync_tweet_links(db, b_id, {"quoted_tweet": {"tweet_id": "tweet_MISSING"}})
    row = db.execute("SELECT * FROM tweet_links WHERE src_article_id=?", (b_id,)).fetchone()
    assert row is not None
    assert row["dst_article_id"] is None


def test_resolve_dangling_tweet_links(db):
    b_id = _seed_tweet(db, "https://x.com/b/2", "tweet_B")
    sync_tweet_links(db, b_id, {"quoted_tweet": {"tweet_id": "tweet_A_new"}})
    # Dangling at this point
    row = db.execute("SELECT dst_article_id FROM tweet_links WHERE dst_tweet_id='tweet_A_new'").fetchone()
    assert row["dst_article_id"] is None

    # Now add the missing article
    a_id = _seed_tweet(db, "https://x.com/a/1", "tweet_A_new")
    n = resolve_dangling_tweet_links(db, a_id, "tweet_A_new")
    assert n == 1
    row = db.execute("SELECT dst_article_id FROM tweet_links WHERE dst_tweet_id='tweet_A_new'").fetchone()
    assert row["dst_article_id"] == a_id


def test_get_quoted_by(db):
    a_id = _seed_tweet(db, "https://x.com/a/1", "tweet_A")
    b_id = _seed_tweet(db, "https://x.com/b/2", "tweet_B")
    c_id = _seed_tweet(db, "https://x.com/c/3", "tweet_C")
    sync_tweet_links(db, b_id, {"quoted_tweet": {"tweet_id": "tweet_A"}})
    sync_tweet_links(db, c_id, {"quoted_tweet": {"tweet_id": "tweet_A"}})
    result = get_quoted_by(db, a_id)
    src_ids = {r["src_article_id"] for r in result}
    assert b_id in src_ids
    assert c_id in src_ids


def test_get_replied_by(db):
    a_id = _seed_tweet(db, "https://x.com/a/1", "tweet_A")
    b_id = _seed_tweet(db, "https://x.com/b/2", "tweet_B")
    sync_tweet_links(db, b_id, {"in_reply_to_status_id": "tweet_A"})
    result = get_replied_by(db, a_id)
    assert len(result) == 1
    assert result[0]["src_article_id"] == b_id


def test_get_article_tweet_links(db):
    a_id = _seed_tweet(db, "https://x.com/a/1", "tweet_A")
    b_id = _seed_tweet(db, "https://x.com/b/2", "tweet_B")
    c_id = _seed_tweet(db, "https://x.com/c/3", "tweet_C")
    # B quotes A
    sync_tweet_links(db, b_id, {"quoted_tweet": {"tweet_id": "tweet_A"}})
    # C replies to A
    sync_tweet_links(db, c_id, {"in_reply_to_status_id": "tweet_A"})
    links = get_article_tweet_links(db, a_id)
    assert len(links["quoted_by"]) == 1
    assert len(links["replied_by"]) == 1
    assert len(links["outbound"]) == 0


def test_idempotent_sync(db):
    a_id = _seed_tweet(db, "https://x.com/a/1", "tweet_A")
    b_id = _seed_tweet(db, "https://x.com/b/2", "tweet_B")
    sync_tweet_links(db, b_id, {"quoted_tweet": {"tweet_id": "tweet_A"}})
    sync_tweet_links(db, b_id, {"quoted_tweet": {"tweet_id": "tweet_A"}})
    count = db.execute("SELECT COUNT(*) AS n FROM tweet_links WHERE src_article_id=?", (b_id,)).fetchone()["n"]
    assert count == 1


# ── API endpoint tests ────────────────────────────────────────────────────────

def test_api_quoted_by_empty(client):
    r = client.get("/api/articles/9999/quoted_by")
    assert r.status_code == 200
    assert r.json() == []


def test_api_replied_by_empty(client):
    r = client.get("/api/articles/9999/replied_by")
    assert r.status_code == 200
    assert r.json() == []


def test_api_links_empty(client):
    r = client.get("/api/articles/9999/links")
    assert r.status_code == 200
    data = r.json()
    assert "quoted_by" in data
    assert "replied_by" in data
    assert "outbound" in data
