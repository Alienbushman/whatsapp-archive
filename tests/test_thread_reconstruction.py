"""I5 — Tweet thread + quote reconstruction via /api/articles/{id}/thread."""

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


def _mk_tweet(tweet_id: str, handle: str, text: str, reply_to: str | None = None) -> str:
    meta = {
        "tweet_id": tweet_id,
        "author_handle": handle,
        "author_name": handle.capitalize(),
        "text": text,
        "hashtags": [],
        "mentioned_handles": [],
        "tickers": [],
    }
    if reply_to:
        meta["in_reply_to_status_id"] = reply_to
    return json.dumps(meta)


@pytest.fixture
def thread_app(tmp_path: Path):
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    shutil.copy(FIXTURE_CHAT, archive_dir / "mini_chat.txt")
    db = open_db(archive_dir)

    # Thread: A → B (reply) → C (reply)
    # D quotes B (independent)
    id_a = upsert_article(db, "https://x.com/alice/status/1000", "ok",
                          title="A", raw_text="Hello world",
                          tweet_meta=_mk_tweet("1000", "alice", "Hello world"),
                          fetched_at="2024-01-01T10:00:00")
    id_b = upsert_article(db, "https://x.com/bob/status/2000", "ok",
                          title="B", raw_text="@alice reply",
                          tweet_meta=_mk_tweet("2000", "bob", "@alice reply", reply_to="1000"),
                          fetched_at="2024-01-01T10:01:00")
    id_c = upsert_article(db, "https://x.com/carol/status/3000", "ok",
                          title="C", raw_text="@bob reply",
                          tweet_meta=_mk_tweet("3000", "carol", "@bob reply", reply_to="2000"),
                          fetched_at="2024-01-01T10:02:00")
    id_d = upsert_article(db, "https://x.com/dan/status/4000", "ok",
                          title="D", raw_text="Quoting B",
                          tweet_meta=_mk_tweet("4000", "dan", "Quoting B"),
                          fetched_at="2024-01-01T10:03:00")

    app = create_app(archive_dir)
    return TestClient(app), id_a, id_b, id_c, id_d


# ── Thread from self (B) ──────────────────────────────────────────────────────

def test_thread_from_b_returns_parent_self_reply(thread_app):
    client, id_a, id_b, id_c, id_d = thread_app
    r = client.get(f"/api/articles/{id_b}/thread")
    assert r.status_code == 200
    items = r.json()
    positions = [i["position"] for i in items]
    # A is parent, B is self, C is reply
    assert "parent" in positions
    assert "self" in positions
    assert "reply" in positions


def test_thread_b_self_is_b(thread_app):
    client, id_a, id_b, id_c, id_d = thread_app
    items = client.get(f"/api/articles/{id_b}/thread").json()
    self_item = next(i for i in items if i["position"] == "self")
    assert self_item["id"] == id_b


def test_thread_b_parent_is_a(thread_app):
    client, id_a, id_b, id_c, id_d = thread_app
    items = client.get(f"/api/articles/{id_b}/thread").json()
    parent_item = next(i for i in items if i["position"] == "parent")
    assert parent_item["id"] == id_a


def test_thread_b_reply_is_c(thread_app):
    client, id_a, id_b, id_c, id_d = thread_app
    items = client.get(f"/api/articles/{id_b}/thread").json()
    reply_items = [i for i in items if i["position"] == "reply"]
    assert any(i["id"] == id_c for i in reply_items)


def test_thread_order_ancestor_self_reply(thread_app):
    client, id_a, id_b, id_c, id_d = thread_app
    items = client.get(f"/api/articles/{id_b}/thread").json()
    positions = [i["position"] for i in items]
    self_idx = positions.index("self")
    # All parents/ancestors come before self
    for i, p in enumerate(positions):
        if p in ("parent", "ancestor"):
            assert i < self_idx
    # All replies come after self
    for i, p in enumerate(positions):
        if p == "reply":
            assert i > self_idx


# ── Thread from root (A) ──────────────────────────────────────────────────────

def test_thread_from_a_returns_self_and_replies(thread_app):
    client, id_a, id_b, id_c, id_d = thread_app
    items = client.get(f"/api/articles/{id_a}/thread").json()
    positions = [i["position"] for i in items]
    assert "self" in positions
    assert "reply" in positions
    assert "parent" not in positions


# ── Missing parent ────────────────────────────────────────────────────────────

def test_thread_missing_parent_shows_placeholder(tmp_path: Path):
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    shutil.copy(FIXTURE_CHAT, archive_dir / "mini_chat.txt")
    db = open_db(archive_dir)

    # Only B and C — A (tweet_id=1000) is missing
    id_b = upsert_article(db, "https://x.com/bob/status/2000", "ok",
                          title="B", raw_text="reply",
                          tweet_meta=_mk_tweet("2000", "bob", "reply", reply_to="1000"),
                          fetched_at="2024-01-01T10:01:00")
    upsert_article(db, "https://x.com/carol/status/3000", "ok",
                   title="C", raw_text="reply to B",
                   tweet_meta=_mk_tweet("3000", "carol", "reply", reply_to="2000"),
                   fetched_at="2024-01-01T10:02:00")

    app = create_app(archive_dir)
    client = TestClient(app)
    items = client.get(f"/api/articles/{id_b}/thread").json()
    missing = [i for i in items if i.get("missing")]
    assert len(missing) == 1
    assert missing[0]["tweet_id"] == "1000"


# ── Self-reference cycle guard ────────────────────────────────────────────────

def test_thread_self_reference_no_loop(tmp_path: Path):
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    shutil.copy(FIXTURE_CHAT, archive_dir / "mini_chat.txt")
    db = open_db(archive_dir)

    # Tweet that replies to itself
    meta = json.dumps({
        "tweet_id": "9999",
        "author_handle": "alice",
        "author_name": "Alice",
        "text": "self-ref",
        "in_reply_to_status_id": "9999",
        "hashtags": [], "mentioned_handles": [], "tickers": [],
    })
    aid = upsert_article(db, "https://x.com/alice/status/9999", "ok",
                         title="self", raw_text="self-ref",
                         tweet_meta=meta, fetched_at="2024-01-01T00:00:00")

    app = create_app(archive_dir)
    client = TestClient(app)
    r = client.get(f"/api/articles/{aid}/thread")
    assert r.status_code == 200
    items = r.json()
    positions = [i["position"] for i in items]
    assert positions.count("self") == 1


# ── 404 cases ─────────────────────────────────────────────────────────────────

def test_thread_404_for_unknown_article(thread_app):
    client, *_ = thread_app
    r = client.get("/api/articles/99999/thread")
    assert r.status_code == 404


def test_thread_d_returns_only_self(thread_app):
    client, id_a, id_b, id_c, id_d = thread_app
    items = client.get(f"/api/articles/{id_d}/thread").json()
    positions = [i["position"] for i in items]
    assert positions == ["self"]
