"""U4 — tweet metadata (author, hashtags, mentions) is queryable via /api/search."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from whatsapp_archive import create_app
from whatsapp_archive.scrape.db import (
    compute_tweet_search_blob,
    open_db,
    upsert_article,
    upsert_enrichment,
)

FIXTURE_CHAT = Path(__file__).parent / "fixtures" / "mini_chat.txt"


@pytest.fixture(autouse=True)
def _no_background_loops(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BACKGROUND_SCRAPE", "false")
    monkeypatch.setenv("BACKGROUND_ENRICH", "false")


_TWEET_META = {
    "author_name": "Charlie Bilello",
    "author_handle": "charliebilello",
    "hashtags": ["Gold", "Macro"],
    "mentioned_handles": ["JanGold_"],
    "embedded_urls": [],
}

_TWEET_META_JSON = json.dumps(_TWEET_META)


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    shutil.copy(FIXTURE_CHAT, archive_dir / "mini_chat.txt")

    db = open_db(archive_dir)

    # Tweet article — metadata includes author, hashtags, mention
    upsert_article(
        db,
        "https://x.com/charliebilello/status/1",
        "ok",
        title="@charliebilello: Gold is up",
        raw_text="Gold is up today #Gold #Macro",
        tweet_meta=_TWEET_META_JSON,
    )

    # Plain article — only raw_text, no tweet_meta
    upsert_article(
        db,
        "https://example.com/plain-article",
        "ok",
        title="Silver prices rise",
        raw_text="Silver futures climbed 2% on Monday amid demand from industry.",
    )

    return TestClient(create_app(archive_dir))


# ── compute_tweet_search_blob unit test ───────────────────────────────────────

def test_compute_blob_includes_all_fields():
    blob = compute_tweet_search_blob(_TWEET_META)
    assert "Charlie Bilello" in blob
    assert "@charliebilello" in blob
    assert "#Gold" in blob
    assert "#Macro" in blob
    assert "@JanGold_" in blob


def test_compute_blob_empty_meta():
    assert compute_tweet_search_blob({}) == ""


# ── /api/search FTS against tweet_search_blob ────────────────────────────────

def test_search_by_author_name(client):
    r = client.get("/api/search?q=Charlie+Bilello&kind=article")
    assert r.status_code == 200
    results = r.json()["results"]
    assert len(results) >= 1
    assert any("charliebilello" in (h.get("url") or "") for h in results)


def test_search_by_hashtag(client):
    r = client.get("/api/search?q=%23Gold&kind=article")
    assert r.status_code == 200
    results = r.json()["results"]
    assert len(results) >= 1
    urls = [h.get("url") for h in results]
    assert "https://x.com/charliebilello/status/1" in urls


def test_search_by_mention(client):
    r = client.get("/api/search?q=%40JanGold_&kind=article")
    assert r.status_code == 200
    results = r.json()["results"]
    assert len(results) >= 1
    assert any("charliebilello" in (h.get("url") or "") for h in results)


def test_match_field_meta_for_tweet_metadata_hit(client):
    r = client.get("/api/search?q=%23Gold&kind=article")
    results = r.json()["results"]
    tweet_hits = [h for h in results if "charliebilello" in (h.get("url") or "")]
    assert tweet_hits, "Expected a hit for the tweet article"
    assert tweet_hits[0]["match_field"] == "meta"


def test_raw_text_search_still_works(client):
    """Regression: plain article text still hits when tweet metadata is not involved."""
    r = client.get("/api/search?q=Silver+futures&kind=article")
    assert r.status_code == 200
    results = r.json()["results"]
    assert any("example.com/plain-article" in (h.get("url") or "") for h in results)


def test_upsert_auto_derives_blob(tmp_path):
    """upsert_article populates tweet_search_blob automatically from tweet_meta."""
    archive_dir = tmp_path / "db_only"
    archive_dir.mkdir()
    db = open_db(archive_dir)
    upsert_article(
        db,
        "https://x.com/test/status/99",
        "ok",
        title="test tweet",
        raw_text="test",
        tweet_meta=json.dumps({"author_handle": "tester", "hashtags": ["BTC"]}),
    )
    row = db.execute(
        "SELECT tweet_search_blob FROM articles WHERE url=?",
        ("https://x.com/test/status/99",),
    ).fetchone()
    assert row is not None
    assert "@tester" in (row["tweet_search_blob"] or "")
    assert "#BTC" in (row["tweet_search_blob"] or "")
