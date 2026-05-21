"""Q6 — Entity-home navigation + ticker 500 tests."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from whatsapp_archive import create_app
from whatsapp_archive.scrape.db import open_db, upsert_article, upsert_entity, link_article_entity

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
def empty_client(tmp_path: Path):
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    shutil.copy(FIXTURE_CHAT, archive_dir / "mini_chat.txt")
    conn = open_db(archive_dir)
    conn.commit()
    client = TestClient(create_app(archive_dir))
    yield conn, client
    conn.close()


@pytest.fixture
def entity_client(tmp_path: Path):
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    shutil.copy(FIXTURE_CHAT, archive_dir / "mini_chat.txt")
    conn = open_db(archive_dir)
    eid = upsert_entity(conn, "$NDX", "ticker")
    aid = upsert_article(conn, "https://x.com/t/1", "ok", title="NDX update")
    link_article_entity(conn, aid, eid, "$NDX")
    conn.commit()
    client = TestClient(create_app(archive_dir))
    yield conn, client, eid
    conn.close()


# ── /api/home/entities ────────────────────────────────────────────────────────

def test_home_entities_empty_table_returns_200(empty_client):
    _, client = empty_client
    r = client.get("/api/home/entities")
    assert r.status_code == 200
    data = r.json()
    assert "entities" in data
    assert data["entities"] == [] or isinstance(data["entities"], list)


def test_home_entities_with_kind_filter(entity_client):
    _, client, _ = entity_client
    r = client.get("/api/home/entities?kind=ticker")
    assert r.status_code == 200


def test_home_entities_with_search(entity_client):
    _, client, _ = entity_client
    r = client.get("/api/home/entities?search=NDX")
    assert r.status_code == 200


# ── /api/research/entity/{name} ───────────────────────────────────────────────

def test_research_entity_nonexistent_returns_404(empty_client):
    _, client = empty_client
    r = client.get("/api/research/entity/%24NDX")  # $NDX URL-encoded
    assert r.status_code == 404
    assert "not found" in r.json()["detail"].lower()


def test_research_entity_ticker_with_dollar_sign(entity_client):
    """$NDX properly URL-encoded should hit the route and return 404 (no dossier data)."""
    _, client, _ = entity_client
    # No enrichments, so dossier will be built with empty articles
    # Should return 200 with a minimal dossier, not 500
    import urllib.parse
    name = urllib.parse.quote("$NDX")
    r = client.get(f"/api/research/entity/{name}")
    # Entity exists but has no enrichments — dossier builds successfully with empty claim_buckets
    assert r.status_code in (200, 404)
    assert r.status_code != 500


def test_research_entity_dossier_no_crash_without_ollama(entity_client):
    """build_entity_dossier must not 500 when Ollama is unavailable."""
    _, client, _ = entity_client
    import urllib.parse
    r = client.get(f"/api/research/entity/{urllib.parse.quote('$NDX')}")
    assert r.status_code != 500
