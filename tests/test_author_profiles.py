"""E5 — Author profiles and engagement-weighted aggregation tests."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from whatsapp_archive import create_app
from whatsapp_archive.scrape.db import open_db, upsert_article, list_top_authors_windowed, list_top_hashtags_windowed
from whatsapp_archive.enrich.author_profile import rebuild_all_profiles, get_author_profile

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
def db(tmp_path: Path):
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    shutil.copy(FIXTURE_CHAT, archive_dir / "mini_chat.txt")
    conn = open_db(archive_dir)
    yield conn
    conn.close()


@pytest.fixture
def client(tmp_path: Path):
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    shutil.copy(FIXTURE_CHAT, archive_dir / "mini_chat.txt")
    return TestClient(create_app(archive_dir))


def _seed_article(conn, url, handle, favorites, retweets, hashtags=None, published_at="2024-01-01"):
    upsert_article(conn, url, "ok", title=f"Tweet by {handle}")
    art_id = conn.execute("SELECT id FROM articles WHERE url=?", (url,)).fetchone()["id"]
    meta = {
        "tweet_id": url.split("/")[-1],
        "author_handle": handle,
        "author_name": handle.title(),
        "is_verified": handle == "bigname",
        "favorite_count": favorites,
        "retweet_count": retweets,
        "hashtags": hashtags or [],
    }
    conn.execute(
        "UPDATE articles SET tweet_meta=?, published_at=? WHERE id=?",
        (json.dumps(meta), published_at, art_id),
    )
    conn.commit()
    return art_id


# ── rebuild_all_profiles unit tests ──────────────────────────────────────────

def test_rebuild_creates_profiles(db):
    _seed_article(db, "https://x.com/1", "alice", 100, 20)
    _seed_article(db, "https://x.com/2", "bob", 50, 5)
    n = rebuild_all_profiles(db)
    assert n == 2


def test_rebuild_correct_totals(db):
    _seed_article(db, "https://x.com/1", "alice", 100, 20)
    _seed_article(db, "https://x.com/2", "alice", 200, 10)  # second article
    rebuild_all_profiles(db)
    profile = get_author_profile(db, "alice")
    assert profile is not None
    assert profile["total_favorites"] == 300
    assert profile["total_retweets"] == 30
    assert profile["article_count"] == 2


def test_rebuild_idempotent(db):
    _seed_article(db, "https://x.com/1", "alice", 100, 20)
    rebuild_all_profiles(db)
    rebuild_all_profiles(db)
    n = db.execute("SELECT COUNT(*) AS c FROM author_profiles WHERE handle='alice'").fetchone()["c"]
    assert n == 1


def test_rebuild_updates_after_new_article(db):
    _seed_article(db, "https://x.com/1", "alice", 100, 20)
    rebuild_all_profiles(db)
    profile1 = get_author_profile(db, "alice")
    _seed_article(db, "https://x.com/2", "alice", 500, 100)
    rebuild_all_profiles(db)
    profile2 = get_author_profile(db, "alice")
    assert profile2["total_favorites"] > profile1["total_favorites"]


def test_influence_score_verified_higher(db):
    _seed_article(db, "https://x.com/1", "bigname", 50, 5)
    _seed_article(db, "https://x.com/2", "regular", 50, 5)
    rebuild_all_profiles(db)
    big = get_author_profile(db, "bigname")
    reg = get_author_profile(db, "regular")
    assert big["influence_score"] > reg["influence_score"]


# ── Engagement-weighted list_top_authors_windowed ─────────────────────────────

def test_top_authors_engagement_weighted(db):
    # alice: 1 tweet with 1000 favorites
    # bob: 5 tweets with 10 favorites each
    _seed_article(db, "https://x.com/1", "alice", 1000, 0)
    for i in range(5):
        _seed_article(db, f"https://x.com/{i+10}", "bob", 10, 0)

    # By count: bob wins (5 vs 1)
    by_count = list_top_authors_windowed(db, limit=2, weighted=False)
    assert by_count[0]["handle"] == "bob"

    # By engagement: alice wins (1000 vs 50)
    by_eng = list_top_authors_windowed(db, limit=2, weighted=True)
    assert by_eng[0]["handle"] == "alice"


# ── Engagement-weighted list_top_hashtags_windowed ────────────────────────────

def test_top_hashtags_engagement_weighted(db):
    # #viral: 1 article with 500 favorites
    # #common: 5 articles with 10 favorites each
    _seed_article(db, "https://x.com/v1", "alice", 500, 0, hashtags=["viral"])
    for i in range(5):
        _seed_article(db, f"https://x.com/c{i}", "bob", 10, 0, hashtags=["common"])

    by_count = list_top_hashtags_windowed(db, limit=2, weighted=False)
    assert by_count[0]["tag"] == "common"

    by_eng = list_top_hashtags_windowed(db, limit=2, weighted=True)
    assert by_eng[0]["tag"] == "viral"


# ── API tests ─────────────────────────────────────────────────────────────────

def test_api_top_authors_default(client):
    r = client.get("/api/aggregations/top-authors?limit=5")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_api_top_authors_weighted(client):
    r = client.get("/api/aggregations/top-authors?weighted=engagement&limit=5")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_api_author_profile_not_found(client):
    r = client.get("/api/authors/profiles/nobody_xyz_999")
    assert r.status_code == 404


def test_api_rebuild_profiles(client):
    r = client.post("/api/authors/profiles/rebuild")
    assert r.status_code == 200
    assert "profiles_rebuilt" in r.json()
