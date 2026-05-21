"""I18 — Bulk re-scrape + failed-scrape recovery."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from whatsapp_archive import create_app
from whatsapp_archive.scrape.article import ScrapedArticle
from whatsapp_archive.scrape.db import open_db, upsert_article

FIXTURE_CHAT = Path(__file__).parent / "fixtures" / "mini_chat.txt"


def _ok_article(url):
    return ScrapedArticle(
        url=url, title="OK", author="alice",
        published_at=None, raw_text="text ok",
        og_image_url=None, scrape_method="syndication",
        tweet={"author_handle": "alice", "text": "test"},
    )


@pytest.fixture(autouse=True)
def _no_background(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BACKGROUND_SCRAPE", "false")
    monkeypatch.setenv("BACKGROUND_ENRICH", "false")
    monkeypatch.setenv("BACKGROUND_OCR", "false")
    monkeypatch.setenv("BACKGROUND_DIGESTS", "false")
    monkeypatch.setenv("BACKGROUND_ENGAGEMENT_REFRESH", "false")


@pytest.fixture
def client(tmp_path: Path):
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    shutil.copy(FIXTURE_CHAT, archive_dir / "mini_chat.txt")
    db = open_db(archive_dir)

    upsert_article(db, "https://x.com/ok/1", "ok",
                   fetched_at="2024-06-01T00:00:00", title="Good article")
    upsert_article(db, "https://x.com/fail/1", "failed",
                   fetched_at="2024-06-02T00:00:00",
                   error="TransientError: timeout")
    upsert_article(db, "https://x.com/fail/2", "failed",
                   fetched_at="2024-06-03T00:00:00",
                   error="Gone404: not found")
    upsert_article(db, "https://x.com/block/1", "blocked",
                   fetched_at="2024-06-04T00:00:00",
                   error="BlockedPaywall: age restricted")

    db.commit()
    db.close()
    app = create_app(archive_dir)
    return TestClient(app)


# ── GET /api/articles/by_status ───────────────────────────────────────────────

def test_by_status_failed(client):
    r = client.get("/api/articles/by_status?status=failed")
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 2
    assert all(i["status"] == "failed" for i in data["items"])


def test_by_status_blocked(client):
    r = client.get("/api/articles/by_status?status=blocked")
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 1
    assert data["items"][0]["status"] == "blocked"


def test_by_status_ok_not_in_failed(client):
    r = client.get("/api/articles/by_status?status=failed")
    urls = [i["url"] for i in r.json()["items"]]
    assert "https://x.com/ok/1" not in urls


def test_by_status_pagination(client):
    r = client.get("/api/articles/by_status?status=failed&page=1&page_size=1")
    assert r.status_code == 200
    data = r.json()
    assert len(data["items"]) == 1
    assert data["total"] == 2


# ── GET /api/articles/scrape_errors/summary ───────────────────────────────────

def test_scrape_errors_summary(client):
    r = client.get("/api/articles/scrape_errors/summary")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    total_articles = sum(c["count"] for c in data)
    assert total_articles >= 3  # 2 failed + 1 blocked


def test_scrape_errors_summary_has_sample_urls(client):
    r = client.get("/api/articles/scrape_errors/summary")
    for cluster in r.json():
        assert "sample_urls" in cluster
        assert isinstance(cluster["sample_urls"], list)


# ── POST /api/articles/{id}/retry ─────────────────────────────────────────────

def test_retry_failed_article(client):
    r = client.get("/api/articles/by_status?status=failed")
    article_id = r.json()["items"][0]["id"]
    url = r.json()["items"][0]["url"]

    with patch("whatsapp_archive.api.scrape_generic", return_value=_ok_article(url)):
        r2 = client.post(f"/api/articles/{article_id}/retry")

    assert r2.status_code == 200
    assert r2.json()["status"] == "ok"


def test_retry_404(client):
    r = client.post("/api/articles/99999/retry")
    assert r.status_code == 404


def test_retry_updates_db(client):
    r = client.get("/api/articles/by_status?status=failed")
    item = r.json()["items"][0]
    url = item["url"]
    article_id = item["id"]

    with patch("whatsapp_archive.api.scrape_generic", return_value=_ok_article(url)):
        client.post(f"/api/articles/{article_id}/retry")

    r2 = client.get("/api/articles/by_status?status=failed")
    ids = [i["id"] for i in r2.json()["items"]]
    assert article_id not in ids


# ── POST /api/articles/retry_bulk ─────────────────────────────────────────────

def test_retry_bulk(client):
    r = client.get("/api/articles/by_status?status=failed")
    ids = [i["id"] for i in r.json()["items"]]
    with patch("whatsapp_archive.scrape.background.scrape_generic",
               return_value=_ok_article("https://x.com/fail/1")):
        r2 = client.post("/api/articles/retry_bulk", json={"ids": ids})
    assert r2.status_code == 200
    assert r2.json()["enqueued"] == len(ids)


def test_retry_bulk_empty(client):
    r = client.post("/api/articles/retry_bulk", json={"ids": []})
    assert r.status_code == 200
    assert r.json()["enqueued"] == 0


def test_retry_bulk_unknown_ids(client):
    r = client.post("/api/articles/retry_bulk", json={"ids": [99998, 99999]})
    assert r.status_code == 200
    assert r.json()["enqueued"] == 0  # no such articles


# ── POST /api/articles/{id}/mark_blocked ──────────────────────────────────────

def test_mark_blocked(client):
    r = client.get("/api/articles/by_status?status=failed")
    item = r.json()["items"][0]
    r2 = client.post(f"/api/articles/{item['id']}/mark_blocked",
                     json={"reason": "spam-account"})
    assert r2.status_code == 200
    assert r2.json()["status"] == "blocked"
    assert r2.json()["error"] == "spam-account"


def test_mark_blocked_404(client):
    r = client.post("/api/articles/99999/mark_blocked", json={"reason": "test"})
    assert r.status_code == 404


def test_mark_blocked_removes_from_failed_queue(client):
    r = client.get("/api/articles/by_status?status=failed")
    item = r.json()["items"][0]
    client.post(f"/api/articles/{item['id']}/mark_blocked", json={"reason": "manual"})
    r2 = client.get("/api/articles/by_status?status=failed")
    ids = [i["id"] for i in r2.json()["items"]]
    assert item["id"] not in ids
