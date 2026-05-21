"""R6 — Entity-first home page tests."""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from whatsapp_archive import create_app
from whatsapp_archive.scrape.db import open_db, upsert_article, upsert_enrichment

FIXTURE_CHAT = Path(__file__).parent / "fixtures" / "mini_chat.txt"

# ── Helpers ───────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _no_background(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BACKGROUND_SCRAPE", "false")
    monkeypatch.setenv("BACKGROUND_ENRICH", "false")
    monkeypatch.setenv("BACKGROUND_OCR", "false")
    monkeypatch.setenv("BACKGROUND_DIGESTS", "false")
    monkeypatch.setenv("BACKGROUND_ENGAGEMENT_REFRESH", "false")


def _seed_entity(db, name: str, kind: str = "company") -> int:
    normalized = name.strip().lower()
    db.execute(
        "INSERT OR IGNORE INTO entities (name, kind, normalized_name) VALUES (?, ?, ?)",
        (name, kind, normalized),
    )
    db.commit()
    return db.execute(
        "SELECT id FROM entities WHERE normalized_name=? AND canonical_id IS NULL", (normalized,)
    ).fetchone()["id"]


def _seed_articles_for_entity(db, entity_id: int, n: int, sentiment: str, base_date: str, base_url: str) -> list[int]:
    ids = []
    for i in range(n):
        art_id = upsert_article(
            db,
            f"{base_url}/{i}",
            "ok",
            title=f"Article {i} for entity {entity_id}",
            published_at=f"{base_date}T12:00:00",
            tweet_meta=json.dumps({
                "author_handle": f"author{i % 3}",
                "author_name": f"Author {i % 3}",
                "favorite_count": (i + 1) * 5,
            }),
        )
        upsert_enrichment(
            db,
            art_id,
            summary=f"Summary {i}",
            categories=json.dumps(["finance"]),
            entities=json.dumps([]),
            sentiment=sentiment,
            model="test",
            enriched_at=datetime.now(timezone.utc).isoformat(),
        )
        db.execute(
            "INSERT OR IGNORE INTO article_entities (article_id, entity_id, assigned_by) VALUES (?, ?, 'test')",
            (art_id, entity_id),
        )
        ids.append(art_id)
    db.commit()
    return ids


@pytest.fixture
def client(tmp_path: Path):
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    shutil.copy(FIXTURE_CHAT, archive_dir / "mini_chat.txt")
    db = open_db(archive_dir)

    now = datetime.now(timezone.utc)
    recent_date = (now - timedelta(days=10)).strftime("%Y-%m-%d")   # within 30d
    old_date    = (now - timedelta(days=50)).strftime("%Y-%m-%d")   # outside 30d, within 60d
    older_date  = (now - timedelta(days=70)).strftime("%Y-%m-%d")   # outside 60d

    # Vista Gold: 10 articles (8 recent bullish, 2 old)
    e1 = _seed_entity(db, "Vista Gold", "company")
    _seed_articles_for_entity(db, e1, 8, "bullish", recent_date, "https://x.com/visgold")
    _seed_articles_for_entity(db, e1, 2, "neutral", old_date, "https://x.com/visgold/old")

    # Silver Corp: 5 articles (2 recent)
    e2 = _seed_entity(db, "Silver Corp", "company")
    _seed_articles_for_entity(db, e2, 2, "bearish", recent_date, "https://x.com/silvercorp")
    _seed_articles_for_entity(db, e2, 3, "neutral", older_date, "https://x.com/silvercorp/old")

    # $VGZ ticker: 3 articles (all recent)
    e3 = _seed_entity(db, "VGZ", "ticker")
    _seed_articles_for_entity(db, e3, 3, "bullish", recent_date, "https://x.com/vgz")

    # PersonEntity: 1 article (old)
    e4 = _seed_entity(db, "Jane Smith", "person")
    _seed_articles_for_entity(db, e4, 1, "neutral", older_date, "https://x.com/janesmith")

    db.close()
    return TestClient(create_app(archive_dir))


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_endpoint_returns_200(client):
    r = client.get("/api/home/entities")
    assert r.status_code == 200


def test_sort_by_count_default(client):
    r = client.get("/api/home/entities?sort=count")
    data = r.json()
    counts = [e["article_count"] for e in data["entities"]]
    assert counts == sorted(counts, reverse=True)


def test_sort_by_count_orders_vista_gold_first(client):
    r = client.get("/api/home/entities?sort=count")
    names = [e["name"] for e in r.json()["entities"]]
    assert names[0] == "Vista Gold"


def test_kind_filter_company(client):
    r = client.get("/api/home/entities?kind=company")
    kinds = [e["kind"] for e in r.json()["entities"]]
    assert all(k == "company" for k in kinds)
    assert len(kinds) == 2


def test_kind_filter_ticker(client):
    r = client.get("/api/home/entities?kind=ticker")
    data = r.json()
    assert data["total"] == 1
    assert data["entities"][0]["name"] == "VGZ"


def test_search_filters(client):
    r = client.get("/api/home/entities?search=gol")
    names = [e["name"] for e in r.json()["entities"]]
    assert any("Gold" in n for n in names)
    assert not any("Silver" in n for n in names)


def test_search_case_insensitive(client):
    r = client.get("/api/home/entities?search=vista")
    names = [e["name"] for e in r.json()["entities"]]
    assert "Vista Gold" in names


def test_total_count(client):
    r = client.get("/api/home/entities")
    assert r.json()["total"] == 4


def test_entity_has_required_fields(client):
    r = client.get("/api/home/entities")
    for e in r.json()["entities"]:
        assert "canonical_id" in e
        assert "name" in e
        assert "kind" in e
        assert "article_count" in e
        assert "sentiment_majority" in e
        assert "top_authors" in e
        assert "top_cooccurring_entities" in e
        assert "recent_article_id" in e
        assert "has_deepdive" in e


def test_sentiment_majority_bullish(client):
    r = client.get("/api/home/entities?search=vista")
    entity = r.json()["entities"][0]
    assert entity["sentiment_majority"] == "bullish"


def test_sort_trending(client):
    # Vista Gold has 8 recent, 2 in prior → high trend %
    # Silver Corp has 2 recent, 0 in prior-period (3 are older_date, outside 60d window)
    # → all should have non-negative trends, Vista Gold first
    r = client.get("/api/home/entities?sort=trending")
    assert r.status_code == 200
    data = r.json()
    # Vista Gold should be first (8 recent vs 2 prior = +300%)
    names = [e["name"] for e in data["entities"] if e["recent_count"] > 0]
    assert names[0] == "Vista Gold"


def test_pagination(client):
    r = client.get("/api/home/entities?limit=2&offset=0")
    data = r.json()
    assert len(data["entities"]) == 2
    assert data["total"] == 4

    r2 = client.get("/api/home/entities?limit=2&offset=2")
    data2 = r2.json()
    assert len(data2["entities"]) == 2

    all_ids = [e["canonical_id"] for e in data["entities"]] + [e["canonical_id"] for e in data2["entities"]]
    assert len(set(all_ids)) == 4


def test_top_authors_populated(client):
    r = client.get("/api/home/entities?search=vista")
    entity = r.json()["entities"][0]
    assert len(entity["top_authors"]) > 0
    for a in entity["top_authors"]:
        assert "handle" in a
        assert "count" in a
