"""Q10 — Exports hub endpoint tests."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from whatsapp_archive import create_app

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
def client(tmp_path: Path):
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    shutil.copy(FIXTURE_CHAT, archive_dir / "mini_chat.txt")
    app = create_app(archive_dir)
    return TestClient(app)


def test_preview_keyword(client):
    resp = client.post("/api/export/preview", json={
        "scope": "keyword", "q": "Hello", "template": "raw_dump",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "markdown" in data
    assert "item_count" in data
    assert "suggested_filename" in data
    assert data["template_used"] == "raw_dump"


def test_preview_empty_keyword(client):
    resp = client.post("/api/export/preview", json={
        "scope": "keyword", "q": "", "template": "raw_dump",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "markdown" in data


def test_preview_timerange(client):
    resp = client.post("/api/export/preview", json={
        "scope": "timerange", "q": "", "template": "raw_dump",
        "from_date": "2025-01-01", "to_date": "2025-12-31",
    })
    assert resp.status_code == 200
    assert "markdown" in resp.json()


def test_preview_briefing_template(client):
    resp = client.post("/api/export/preview", json={
        "scope": "keyword", "q": "Hello", "template": "briefing",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["template_used"] == "briefing"
    assert "research analyst" in data["markdown"].lower() or "briefing" in data["markdown"].lower()


def test_preview_entity_not_found(client):
    resp = client.post("/api/export/preview", json={
        "scope": "entity", "q": "nonexistent_entity_xyz",
    })
    assert resp.status_code == 404


def test_preview_author_empty_q_returns_empty(client):
    resp = client.post("/api/export/preview", json={"scope": "author", "q": ""})
    assert resp.status_code == 200
    assert resp.json()["item_count"] == 0


def test_entity_export_not_found(client):
    resp = client.get("/api/export/entity/totally_unknown_entity_xyz")
    assert resp.status_code == 404


def test_existing_keyword_export_unaffected(client):
    resp = client.get("/api/export/keyword?q=Hello&format=md")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/markdown")
