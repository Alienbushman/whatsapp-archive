"""R1 — Entity research workspace tests."""

from __future__ import annotations

import json
import math
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest
from fastapi.testclient import TestClient

from whatsapp_archive import create_app
from whatsapp_archive.scrape.db import open_db, upsert_article, upsert_enrichment

FIXTURE_CHAT = Path(__file__).parent / "fixtures" / "mini_chat.txt"

# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _no_background(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BACKGROUND_SCRAPE", "false")
    monkeypatch.setenv("BACKGROUND_ENRICH", "false")
    monkeypatch.setenv("BACKGROUND_OCR", "false")
    monkeypatch.setenv("BACKGROUND_DIGESTS", "false")
    monkeypatch.setenv("BACKGROUND_ENGAGEMENT_REFRESH", "false")


def _make_client(tmp_path: Path, articles: list[dict]) -> tuple[TestClient, object]:
    """Create a test client with given articles seeded in the DB."""
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    shutil.copy(FIXTURE_CHAT, archive_dir / "mini_chat.txt")
    db = open_db(archive_dir)

    for a in articles:
        art_id = upsert_article(
            db,
            a["url"],
            "ok",
            title=a.get("title", ""),
            published_at=a.get("published_at"),
            tweet_meta=json.dumps(a.get("tweet_meta", {})),
        )
        if "summary" in a:
            upsert_enrichment(
                db,
                art_id,
                summary=a["summary"],
                categories=json.dumps(["finance"]),
                entities=json.dumps([{"name": "Vista Gold", "kind": "company", "mention_text": "Vista Gold"}]),
                sentiment=a.get("sentiment", "neutral"),
                model="test",
                enriched_at=datetime.now(timezone.utc).isoformat(),
            )
        # Link article to entity
        entity_id = db.execute(
            "SELECT id FROM entities WHERE normalized_name='vista gold' AND canonical_id IS NULL"
        ).fetchone()
        if entity_id:
            db.execute(
                "INSERT OR IGNORE INTO article_entities (article_id, entity_id, assigned_by) VALUES (?, ?, 'test')",
                (art_id, entity_id["id"]),
            )
    db.commit()
    db.close()

    app = create_app(archive_dir)
    return TestClient(app), archive_dir


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


def _build_cluster_vectors(n_per_cluster: int, k: int, dim: int = 8) -> list[list[float]]:
    """Create clearly separated cluster vectors: each cluster centred at basis vector i."""
    vectors = []
    rng = np.random.default_rng(42)
    for cluster_idx in range(k):
        centre = np.zeros(dim)
        centre[cluster_idx % dim] = 10.0
        for _ in range(n_per_cluster):
            vec = centre + rng.normal(0, 0.01, dim)
            vectors.append(vec.tolist())
    return vectors


@pytest.fixture
def research_client(tmp_path: Path):
    """30 articles in 3 clusters about Vista Gold, entity seeded in DB."""
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    shutil.copy(FIXTURE_CHAT, archive_dir / "mini_chat.txt")
    db = open_db(archive_dir)

    entity_id = _seed_entity(db, "Vista Gold", "company")

    themes = [
        ("Production milestone: new gold reserve discovered", "bullish"),
        ("Capital raise: Vista Gold completes equity offering", "bearish"),
        ("Management change: CEO transition at Vista Gold", "neutral"),
    ]
    articles = []
    # 9 articles (3 per theme) — sqrt(9)=3 so k=3 clusters exactly
    for i in range(9):
        theme_idx = i % 3
        summary, sentiment = themes[theme_idx]
        url = f"https://x.com/vistatest/{i}"
        art_id = upsert_article(
            db,
            url,
            "ok",
            title=f"Vista Gold article {i}",
            published_at=f"2025-0{(i % 3) + 1}-{(i % 28) + 1:02d}T12:00:00",
            tweet_meta=json.dumps({
                "author_handle": f"analyst{i % 3}",
                "author_name": f"Analyst {i % 3}",
                "favorite_count": (i + 1) * 10,
                "retweet_count": i,
            }),
        )
        upsert_enrichment(
            db,
            art_id,
            summary=f"{summary} — article {i}",
            categories=json.dumps(["finance"]),
            entities=json.dumps([{"name": "Vista Gold", "kind": "company", "mention_text": "Vista Gold"}]),
            sentiment=sentiment,
            model="test",
            enriched_at=datetime.now(timezone.utc).isoformat(),
        )
        db.execute(
            "INSERT OR IGNORE INTO article_entities (article_id, entity_id, assigned_by) VALUES (?, ?, 'test')",
            (art_id, entity_id),
        )
        articles.append(art_id)
    db.commit()
    db.close()

    app = create_app(archive_dir)
    return TestClient(app), archive_dir


