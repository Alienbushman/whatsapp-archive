"""Tests for /api/export/article/{id} and /api/export/research_bin/{id}."""
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
    create_research_bin,
    add_research_bin_item,
)

FIXTURE_CHAT = Path(__file__).parent / "fixtures" / "mini_chat.txt"


@pytest.fixture
def setup(tmp_path):
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    shutil.copy(FIXTURE_CHAT, archive_dir / "mini_chat.txt")
    conn = open_db(archive_dir)

    aid = upsert_article(
        conn, "https://x.com/test/1", "ok",
        title="Test Article",
        tweet_meta=json.dumps({"author_handle": "tester", "text": "test tweet"}),
        published_at="2025-01-01T10:00:00",
    )
    conn.commit()

    bin_row = create_research_bin(conn, name="My Bin")
    bin_id = bin_row["id"]
    add_research_bin_item(conn, bin_id, "article", str(aid))
    conn.commit()

    app = create_app(archive_dir)
    client = TestClient(app)
    yield client, aid, bin_id
    conn.close()


# ── Article export ─────────────────────────────────────────────────────────────

def test_export_article_200(setup):
    client, aid, _ = setup
    r = client.get(f"/api/export/article/{aid}?format=md")
    assert r.status_code == 200


def test_export_article_content_disposition(setup):
    client, aid, _ = setup
    r = client.get(f"/api/export/article/{aid}?format=md")
    assert "attachment" in r.headers.get("content-disposition", "")


def test_export_article_contains_title(setup):
    client, aid, _ = setup
    r = client.get(f"/api/export/article/{aid}?format=md")
    assert "Test Article" in r.text


def test_export_article_json_format(setup):
    client, aid, _ = setup
    r = client.get(f"/api/export/article/{aid}?format=json")
    assert r.status_code == 200
    data = r.json()
    assert "articles" in data
    assert len(data["articles"]) == 1
    assert data["articles"][0]["title"] == "Test Article"


def test_export_article_404(setup):
    client, _, _ = setup
    r = client.get("/api/export/article/999999?format=md")
    assert r.status_code == 404


# ── Research bin export ────────────────────────────────────────────────────────

def test_export_research_bin_200(setup):
    client, _, bin_id = setup
    r = client.get(f"/api/export/research_bin/{bin_id}?format=md")
    assert r.status_code == 200


def test_export_research_bin_content_disposition(setup):
    client, _, bin_id = setup
    r = client.get(f"/api/export/research_bin/{bin_id}?format=md")
    assert "attachment" in r.headers.get("content-disposition", "")


def test_export_research_bin_contains_article(setup):
    client, _, bin_id = setup
    r = client.get(f"/api/export/research_bin/{bin_id}?format=md")
    assert "Test Article" in r.text


def test_export_research_bin_json(setup):
    client, _, bin_id = setup
    r = client.get(f"/api/export/research_bin/{bin_id}?format=json")
    assert r.status_code == 200
    data = r.json()
    assert "articles" in data
    assert any(a["title"] == "Test Article" for a in data["articles"])


def test_export_research_bin_404(setup):
    client, _, _ = setup
    r = client.get("/api/export/research_bin/999999?format=md")
    assert r.status_code == 404


def test_export_research_bin_empty_items(tmp_path, monkeypatch):
    """Bin with no article items exports empty article list without error."""
    for env in ["BACKGROUND_SCRAPE", "BACKGROUND_ENRICH", "BACKGROUND_OCR",
                "BACKGROUND_DIGESTS", "BACKGROUND_ENGAGEMENT_REFRESH",
                "BACKGROUND_CLUSTERING", "BACKGROUND_PROFILES"]:
        monkeypatch.setenv(env, "false")

    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    shutil.copy(FIXTURE_CHAT, archive_dir / "mini_chat.txt")
    conn = open_db(archive_dir)
    bin_row = create_research_bin(conn, name="Empty Bin")
    bin_id = bin_row["id"]
    conn.commit()

    client = TestClient(create_app(archive_dir))
    r = client.get(f"/api/export/research_bin/{bin_id}?format=json")
    assert r.status_code == 200
    data = r.json()
    assert data["articles"] == []
    conn.close()
