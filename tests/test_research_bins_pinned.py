"""Q19 — research bins with_pinned_for query param."""
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


@pytest.fixture(autouse=True)
def _no_background(monkeypatch):
    for env in ["BACKGROUND_SCRAPE", "BACKGROUND_ENRICH", "BACKGROUND_OCR",
                "BACKGROUND_DIGESTS", "BACKGROUND_ENGAGEMENT_REFRESH",
                "BACKGROUND_CLUSTERING", "BACKGROUND_PROFILES"]:
        monkeypatch.setenv(env, "false")


@pytest.fixture
def setup(tmp_path):
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    shutil.copy(FIXTURE_CHAT, archive_dir / "mini_chat.txt")
    conn = open_db(archive_dir)

    aid = upsert_article(
        conn, "https://x.com/test/1", "ok",
        title="Test",
        tweet_meta=json.dumps({"author_handle": "t", "text": "x"}),
        published_at="2025-01-01T10:00:00",
    )
    conn.commit()

    bin1 = create_research_bin(conn, name="Bin A")["id"]
    bin2 = create_research_bin(conn, name="Bin B")["id"]
    add_research_bin_item(conn, bin1, "article", str(aid))
    conn.commit()

    app = create_app(archive_dir)
    client = TestClient(app)
    yield client, aid, bin1, bin2
    conn.close()


def test_without_pinned_for_returns_bins(setup):
    client, _, _, _ = setup
    r = client.get("/api/research_bins")
    assert r.status_code == 200
    bins = r.json()
    assert len(bins) == 2


def test_with_pinned_for_adds_is_pinned_here(setup):
    client, aid, bin1, _ = setup
    r = client.get(f"/api/research_bins?with_pinned_for=article:{aid}")
    assert r.status_code == 200
    bins = r.json()
    assert all("is_pinned_here" in b for b in bins)


def test_pinned_bin_is_true(setup):
    client, aid, bin1, _ = setup
    r = client.get(f"/api/research_bins?with_pinned_for=article:{aid}")
    bins = {b["id"]: b for b in r.json()}
    assert bins[bin1]["is_pinned_here"] is True


def test_unpinned_bin_is_false(setup):
    client, aid, _, bin2 = setup
    r = client.get(f"/api/research_bins?with_pinned_for=article:{aid}")
    bins = {b["id"]: b for b in r.json()}
    assert bins[bin2]["is_pinned_here"] is False


def test_unknown_article_all_false(setup):
    client, _, _, _ = setup
    r = client.get("/api/research_bins?with_pinned_for=article:99999")
    bins = r.json()
    assert all(not b.get("is_pinned_here") for b in bins)


def test_malformed_pinned_for_ignored(setup):
    client, _, _, _ = setup
    r = client.get("/api/research_bins?with_pinned_for=nocolon")
    assert r.status_code == 200
    bins = r.json()
    assert not any("is_pinned_here" in b for b in bins)