def _fake_embed_factory(article_ids: list[int]):
    """Returns embed fn that gives each article a deterministic cluster vector.

    Articles are assigned to clusters by their position in the embedding call order:
    idx 0,3,6 → cluster 0 ; idx 1,4,7 → cluster 1 ; idx 2,5,8 → cluster 2.
    (9 articles total, 3 per cluster, k = int(sqrt(9)) = 3)
    """
    seen_order: list[str] = []
    dim = 8
    vectors = _build_cluster_vectors(3, 3, dim)  # 9 vectors, 3 clusters of 3

    def _fake_embed(text: str, ollama_url: str) -> list[float]:
        if text not in seen_order:
            seen_order.append(text)
        idx = seen_order.index(text)
        return vectors[idx % len(vectors)]

    return _fake_embed


def _fake_name_cluster(articles, ollama_url):
    """Deterministic theme namer for tests."""
    first_summary = articles[0]["summary"] if articles else ""
    if "Production" in first_summary:
        return "Production milestones"
    if "Capital" in first_summary:
        return "Capital raises"
    return "Management changes"


# ── Tests: claim_buckets ──────────────────────────────────────────────────────

def test_research_returns_three_clusters(research_client):
    client, _ = research_client
    with (
        patch("whatsapp_archive.research.dossier.embed", side_effect=_fake_embed_factory([])),
        patch("whatsapp_archive.research.dossier._name_cluster_with_llm", side_effect=_fake_name_cluster),
    ):
        r = client.get("/api/research/entity/Vista Gold")
    assert r.status_code == 200
    data = r.json()
    assert len(data["claim_buckets"]) == 3


def test_research_cluster_article_ids_cover_all(research_client):
    client, _ = research_client
    with (
        patch("whatsapp_archive.research.dossier.embed", side_effect=_fake_embed_factory([])),
        patch("whatsapp_archive.research.dossier._name_cluster_with_llm", side_effect=_fake_name_cluster),
    ):
        r = client.get("/api/research/entity/Vista Gold")
    data = r.json()
    all_ids = [aid for b in data["claim_buckets"] for aid in b["article_ids"]]
    assert len(all_ids) == 9


# ── Tests: narrative arc ──────────────────────────────────────────────────────

def test_narrative_arc_months(research_client):
    client, _ = research_client
    with (
        patch("whatsapp_archive.research.dossier.embed", side_effect=_fake_embed_factory([])),
        patch("whatsapp_archive.research.dossier._name_cluster_with_llm", side_effect=_fake_name_cluster),
    ):
        r = client.get("/api/research/entity/Vista Gold")
    arc = r.json()["narrative_arc"]
    # 3 distinct months (2025-01, 2025-02, 2025-03)
    months = [a["month"] for a in arc]
    assert "2025-01" in months
    assert "2025-02" in months
    assert "2025-03" in months


def test_narrative_arc_sentiment_counts(research_client):
    client, _ = research_client
    with (
        patch("whatsapp_archive.research.dossier.embed", side_effect=_fake_embed_factory([])),
        patch("whatsapp_archive.research.dossier._name_cluster_with_llm", side_effect=_fake_name_cluster),
    ):
        r = client.get("/api/research/entity/Vista Gold")
    arc = r.json()["narrative_arc"]
    total_bullish = sum(a["sentiment"]["bullish"] for a in arc)
    total_bearish = sum(a["sentiment"]["bearish"] for a in arc)
    # 9 articles evenly split: 3 bullish, 3 bearish, 3 neutral
    assert total_bullish == 3
    assert total_bearish == 3


# ── Tests: caching ────────────────────────────────────────────────────────────

def test_cache_hit_no_llm_call(research_client):
    client, _ = research_client
    call_count = {"n": 0}

    def _counting_embed(text, url):
        call_count["n"] += 1
        return [0.0] * 768

    with (
        patch("whatsapp_archive.research.dossier.embed", side_effect=_counting_embed),
        patch("whatsapp_archive.research.dossier._name_cluster_with_llm", side_effect=_fake_name_cluster),
    ):
        client.get("/api/research/entity/Vista Gold")
        count_first = call_count["n"]
        client.get("/api/research/entity/Vista Gold")
        count_second = call_count["n"]

    # Second call should not increase the embed count (served from cache)
    assert count_second == count_first


