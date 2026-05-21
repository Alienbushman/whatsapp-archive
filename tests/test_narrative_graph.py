"""R4 — Narrative entity graph endpoint tests."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from whatsapp_archive import create_app
from whatsapp_archive.scrape.db import (
    open_db,
    upsert_article,
    upsert_entity,
    link_article_entity,
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
def db_client(tmp_path: Path):
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    shutil.copy(FIXTURE_CHAT, archive_dir / "mini_chat.txt")
    conn = open_db(archive_dir)

    # Entity IDs
    eid_a = upsert_entity(conn, "Alpha", "company")
    eid_b = upsert_entity(conn, "Beta", "ticker")
    eid_c = upsert_entity(conn, "Gamma", "hashtag")
    eid_d = upsert_entity(conn, "Delta", "person")

    # Articles: A appears with B 5x, A with C 2x, B with D 1x
    art_ids = []
    for i in range(10):
        aid = upsert_article(conn, f"https://x.com/t/{i}", "ok", title=f"Article {i}",
                             published_at="2025-01-01T10:00:00")
        art_ids.append(aid)

    # A+B: articles 0-4 (5 co-occurrences)
    for i in range(5):
        link_article_entity(conn, art_ids[i], eid_a, "Alpha")
        link_article_entity(conn, art_ids[i], eid_b, "Beta")

    # A+C: articles 5-6 (2 co-occurrences)
    for i in range(5, 7):
        link_article_entity(conn, art_ids[i], eid_a, "Alpha")
        link_article_entity(conn, art_ids[i], eid_c, "Gamma")

    # B+D: article 7 (1 co-occurrence)
    link_article_entity(conn, art_ids[7], eid_b, "Beta")
    link_article_entity(conn, art_ids[7], eid_d, "Delta")

    conn.commit()
    client = TestClient(create_app(archive_dir))
    yield conn, client
    conn.close()


# ── Basic graph construction ──────────────────────────────────────────────────

def test_graph_seed_not_found(db_client):
    _, client = db_client
    r = client.get("/api/research/graph?seed=NonExistent&min_edge_weight=1")
    assert r.status_code == 404


def test_graph_returns_seed_node(db_client):
    _, client = db_client
    r = client.get("/api/research/graph?seed=Alpha&min_edge_weight=1&depth=1")
    assert r.status_code == 200
    data = r.json()
    node_ids = [n["id"] for n in data["nodes"]]
    assert "Alpha" in node_ids


def test_graph_depth1_direct_neighbors_only(db_client):
    _, client = db_client
    r = client.get("/api/research/graph?seed=Alpha&min_edge_weight=1&depth=1")
    assert r.status_code == 200
    data = r.json()
    node_ids = set(n["id"] for n in data["nodes"])
    # Depth=1: Alpha + its direct neighbors Beta, Gamma
    assert "Alpha" in node_ids
    assert "Beta" in node_ids
    assert "Gamma" in node_ids
    # Delta should NOT appear at depth=1 (it co-occurs with Beta, not Alpha directly)
    assert "Delta" not in node_ids


def test_graph_depth2_includes_second_hop(db_client):
    _, client = db_client
    r = client.get("/api/research/graph?seed=Alpha&min_edge_weight=1&depth=2")
    assert r.status_code == 200
    data = r.json()
    node_ids = set(n["id"] for n in data["nodes"])
    # Depth=2: Alpha → Beta → Delta
    assert "Delta" in node_ids


# ── Edge filtering ─────────────────────────────────────────────────────────────

def test_min_edge_weight_filters(db_client):
    _, client = db_client
    r = client.get("/api/research/graph?seed=Alpha&min_edge_weight=3&depth=2")
    assert r.status_code == 200
    data = r.json()
    node_ids = set(n["id"] for n in data["nodes"])
    # A-B weight=5 passes, A-C weight=2 filtered, B-D weight=1 filtered
    assert "Beta" in node_ids
    assert "Gamma" not in node_ids
    assert "Delta" not in node_ids


def test_edges_have_weight_and_supporting_articles(db_client):
    _, client = db_client
    r = client.get("/api/research/graph?seed=Alpha&min_edge_weight=1&depth=1")
    assert r.status_code == 200
    data = r.json()
    edges = {(e["source"], e["target"]): e for e in data["edges"]}
    # Find Alpha-Beta edge
    ab_edge = edges.get(("Alpha", "Beta")) or edges.get(("Beta", "Alpha"))
    assert ab_edge is not None
    assert ab_edge["weight"] == 5
    assert "supporting_articles" in ab_edge
    assert len(ab_edge["supporting_articles"]) <= 3


# ── Node properties ────────────────────────────────────────────────────────────

def test_nodes_have_kind_and_size(db_client):
    _, client = db_client
    r = client.get("/api/research/graph?seed=Alpha&min_edge_weight=1&depth=1")
    assert r.status_code == 200
    data = r.json()
    alpha = next(n for n in data["nodes"] if n["id"] == "Alpha")
    assert alpha["kind"] == "company"
    assert alpha["size"] == 7  # articles 0-6


# ── Response metadata ─────────────────────────────────────────────────────────

def test_graph_metadata(db_client):
    _, client = db_client
    r = client.get("/api/research/graph?seed=Alpha&min_edge_weight=1&window=all&depth=1")
    assert r.status_code == 200
    data = r.json()
    assert data["seed"] == "Alpha"
    assert data["window"] == "all"
