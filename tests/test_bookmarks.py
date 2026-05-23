"""I11 — Bookmarks, notes, and personal collections."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from whatsapp_archive import create_app
from whatsapp_archive.scrape.db import (
    add_collection_item,
    create_collection,
    delete_bookmark,
    get_bookmark,
    get_bookmarks,
    get_collection_items,
    get_collections,
    open_db,
    remove_collection_item,
    upsert_article,
    upsert_bookmark,
)

FIXTURE_CHAT = Path(__file__).parent / "fixtures" / "mini_chat.txt"

_TWEET_META = json.dumps({
    "author_handle": "alice",
    "text": "Gold makes new ATH",
    "hashtags": ["Gold"],
    "mentioned_handles": [],
    "tickers": ["GC"],
})


@pytest.fixture(autouse=True)
def _no_background(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BACKGROUND_SCRAPE", "false")
    monkeypatch.setenv("BACKGROUND_ENRICH", "false")
    monkeypatch.setenv("BACKGROUND_OCR", "false")


@pytest.fixture
def db(tmp_path: Path):
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    shutil.copy(FIXTURE_CHAT, archive_dir / "mini_chat.txt")
    return open_db(archive_dir)


@pytest.fixture
def seeded_db(db):
    aid1 = upsert_article(db, "https://x.com/alice/status/1", "ok", title="T1", tweet_meta=_TWEET_META, fetched_at="2024-01-10T10:00:00")
    aid2 = upsert_article(db, "https://x.com/alice/status/2", "ok", title="T2", tweet_meta=_TWEET_META, fetched_at="2024-01-11T10:00:00")
    return db, aid1, aid2


@pytest.fixture
def client(tmp_path: Path):
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    shutil.copy(FIXTURE_CHAT, archive_dir / "mini_chat.txt")
    db = open_db(archive_dir)
    aid1 = upsert_article(db, "https://x.com/alice/status/10", "ok", title="T1", tweet_meta=_TWEET_META, fetched_at="2024-01-10T10:00:00")
    aid2 = upsert_article(db, "https://x.com/alice/status/20", "ok", title="T2", tweet_meta=_TWEET_META, fetched_at="2024-01-11T10:00:00")
    db.commit()
    db.close()
    app = create_app(archive_dir)
    return TestClient(app), aid1, aid2


# ── DB helpers — bookmarks ────────────────────────────────────────────────────

def test_upsert_and_get_bookmark(seeded_db):
    db, aid1, _ = seeded_db
    row = upsert_bookmark(db, aid1)
    assert row["article_id"] == aid1
    assert row["starred"] == 1
    assert row["note"] is None

    fetched = get_bookmark(db, aid1)
    assert fetched["article_id"] == aid1


def test_bookmark_note_roundtrip(seeded_db):
    db, aid1, _ = seeded_db
    upsert_bookmark(db, aid1, note="Very interesting")
    row = get_bookmark(db, aid1)
    assert row["note"] == "Very interesting"


def test_upsert_bookmark_updates_note(seeded_db):
    db, aid1, _ = seeded_db
    upsert_bookmark(db, aid1, note="first")
    upsert_bookmark(db, aid1, note="second")
    assert get_bookmark(db, aid1)["note"] == "second"


def test_delete_bookmark(seeded_db):
    db, aid1, _ = seeded_db
    upsert_bookmark(db, aid1)
    assert delete_bookmark(db, aid1) is True
    assert get_bookmark(db, aid1) is None


def test_delete_nonexistent_bookmark(seeded_db):
    db, aid1, _ = seeded_db
    assert delete_bookmark(db, aid1) is False


def test_get_bookmarks_list(seeded_db):
    db, aid1, aid2 = seeded_db
    upsert_bookmark(db, aid1)
    upsert_bookmark(db, aid2)
    rows = get_bookmarks(db)
    ids = [r["article_id"] for r in rows]
    assert aid1 in ids and aid2 in ids


def test_get_bookmarks_has_note_filter(seeded_db):
    db, aid1, aid2 = seeded_db
    upsert_bookmark(db, aid1, note="my note")
    upsert_bookmark(db, aid2)
    rows = get_bookmarks(db, has_note=True)
    ids = [r["article_id"] for r in rows]
    assert aid1 in ids
    assert aid2 not in ids


# ── DB helpers — collections ──────────────────────────────────────────────────

def test_create_and_get_collection(seeded_db):
    db, _, _ = seeded_db
    col = create_collection(db, "Gold thesis")
    assert col["name"] == "Gold thesis"
    assert col["id"] is not None


def test_get_collections_with_item_count(seeded_db):
    db, aid1, _ = seeded_db
    col = create_collection(db, "My picks")
    add_collection_item(db, col["id"], aid1)
    cols = get_collections(db)
    found = next(c for c in cols if c["id"] == col["id"])
    assert found["item_count"] == 1


def test_add_remove_collection_item(seeded_db):
    db, aid1, aid2 = seeded_db
    col = create_collection(db, "Test col")
    add_collection_item(db, col["id"], aid1)
    add_collection_item(db, col["id"], aid2)
    items = get_collection_items(db, col["id"])
    ids = [r["id"] for r in items]
    assert aid1 in ids and aid2 in ids

    remove_collection_item(db, col["id"], aid1)
    items2 = get_collection_items(db, col["id"])
    ids2 = [r["id"] for r in items2]
    assert aid1 not in ids2
    assert aid2 in ids2


def test_add_collection_item_idempotent(seeded_db):
    db, aid1, _ = seeded_db
    col = create_collection(db, "Idempotent")
    add_collection_item(db, col["id"], aid1)
    add_collection_item(db, col["id"], aid1)  # should not raise
    assert len(get_collection_items(db, col["id"])) == 1


def test_collection_name_unique(seeded_db):
    db, _, _ = seeded_db
    create_collection(db, "Unique")
    with pytest.raises(Exception):
        create_collection(db, "Unique")


# ── API — bookmarks ───────────────────────────────────────────────────────────

def test_api_bookmark_create(client):
    c, aid1, _ = client
    r = c.post(f"/api/bookmarks/{aid1}", json={})
    assert r.status_code == 200
    data = r.json()
    assert data["article_id"] == aid1
    assert data["starred"] == 1


def test_api_bookmark_with_note(client):
    c, aid1, _ = client
    r = c.post(f"/api/bookmarks/{aid1}", json={"note": "Great find"})
    assert r.status_code == 200
    assert r.json()["note"] == "Great find"


def test_api_bookmark_delete(client):
    c, aid1, _ = client
    c.post(f"/api/bookmarks/{aid1}", json={})
    r = c.delete(f"/api/bookmarks/{aid1}")
    assert r.status_code == 200
    assert r.json()["deleted"] is True


def test_api_bookmark_delete_nonexistent(client):
    c, _, _ = client
    r = c.delete("/api/bookmarks/99999")
    assert r.status_code == 200
    assert r.json()["deleted"] is False


def test_api_bookmarks_list(client):
    c, aid1, aid2 = client
    c.post(f"/api/bookmarks/{aid1}", json={})
    c.post(f"/api/bookmarks/{aid2}", json={})
    r = c.get("/api/bookmarks")
    assert r.status_code == 200
    ids = [b["article_id"] for b in r.json()]
    assert aid1 in ids and aid2 in ids


def test_api_bookmarks_persist_after_create(client):
    c, aid1, _ = client
    c.post(f"/api/bookmarks/{aid1}", json={"note": "Persisted"})
    r = c.get("/api/bookmarks")
    bm = next(b for b in r.json() if b["article_id"] == aid1)
    assert bm["note"] == "Persisted"


# ── API — collections ─────────────────────────────────────────────────────────

def test_api_collection_create(client):
    c, _, _ = client
    r = c.post("/api/collections", json={"name": "Gold thesis"})
    assert r.status_code == 200
    assert r.json()["name"] == "Gold thesis"


def test_api_collections_list(client):
    c, _, _ = client
    c.post("/api/collections", json={"name": "Col A"})
    c.post("/api/collections", json={"name": "Col B"})
    r = c.get("/api/collections")
    assert r.status_code == 200
    names = [col["name"] for col in r.json()]
    assert "Col A" in names and "Col B" in names


def test_api_collection_duplicate_name(client):
    c, _, _ = client
    c.post("/api/collections", json={"name": "Dupe"})
    r = c.post("/api/collections", json={"name": "Dupe"})
    assert r.status_code == 409


def test_api_collection_add_item(client):
    c, aid1, _ = client
    col_id = c.post("/api/collections", json={"name": "MyCol"}).json()["id"]
    r = c.post(f"/api/collections/{col_id}/items", json={"article_id": aid1})
    assert r.status_code == 200


def test_api_collection_items_list(client):
    c, aid1, aid2 = client
    col_id = c.post("/api/collections", json={"name": "Items"}).json()["id"]
    c.post(f"/api/collections/{col_id}/items", json={"article_id": aid1})
    c.post(f"/api/collections/{col_id}/items", json={"article_id": aid2})
    r = c.get(f"/api/collections/{col_id}/items")
    assert r.status_code == 200
    ids = [item["id"] for item in r.json()]
    assert aid1 in ids and aid2 in ids


def test_api_collection_remove_item(client):
    c, aid1, _ = client
    col_id = c.post("/api/collections", json={"name": "RemoveTest"}).json()["id"]
    c.post(f"/api/collections/{col_id}/items", json={"article_id": aid1})
    r = c.delete(f"/api/collections/{col_id}/items/{aid1}")
    assert r.status_code == 200
    assert r.json()["removed"] is True
    items = c.get(f"/api/collections/{col_id}/items").json()
    assert not any(i["id"] == aid1 for i in items)


def test_api_collection_remove_nonexistent_item_404(client):
    """Removing an item not in the collection should be 404, not silent 200."""
    c, aid1, _ = client
    col_id = c.post("/api/collections", json={"name": "404Test"}).json()["id"]
    # Item was never added — delete should 404.
    r = c.delete(f"/api/collections/{col_id}/items/{aid1}")
    assert r.status_code == 404


def test_api_collection_remove_item_in_nonexistent_collection_404(client):
    c, aid1, _ = client
    r = c.delete(f"/api/collections/99999/items/{aid1}")
    assert r.status_code == 404


def test_api_collection_delete(client):
    """DELETE /api/collections/{id} removes the collection and its items."""
    c, aid1, _ = client
    col_id = c.post("/api/collections", json={"name": "DeleteMe"}).json()["id"]
    c.post(f"/api/collections/{col_id}/items", json={"article_id": aid1})
    r = c.delete(f"/api/collections/{col_id}")
    assert r.status_code == 200
    assert r.json() == {"deleted": True, "id": col_id}
    # Subsequent GET should 404.
    assert c.get(f"/api/collections/{col_id}").status_code == 404
    # And it's gone from the list.
    listed = c.get("/api/collections").json()
    assert not any(col["id"] == col_id for col in listed)


def test_api_collection_delete_nonexistent_404(client):
    c, _, _ = client
    r = c.delete("/api/collections/99999")
    assert r.status_code == 404


def test_api_collection_404(client):
    c, _, _ = client
    r = c.get("/api/collections/99999")
    assert r.status_code == 404


def test_api_collection_export_md(client):
    c, aid1, _ = client
    col_id = c.post("/api/collections", json={"name": "Export test"}).json()["id"]
    c.post(f"/api/collections/{col_id}/items", json={"article_id": aid1})
    r = c.get(f"/api/collections/{col_id}/export?format=md")
    assert r.status_code == 200
    assert "Export test" in r.text


def test_api_collection_export_empty(client):
    c, _, _ = client
    col_id = c.post("/api/collections", json={"name": "Empty"}).json()["id"]
    r = c.get(f"/api/collections/{col_id}/export?format=json")
    assert r.status_code == 200