def test_stale_cache_triggers_regeneration(research_client):
    client, archive_dir = research_client
    from whatsapp_archive.scrape.db import open_db as _open

    call_count = {"n": 0}

    def _counting_embed(text, url):
        call_count["n"] += 1
        return [0.0] * 768

    with (
        patch("whatsapp_archive.research.dossier.embed", side_effect=_counting_embed),
        patch("whatsapp_archive.research.dossier._name_cluster_with_llm", side_effect=_fake_name_cluster),
    ):
        client.get("/api/research/entity/Vista Gold")

    # Backdate the cache by 25 hours
    db = _open(archive_dir)
    stale_ts = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
    db.execute(
        "UPDATE entity_dossiers SET generated_at=? WHERE entity_name='vista gold'",
        (stale_ts,),
    )
    db.commit()
    db.close()

    count_before = call_count["n"]

    with (
        patch("whatsapp_archive.research.dossier.embed", side_effect=_counting_embed),
        patch("whatsapp_archive.research.dossier._name_cluster_with_llm", side_effect=_fake_name_cluster),
    ):
        client.get("/api/research/entity/Vista Gold")

    # Embed should be called again (cache was stale)
    assert call_count["n"] > count_before


# ── Tests: API shape ──────────────────────────────────────────────────────────

def test_research_entity_not_found(research_client):
    client, _ = research_client
    r = client.get("/api/research/entity/Nonexistent Corp XYZ")
    assert r.status_code == 404


def test_research_response_shape(research_client):
    client, _ = research_client
    with (
        patch("whatsapp_archive.research.dossier.embed", side_effect=_fake_embed_factory([])),
        patch("whatsapp_archive.research.dossier._name_cluster_with_llm", side_effect=_fake_name_cluster),
    ):
        r = client.get("/api/research/entity/Vista Gold")
    data = r.json()
    assert "entity" in data
    assert "stats" in data
    assert "narrative_arc" in data
    assert "key_authors" in data
    assert "related_entities" in data
    assert "key_articles" in data
    assert "claim_buckets" in data
    assert data["entity"]["name"] == "Vista Gold"
    assert data["entity"]["kind"] == "company"
    assert data["stats"]["article_count"] == 9


def test_research_key_articles_shape(research_client):
    client, _ = research_client
    with (
        patch("whatsapp_archive.research.dossier.embed", side_effect=_fake_embed_factory([])),
        patch("whatsapp_archive.research.dossier._name_cluster_with_llm", side_effect=_fake_name_cluster),
    ):
        r = client.get("/api/research/entity/Vista Gold")
    ka = r.json()["key_articles"]
    assert "most_engaged" in ka
    assert "most_recent" in ka
    assert "most_quoted" in ka
    assert len(ka["most_engaged"]) <= 5
    assert len(ka["most_recent"]) <= 5


def test_research_key_authors(research_client):
    client, _ = research_client
    with (
        patch("whatsapp_archive.research.dossier.embed", side_effect=_fake_embed_factory([])),
        patch("whatsapp_archive.research.dossier._name_cluster_with_llm", side_effect=_fake_name_cluster),
    ):
        r = client.get("/api/research/entity/Vista Gold")
    authors = r.json()["key_authors"]
    assert len(authors) > 0
    for a in authors:
        assert "handle" in a
        assert "article_count" in a
        assert "avg_sentiment" in a
        assert a["avg_sentiment"] in ("bullish", "bearish", "neutral")


def test_research_force_refresh(research_client):
    client, _ = research_client
    call_count = {"n": 0}

    def _counting_embed(text, url):
        call_count["n"] += 1
        return [0.0] * 768

    with (
        patch("whatsapp_archive.research.dossier.embed", side_effect=_counting_embed),
        patch("whatsapp_archive.research.dossier._name_cluster_with_llm", side_effect=_fake_name_cluster),
    ):
        client.get("/api/research/entity/Vista Gold")
        count_first = call_count["n"]
        # Force refresh should re-run embedding
        client.get("/api/research/entity/Vista Gold?refresh=true")
        count_refresh = call_count["n"]

    assert count_refresh > count_first
