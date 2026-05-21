"""I8 — Image OCR pipeline: DB helpers, ocr_article_media, and API endpoints."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from whatsapp_archive import create_app
from whatsapp_archive.scrape.db import (
    get_articles_with_unprocessed_media,
    get_media_ocr_for_article,
    open_db,
    upsert_article,
    upsert_media_ocr,
)
from whatsapp_archive.enrich.ocr import ocr_article_media

FIXTURE_CHAT = Path(__file__).parent / "fixtures" / "mini_chat.txt"

_TWEET_META_WITH_MEDIA = json.dumps({
    "author_handle": "alice",
    "text": "Check this chart",
    "hashtags": [],
    "mentioned_handles": [],
    "tickers": [],
    "media_urls": ["https://pbs.twimg.com/media/img1.jpg", "https://pbs.twimg.com/media/img2.jpg"],
})

_TWEET_META_NO_MEDIA = json.dumps({
    "author_handle": "bob",
    "text": "No images here",
    "hashtags": [],
    "mentioned_handles": [],
    "tickers": [],
    "media_urls": [],
})


@pytest.fixture(autouse=True)
def _no_background(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BACKGROUND_SCRAPE", "false")
    monkeypatch.setenv("BACKGROUND_ENRICH", "false")
    monkeypatch.setenv("BACKGROUND_OCR", "false")


@pytest.fixture
def db(tmp_path: Path):
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    shutil.copy(FIXTURE_CHAT, archive_dir / "mini_chat.txt")
    return open_db(archive_dir)


@pytest.fixture
def article_with_media(db):
    aid = upsert_article(
        db, "https://x.com/alice/status/100", "ok",
        title="Media tweet",
        tweet_meta=_TWEET_META_WITH_MEDIA,
        fetched_at="2024-01-10T10:00:00",
    )
    return aid, db


@pytest.fixture
def client_with_media(tmp_path: Path):
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    shutil.copy(FIXTURE_CHAT, archive_dir / "mini_chat.txt")
    db = open_db(archive_dir)
    aid = upsert_article(
        db, "https://x.com/alice/status/200", "ok",
        title="Media tweet 2",
        tweet_meta=_TWEET_META_WITH_MEDIA,
        fetched_at="2024-01-10T10:00:00",
    )
    upsert_media_ocr(db, aid, "https://pbs.twimg.com/media/img1.jpg", "Price: $120", extracted_at="2024-01-10T11:00:00")
    db.commit()
    db.close()
    app = create_app(archive_dir)
    return TestClient(app), aid


# ── DB helpers ────────────────────────────────────────────────────────────────

def test_upsert_and_get_media_ocr(article_with_media):
    aid, db = article_with_media
    upsert_media_ocr(db, aid, "https://pbs.twimg.com/media/img1.jpg", "Hello chart", extracted_at="2024-01-10T11:00:00")
    rows = get_media_ocr_for_article(db, aid)
    assert len(rows) == 1
    assert rows[0]["ocr_text"] == "Hello chart"
    assert rows[0]["media_url"] == "https://pbs.twimg.com/media/img1.jpg"


def test_upsert_media_ocr_idempotent(article_with_media):
    aid, db = article_with_media
    url = "https://pbs.twimg.com/media/img1.jpg"
    upsert_media_ocr(db, aid, url, "first", extracted_at="2024-01-10T11:00:00")
    upsert_media_ocr(db, aid, url, "updated", extracted_at="2024-01-10T12:00:00")
    rows = get_media_ocr_for_article(db, aid)
    assert len(rows) == 1
    assert rows[0]["ocr_text"] == "updated"


def test_get_articles_with_unprocessed_media(article_with_media):
    aid, db = article_with_media
    rows = get_articles_with_unprocessed_media(db)
    ids = [r["id"] for r in rows]
    assert aid in ids


def test_get_articles_with_unprocessed_media_excludes_fully_processed(article_with_media):
    aid, db = article_with_media
    # Process all media URLs for this article
    upsert_media_ocr(db, aid, "https://pbs.twimg.com/media/img1.jpg", "a", extracted_at="2024-01-10T11:00:00")
    upsert_media_ocr(db, aid, "https://pbs.twimg.com/media/img2.jpg", "b", extracted_at="2024-01-10T11:00:00")
    rows = get_articles_with_unprocessed_media(db)
    ids = [r["id"] for r in rows]
    assert aid not in ids


def test_get_articles_with_unprocessed_media_no_media(db):
    aid = upsert_article(
        db, "https://x.com/bob/status/999", "ok",
        title="No media",
        tweet_meta=_TWEET_META_NO_MEDIA,
        fetched_at="2024-01-10T10:00:00",
    )
    rows = get_articles_with_unprocessed_media(db)
    ids = [r["id"] for r in rows]
    assert aid not in ids


# ── ocr_article_media ─────────────────────────────────────────────────────────

def test_ocr_article_media_skips_no_tweet_meta(db):
    aid = upsert_article(db, "https://example.com/article", "ok", title="Plain", fetched_at="2024-01-10T10:00:00")
    result = ocr_article_media(aid, db)
    assert result.get("skipped")


def test_ocr_article_media_skips_no_media_urls(db):
    aid = upsert_article(
        db, "https://x.com/bob/status/1", "ok",
        title="No media",
        tweet_meta=_TWEET_META_NO_MEDIA,
        fetched_at="2024-01-10T10:00:00",
    )
    result = ocr_article_media(aid, db)
    assert result.get("skipped")


def test_ocr_article_media_processes_images(article_with_media):
    aid, db = article_with_media
    fake_image = b"FAKEPNGBYTES"

    with patch("whatsapp_archive.enrich.ocr._fetch_image", return_value=fake_image) as mock_fetch, \
         patch("whatsapp_archive.enrich.ocr._ocr_image_bytes", return_value="extracted text") as mock_ocr:
        result = ocr_article_media(aid, db)

    assert result["processed"] == 2
    assert result.get("failed", 0) == 0
    rows = get_media_ocr_for_article(db, aid)
    assert len(rows) == 2
    texts = {r["media_url"]: r["ocr_text"] for r in rows}
    assert texts["https://pbs.twimg.com/media/img1.jpg"] == "extracted text"
    assert texts["https://pbs.twimg.com/media/img2.jpg"] == "extracted text"


def test_ocr_article_media_skips_already_processed(article_with_media):
    aid, db = article_with_media
    upsert_media_ocr(db, aid, "https://pbs.twimg.com/media/img1.jpg", "cached", extracted_at="2024-01-10T11:00:00")
    upsert_media_ocr(db, aid, "https://pbs.twimg.com/media/img2.jpg", "cached2", extracted_at="2024-01-10T11:00:00")

    with patch("whatsapp_archive.enrich.ocr._fetch_image") as mock_fetch:
        result = ocr_article_media(aid, db)

    mock_fetch.assert_not_called()
    assert result.get("skipped")


def test_ocr_article_media_handles_fetch_failure(article_with_media):
    aid, db = article_with_media
    with patch("whatsapp_archive.enrich.ocr._fetch_image", side_effect=Exception("network error")):
        result = ocr_article_media(aid, db)

    assert result.get("failed", 0) > 0


def test_ocr_article_media_handles_missing_pytesseract(article_with_media):
    aid, db = article_with_media
    fake_image = b"FAKEPNGBYTES"

    with patch("whatsapp_archive.enrich.ocr._fetch_image", return_value=fake_image), \
         patch("whatsapp_archive.enrich.ocr._ocr_image_bytes", side_effect=ImportError("No pytesseract")):
        result = ocr_article_media(aid, db)

    # ImportError is propagated up from _ocr_image_bytes — caught as generic Exception
    # so it counts as failed (not a silent skip like in the inline ImportError branch)
    assert result.get("processed", 0) + result.get("failed", 0) == 2


# ── API endpoints ─────────────────────────────────────────────────────────────

def test_ocr_status_endpoint(tmp_path: Path):
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    shutil.copy(FIXTURE_CHAT, archive_dir / "mini_chat.txt")
    app = create_app(archive_dir)
    client = TestClient(app)
    r = client.get("/api/ocr/status")
    assert r.status_code == 200
    data = r.json()
    assert "phase" in data


def test_article_ocr_endpoint_returns_results(client_with_media):
    client, aid = client_with_media
    r = client.get(f"/api/articles/{aid}/ocr")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    assert len(data) >= 1
    assert "media_url" in data[0]
    assert "ocr_text" in data[0]
    assert data[0]["ocr_text"] == "Price: $120"


def test_article_ocr_endpoint_empty_when_none(client_with_media):
    client, _ = client_with_media
    r = client.get("/api/articles/99999/ocr")
    assert r.status_code == 200
    assert r.json() == []
