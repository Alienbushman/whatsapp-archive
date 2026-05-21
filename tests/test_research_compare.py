"""R2 — Cross-entity comparison tests."""
from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from whatsapp_archive import create_app
from whatsapp_archive.research.compare import (
    _detect_divergence,
    _compute_themes,
    _themes_similar,
    compare_entities,
)
from whatsapp_archive.scrape.db import open_db, save_dossier_cache

FIXTURE_CHAT = Path(__file__).parent / "fixtures" / "mini_chat.txt"


@pytest.fixture(autouse=True)
def _no_background(monkeypatch):
    monkeypatch.setenv("BACKGROUND_SCRAPE", "false")
    monkeypatch.setenv("BACKGROUND_ENRICH", "false")
    monkeypatch.setenv("BACKGROUND_OCR", "false")
    monkeypatch.setenv("BACKGROUND_DIGESTS", "false")
    monkeypatch.setenv("BACKGROUND_ENGAGEMENT_REFRESH", "false")


def _make_dossier(name: str, kind: str, arc: list[dict], buckets: list[dict] | None = None) -> dict:
    return {
        "entity": {"name": name, "kind": kind, "aliases": [], "canonical_id": 1},
        "stats": {"article_count": sum(m["article_count"] for m in arc), "first_seen": None, "last_seen": None, "total_engagement": 0},
        "narrative_arc": arc,
        "key_authors": [],
        "related_entities": [],
        "key_articles": {"most_engaged": [], "most_recent": [], "most_quoted": []},
        "claim_buckets": buckets or [],
    }


def _arc_month(month: str, count: int, bullish: int = 0, bearish: int = 0) -> dict:
    neutral = count - bullish - bearish
    return {
        "month": month,
        "article_count": count,
        "sentiment": {"bullish": bullish, "bearish": bearish, "neutral": max(neutral, 0)},
    }


# ── Unit tests for compare logic ──────────────────────────────────────────────

def test_sentiment_flip_detected():
    d_a = _make_dossier("Alpha", "company", [_arc_month("2025-01", 5, bullish=4, bearish=0)])
    d_b = _make_dossier("Beta", "company", [_arc_month("2025-01", 5, bullish=0, bearish=4)])
    points = _detect_divergence([d_a, d_b])
    types = [p["type"] for p in points]
    assert "sentiment_flip" in types


def test_no_sentiment_flip_when_same_direction():
    d_a = _make_dossier("Alpha", "company", [_arc_month("2025-01", 5, bullish=4)])
    d_b = _make_dossier("Beta", "company", [_arc_month("2025-01", 5, bullish=4)])
    points = _detect_divergence([d_a, d_b])
    assert not any(p["type"] == "sentiment_flip" for p in points)


def test_volume_divergence_detected():
    d_a = _make_dossier("Alpha", "company", [_arc_month("2025-01", 50)])
    d_b = _make_dossier("Beta", "company", [_arc_month("2025-01", 5)])
    points = _detect_divergence([d_a, d_b])
    types = [p["type"] for p in points]
    assert "volume_divergence" in types


def test_no_volume_divergence_when_similar():
    d_a = _make_dossier("Alpha", "company", [_arc_month("2025-01", 10)])
    d_b = _make_dossier("Beta", "company", [_arc_month("2025-01", 8)])
    points = _detect_divergence([d_a, d_b])
    assert not any(p["type"] == "volume_divergence" for p in points)


def test_single_entity_returns_no_divergence():
    d = _make_dossier("Solo", "company", [_arc_month("2025-01", 5, bullish=3)])
    assert _detect_divergence([d]) == []


def test_themes_similar_exact():
    assert _themes_similar("Production Milestones", "production milestones")


def test_themes_similar_overlap():
    assert _themes_similar("Gold Production", "Gold Mining Production")


def test_themes_not_similar_unrelated():
    assert not _themes_similar("Dividend Policy", "Environmental Compliance")


def test_shared_theme_detected():
    buckets_a = [{"theme": "Gold production", "article_ids": [1, 2]}]
    buckets_b = [{"theme": "Gold Production Strategy", "article_ids": [3]}]
    d_a = _make_dossier("Alpha", "company", [], buckets_a)
    d_b = _make_dossier("Beta", "company", [], buckets_b)
    shared, unique = _compute_themes([d_a, d_b])
    assert len(shared) >= 1
    assert "Alpha" in shared[0]["entities_present"]
    assert "Beta" in shared[0]["entities_present"]


def test_unique_theme_when_no_overlap():
    buckets_a = [{"theme": "Dividend Policy", "article_ids": [1]}]
    buckets_b = [{"theme": "Environmental Compliance", "article_ids": [2]}]
    d_a = _make_dossier("Alpha", "company", [], buckets_a)
    d_b = _make_dossier("Beta", "company", [], buckets_b)
    shared, unique = _compute_themes([d_a, d_b])
    assert len(shared) == 0
    assert len(unique) == 2


# ── Integration test via FastAPI client ───────────────────────────────────────

@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    shutil.copy(FIXTURE_CHAT, archive_dir / "mini_chat.txt")
    db = open_db(archive_dir)

    # Pre-populate dossier cache directly so we don't need Ollama
    d_a = _make_dossier("Alpha Corp", "company", [
        _arc_month("2025-01", 5, bullish=4, bearish=0),
        _arc_month("2025-02", 8, bullish=6, bearish=1),
    ])
    d_b = _make_dossier("Beta Ltd", "company", [
        _arc_month("2025-01", 5, bullish=0, bearish=4),
        _arc_month("2025-02", 2, bullish=1, bearish=0),
    ])
    save_dossier_cache(db, "Alpha Corp", json.dumps(d_a))
    save_dossier_cache(db, "Beta Ltd", json.dumps(d_b))
    db.close()

    return TestClient(create_app(archive_dir))


def test_compare_endpoint_returns_200(client):
    r = client.get("/api/research/compare?entities=Alpha+Corp&entities=Beta+Ltd")
    assert r.status_code == 200


def test_compare_returns_two_entities(client):
    r = client.get("/api/research/compare?entities=Alpha+Corp&entities=Beta+Ltd")
    data = r.json()
    assert len(data["entities"]) == 2


def test_compare_detects_sentiment_flip(client):
    r = client.get("/api/research/compare?entities=Alpha+Corp&entities=Beta+Ltd")
    data = r.json()
    types = [p["type"] for p in data["divergence_points"]]
    assert "sentiment_flip" in types


def test_compare_requires_two_entities(client):
    r = client.get("/api/research/compare?entities=Alpha+Corp")
    assert r.status_code == 422


def test_compare_not_found_reported(client):
    r = client.get("/api/research/compare?entities=Alpha+Corp&entities=Ghost+Corp")
    data = r.json()
    assert "Ghost Corp" in data["not_found"]
