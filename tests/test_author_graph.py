"""E6 — Author-influence + mention graph tests."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from whatsapp_archive import create_app
from whatsapp_archive.scrape.db import open_db, upsert_article
from whatsapp_archive.enrich.author_graph import rebuild_author_edges, get_author_influence

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
def db_and_client(tmp_path: Path):
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    shutil.copy(FIXTURE_CHAT, archive_dir / "mini_chat.txt")
    conn = open_db(archive_dir)

    # @x mentions @y in one tweet
    upsert_article(
        conn, "https://x.com/x/1", "ok",
        tweet_meta=json.dumps({
            "author_handle": "x",
            "text": "hello @y",
            "mentioned_handles": ["y"],
        }),
        published_at="2025-01-01T10:00:00",
    )
    # @x also mentions @y again
    upsert_article(
        conn, "https://x.com/x/2", "ok",
        tweet_meta=json.dumps({
            "author_handle": "x",
            "text": "hi @y again",
            "mentioned_handles": ["y"],
        }),
        published_at="2025-01-02T10:00:00",
    )
    # @w mentions @y once
    upsert_article(
        conn, "https://x.com/w/1", "ok",
        tweet_meta=json.dumps({
            "author_handle": "w",
            "text": "shoutout @y",
            "mentioned_handles": ["y"],
        }),
        published_at="2025-01-03T10:00:00",
    )
    conn.commit()
    client = TestClient(create_app(archive_dir))
    yield conn, client
    conn.close()


# ── rebuild_author_edges ──────────────────────────────────────────────────────

def test_rebuild_creates_edges(db_and_client):
    conn, _ = db_and_client
    n = rebuild_author_edges(conn)
    assert n >= 2  # x→y (mentions), w→y (mentions)


def test_edge_weight_aggregated(db_and_client):
    conn, _ = db_and_client
    rebuild_author_edges(conn)
    row = conn.execute(
        "SELECT weight FROM author_edges WHERE src_author='x' AND dst_author='y' AND kind='mentions'"
    ).fetchone()
    assert row is not None
    assert row["weight"] == 2  # x mentions y twice


def test_distinct_src_edge(db_and_client):
    conn, _ = db_and_client
    rebuild_author_edges(conn)
    row = conn.execute(
        "SELECT weight FROM author_edges WHERE src_author='w' AND dst_author='y' AND kind='mentions'"
    ).fetchone()
    assert row is not None
    assert row["weight"] == 1


# ── get_author_influence ──────────────────────────────────────────────────────

def test_influence_out_degree(db_and_client):
    conn, _ = db_and_client
    rebuild_author_edges(conn)
    inf = get_author_influence(conn, "x")
    assert inf is not None
    assert inf["out_degree"] == 1  # x → y only


def test_influence_in_degree(db_and_client):
    conn, _ = db_and_client
    rebuild_author_edges(conn)
    inf = get_author_influence(conn, "y")
    assert inf is not None
    assert inf["in_degree"] == 2  # mentioned by x and w


def test_influence_top_inbound(db_and_client):
    conn, _ = db_and_client
    rebuild_author_edges(conn)
    inf = get_author_influence(conn, "y")
    handles = [e["from"] for e in inf["top_inbound"]]
    assert "x" in handles
    assert "w" in handles


def test_influence_centrality_score(db_and_client):
    conn, _ = db_and_client
    rebuild_author_edges(conn)
    inf_y = get_author_influence(conn, "y")
    inf_x = get_author_influence(conn, "x")
    # y receives more mentions → higher centrality
    assert inf_y["centrality_score"] > inf_x["centrality_score"]


def test_influence_returns_none_for_unknown(db_and_client):
    conn, _ = db_and_client
    rebuild_author_edges(conn)
    assert get_author_influence(conn, "nobody_here") is None


# ── API endpoints ─────────────────────────────────────────────────────────────

def test_api_influence_endpoint(db_and_client):
    conn, client = db_and_client
    rebuild_author_edges(conn)
    r = client.get("/api/authors/x/influence")
    assert r.status_code == 200
    data = r.json()
    assert data["handle"] == "x"
    assert data["out_degree"] == 1


def test_api_influence_404(db_and_client):
    conn, client = db_and_client
    rebuild_author_edges(conn)
    r = client.get("/api/authors/nobody/influence")
    assert r.status_code == 404


def test_api_author_graph(db_and_client):
    conn, client = db_and_client
    rebuild_author_edges(conn)
    r = client.get("/api/research/author-graph?seed=x&depth=1")
    assert r.status_code == 200
    data = r.json()
    node_ids = {n["id"] for n in data["nodes"]}
    assert "x" in node_ids
    assert "y" in node_ids


def test_api_rebuild_edges(db_and_client):
    conn, client = db_and_client
    r = client.post("/api/authors/edges/rebuild")
    assert r.status_code == 200
    assert r.json()["edges_rebuilt"] >= 2
