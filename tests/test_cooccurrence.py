"""I13 — Entity co-occurrence endpoint."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from whatsapp_archive import create_app
from whatsapp_archive.scrape.db import get_cooccurrence, open_db, upsert_article

FIXTURE_CHAT = Path(__file__).parent / "fixtures" / "mini_chat.txt"


def _meta(author: str, hashtags: list, mentions: list = None, tickers: list = None) -> str:
    return json.dumps({
        "author_handle": author,
        "text": "",
        "hashtags": hashtags,
        "mentioned_handles": mentions or [],
        "tickers": tickers or [],
    })


@pytest.fixture(autouse=True)
def _no_background(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BACKGROUND_SCRAPE", "false")
    monkeypatch.setenv("BACKGROUND_ENRICH", "false")
    monkeypatch.setenv("BACKGROUND_OCR", "false")


@pytest.fixture
def seeded_db(tmp_path: Path):
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    shutil.copy(FIXTURE_CHAT, archive_dir / "mini_chat.txt")
    db = open_db(archive_dir)
    # Seeded per ticket spec:
    # A: [Gold, Macro]        author: alice
    # B: [Gold, BRICS]        author: bob
    # C: [Gold, Macro]        author: alice
    # D: [Silver, BRICS]      author: carol
    # E: [Gold, Macro, BRICS] author: alice
    # F: [Silver]             author: carol
    articles = [
        ("https://x.com/a/1", "alice", ["Gold", "Macro"],        [], []),
        ("https://x.com/b/2", "bob",   ["Gold", "BRICS"],        [], []),
        ("https://x.com/c/3", "alice", ["Gold", "Macro"],        [], []),
        ("https://x.com/d/4", "carol", ["Silver", "BRICS"],      [], []),
        ("https://x.com/e/5", "alice", ["Gold", "Macro", "BRICS"], [], []),
        ("https://x.com/f/6", "carol", ["Silver"],               [], []),
    ]
    for url, author, hashtags, mentions, tickers in articles:
        upsert_article(db, url, "ok", title=url,
                       tweet_meta=_meta(author, hashtags, mentions, tickers),
                       fetched_at="2024-01-01T00:00:00")
    return db


@pytest.fixture
def client(tmp_path: Path, seeded_db):
    archive_dir = tmp_path / "archive"
    seeded_db.commit()
    seeded_db.close()
    app = create_app(archive_dir)
    return TestClient(app)


# ── DB helper tests ───────────────────────────────────────────────────────────

def test_gold_hashtag_cooccurrence(seeded_db):
    hits = get_cooccurrence(seeded_db, "hashtag", "Gold", "hashtag", limit=20)
    counts = {h["value"]: h["count"] for h in hits}
    assert counts.get("Macro") == 3
    assert counts.get("BRICS") == 2
    assert "Gold" not in counts  # seed excluded


def test_silver_hashtag_cooccurrence(seeded_db):
    hits = get_cooccurrence(seeded_db, "hashtag", "Silver", "hashtag", limit=20)
    counts = {h["value"]: h["count"] for h in hits}
    assert counts.get("BRICS") == 1
    assert "Macro" not in counts
    assert "Gold" not in counts


def test_sorted_by_count_descending(seeded_db):
    hits = get_cooccurrence(seeded_db, "hashtag", "Gold", "hashtag", limit=20)
    counts = [h["count"] for h in hits]
    assert counts == sorted(counts, reverse=True)


def test_author_seed_hashtag_target(seeded_db):
    # Alice tweets Gold, Macro, BRICS → seed=author:alice, target=hashtag
    hits = get_cooccurrence(seeded_db, "author", "alice", "hashtag", limit=20)
    counts = {h["value"]: h["count"] for h in hits}
    assert counts.get("Gold") == 3
    assert counts.get("Macro") == 3
    assert "alice" not in counts


def test_hashtag_seed_author_target(seeded_db):
    # Gold appears in alice×3, bob×1
    hits = get_cooccurrence(seeded_db, "hashtag", "Gold", "author", limit=20)
    counts = {h["value"]: h["count"] for h in hits}
    assert counts.get("alice") == 3
    assert counts.get("bob") == 1
    # carol doesn't tweet Gold
    assert "carol" not in counts


def test_empty_seed(seeded_db):
    hits = get_cooccurrence(seeded_db, "hashtag", "Nonexistent", "hashtag", limit=20)
    assert hits == []


def test_invalid_kind_returns_empty(seeded_db):
    hits = get_cooccurrence(seeded_db, "hashtag", "Gold", "unknown_kind", limit=20)
    assert hits == []


def test_limit_respected(seeded_db):
    hits = get_cooccurrence(seeded_db, "hashtag", "Gold", "hashtag", limit=1)
    assert len(hits) <= 1


# ── API endpoint tests ────────────────────────────────────────────────────────

def test_api_cooccurrence_200(client):
    r = client.get("/api/cooccurrence?seed_kind=hashtag&seed_value=Gold&kind=hashtag")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    assert len(data) > 0


def test_api_cooccurrence_counts(client):
    r = client.get("/api/cooccurrence?seed_kind=hashtag&seed_value=Gold&kind=hashtag")
    data = r.json()
    counts = {h["value"]: h["count"] for h in data}
    assert counts.get("Macro") == 3
    assert counts.get("BRICS") == 2


def test_api_cooccurrence_structure(client):
    r = client.get("/api/cooccurrence?seed_kind=hashtag&seed_value=Gold&kind=hashtag")
    for item in r.json():
        assert "value" in item
        assert "count" in item
        assert "kind" in item
        assert item["kind"] == "hashtag"


def test_api_cooccurrence_invalid_kind(client):
    r = client.get("/api/cooccurrence?seed_kind=hashtag&seed_value=Gold&kind=invalid")
    assert r.status_code == 422


def test_api_cooccurrence_empty_seed(client):
    r = client.get("/api/cooccurrence?seed_kind=hashtag&seed_value=Nonexistent&kind=hashtag")
    assert r.status_code == 200
    assert r.json() == []


def test_api_cooccurrence_network_200(client):
    r = client.get("/api/cooccurrence/network?seed_kind=hashtag&seed_value=Gold")
    assert r.status_code == 200
    data = r.json()
    assert "nodes" in data
    assert "edges" in data


def test_api_cooccurrence_network_structure(client):
    r = client.get("/api/cooccurrence/network?seed_kind=hashtag&seed_value=Gold")
    data = r.json()
    seed_node = next((n for n in data["nodes"] if n["kind"] == "hashtag" and n["value"] == "Gold"), None)
    assert seed_node is not None
    # All edges source from the seed
    for e in data["edges"]:
        assert e["source"] == "hashtag:Gold"
