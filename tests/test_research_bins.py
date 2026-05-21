"""Q11 — Research bins tests."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from whatsapp_archive import create_app
from whatsapp_archive.scrape.db import (
    open_db,
    create_research_bin,
    get_research_bins,
    get_research_bin,
    add_research_bin_item,
    delete_research_bin_item,
    delete_research_bin,
    promote_research_bin_to_collection,
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

def test_create_research_bin(db):
    row = create_research_bin(db, "Gold thesis", hypothesis="Is gold undervalued?", seed_query="gold")
    assert row["name"] == "Gold thesis"
    assert row["seed_query"] == "gold"
    assert row["expires_at"] > row["created_at"]


def test_get_research_bins_excludes_expired_by_default(db):
    b = create_research_bin(db, "Active", expiry_days=7)
    # Manually expire it
    db.execute("UPDATE research_bins SET expires_at='2020-01-01' WHERE id=?", (b["id"],))
    db.commit()
    rows = get_research_bins(db, include_expired=False)
    assert not any(r["id"] == b["id"] for r in rows)


def test_get_research_bins_include_expired(db):
    b = create_research_bin(db, "Old", expiry_days=7)
    db.execute("UPDATE research_bins SET expires_at='2020-01-01' WHERE id=?", (b["id"],))
    db.commit()
    rows = get_research_bins(db, include_expired=True)
    assert any(r["id"] == b["id"] for r in rows)


def test_add_and_delete_item(db):
    b = create_research_bin(db, "Test")
    item = add_research_bin_item(db, b["id"], "article", "42")
    assert item["target_id"] == "42"
    assert delete_research_bin_item(db, item["id"]) is True


def test_delete_nonexistent_item_returns_false(db):
    assert delete_research_bin_item(db, 9999) is False


def test_delete_research_bin(db):
    b = create_research_bin(db, "Temp")
    assert delete_research_bin(db, b["id"]) is True
    assert get_research_bin(db, b["id"]) is None


# ── API layer ─────────────────────────────────────────────────────────────────

def test_api_create_bin(client):
    resp = client.post("/api/research_bins", json={
        "name": "Gold supercycle", "hypothesis": "Uptrend continues",
        "seed_query": "gold", "expiry_days": 7,
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "Gold supercycle"
    assert "id" in data


def test_api_list_bins(client):
    client.post("/api/research_bins", json={"name": "Bin A"})
    client.post("/api/research_bins", json={"name": "Bin B"})
    resp = client.get("/api/research_bins")
    assert resp.status_code == 200
    assert len(resp.json()) >= 2


def test_api_get_bin_with_items(client):
    c = client.post("/api/research_bins", json={"name": "My bin", "seed_query": "hello"}).json()
    bin_id = c["id"]
    client.post(f"/api/research_bins/{bin_id}/items", json={"target_kind": "article", "target_id": "1"})
    resp = client.get(f"/api/research_bins/{bin_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data
    assert len(data["items"]) == 1
    assert "live_hits" in data


def test_api_delete_bin(client):
    c = client.post("/api/research_bins", json={"name": "To delete"}).json()
    bin_id = c["id"]
    resp = client.delete(f"/api/research_bins/{bin_id}")
    assert resp.status_code == 200
    assert resp.json()["deleted"] is True
    assert client.get(f"/api/research_bins/{bin_id}").status_code == 404


def test_api_promote_bin(client):
    c = client.post("/api/research_bins", json={"name": "To promote"}).json()
    resp = client.post(f"/api/research_bins/{c['id']}/promote")
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "To promote"


def test_api_patch_bin(client):
    c = client.post("/api/research_bins", json={"name": "Old name"}).json()
    resp = client.patch(f"/api/research_bins/{c['id']}", json={"name": "New name", "hypothesis": "New hyp"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "New name"
