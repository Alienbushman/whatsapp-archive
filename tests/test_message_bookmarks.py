"""Q8 — Bookmark plain message text tests."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from whatsapp_archive import create_app
from whatsapp_archive.scrape.db import (
    open_db,
    upsert_message_bookmark,
    delete_message_bookmark,
    get_message_bookmark,
    get_message_bookmarks,
)

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
    app = create_app(archive_dir)
    return TestClient(app)


# ── DB layer ──────────────────────────────────────────────────────────────────

def test_upsert_message_bookmark_creates_row(db):
    row = upsert_message_bookmark(
        db, "chat1|2025-01-05T09:01:00|Alice",
        "chat1", "Alice", "2025-01-05T09:01:00", "Hello everyone"
    )
    assert row["msg_key"] == "chat1|2025-01-05T09:01:00|Alice"
    assert row["body"] == "Hello everyone"
    assert row["sender"] == "Alice"


def test_upsert_message_bookmark_updates_note(db):
    key = "chat1|2025-01-05T09:01:00|Alice"
    upsert_message_bookmark(db, key, "chat1", "Alice", "2025-01-05T09:01:00", "Hello everyone")
    upsert_message_bookmark(db, key, "chat1", "Alice", "2025-01-05T09:01:00", "Hello everyone", note="great insight")
    row = get_message_bookmark(db, key)
    assert row["note"] == "great insight"


def test_delete_message_bookmark(db):
    key = "chat1|2025-01-05T09:01:00|Bob"
    upsert_message_bookmark(db, key, "chat1", "Bob", "2025-01-05T09:01:00", "Hi there")
    assert delete_message_bookmark(db, key) is True
    assert get_message_bookmark(db, key) is None


def test_delete_nonexistent_returns_false(db):
    assert delete_message_bookmark(db, "nonexistent|key") is False


def test_get_message_bookmarks_list(db):
    for i in range(3):
        upsert_message_bookmark(
            db, f"chat1|2025-01-0{i+1}T09:00:00|Alice",
            "chat1", "Alice", f"2025-01-0{i+1}T09:00:00", f"Message {i}"
        )
    rows = get_message_bookmarks(db, limit=10)
    assert len(rows) == 3


# ── API layer ─────────────────────────────────────────────────────────────────

def test_api_post_message_bookmark(client):
    resp = client.post("/api/message-bookmarks", json={
        "chat_id": "chat1", "sender": "Alice",
        "ts": "2025-01-05T09:01:00", "body": "Hello everyone",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["sender"] == "Alice"
    assert data["body"] == "Hello everyone"
    assert "msg_key" in data


def test_api_delete_message_bookmark(client):
    # Create
    resp = client.post("/api/message-bookmarks", json={
        "chat_id": "chat1", "sender": "Bob",
        "ts": "2025-01-05T09:02:00", "body": "Hi there",
    })
    msg_key = resp.json()["msg_key"]

    # Delete
    del_resp = client.delete(f"/api/message-bookmarks/{msg_key}")
    assert del_resp.status_code == 200
    assert del_resp.json()["deleted"] is True

    # Confirm gone
    get_resp = client.get(f"/api/message-bookmarks/{msg_key}")
    assert get_resp.status_code == 404


def test_api_list_message_bookmarks(client):
    for i in range(2):
        client.post("/api/message-bookmarks", json={
            "chat_id": "chat1", "sender": "Alice",
            "ts": f"2025-01-0{i+1}T09:00:00", "body": f"Msg {i}",
        })
    resp = client.get("/api/message-bookmarks")
    assert resp.status_code == 200
    assert len(resp.json()) >= 2


def test_api_existing_article_bookmarks_unaffected(client):
    """Adding message bookmarks does not break /api/bookmarks."""
    client.post("/api/message-bookmarks", json={
        "chat_id": "c1", "sender": "Alice",
        "ts": "2025-01-05T09:01:00", "body": "Plain text",
    })
    resp = client.get("/api/bookmarks")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)
