"""Image OCR for tweet media. Extracts text from chart screenshots via tesseract."""

from __future__ import annotations

import io
import json
import logging
import sqlite3
from datetime import datetime, timezone

import httpx

from ..scrape.db import compute_tweet_search_blob, get_media_ocr_for_article, upsert_media_ocr

logger = logging.getLogger(__name__)

_MAX_IMAGE_BYTES = 5 * 1024 * 1024  # 5 MB


def _ocr_image_bytes(data: bytes) -> str:
    """Run OCR on raw image bytes. Returns extracted text or '' on failure."""
    import pytesseract
    from PIL import Image

    img = Image.open(io.BytesIO(data))
    return pytesseract.image_to_string(img).strip()


def _fetch_image(url: str) -> bytes:
    resp = httpx.get(url, timeout=10, follow_redirects=True)
    resp.raise_for_status()
    content = resp.content
    if len(content) > _MAX_IMAGE_BYTES:
        raise ValueError(f"Image too large: {len(content)} bytes (max {_MAX_IMAGE_BYTES})")
    return content


def ocr_article_media(
    article_id: int,
    db: sqlite3.Connection,
) -> dict:
    """OCR all unprocessed media URLs for the article.

    Idempotent — skips URLs already present in tweet_media_ocr.
    Returns a result dict with keys: article_id, processed, skipped, failed.
    """
    row = db.execute(
        "SELECT * FROM articles WHERE id = ? AND status = 'ok'",
        (article_id,),
    ).fetchone()
    if not row or not row["tweet_meta"]:
        return {"article_id": article_id, "skipped": True, "reason": "no tweet_meta"}

    try:
        meta = json.loads(row["tweet_meta"])
    except (ValueError, TypeError):
        return {"article_id": article_id, "skipped": True, "reason": "invalid tweet_meta"}

    media_urls: list[str] = meta.get("media_urls") or []
    if not media_urls:
        return {"article_id": article_id, "skipped": True, "reason": "no media_urls"}

    existing = {r["media_url"] for r in get_media_ocr_for_article(db, article_id)}
    to_process = [u for u in media_urls if u not in existing]
    if not to_process:
        return {"article_id": article_id, "skipped": True, "reason": "all already ocr'd"}

    processed = 0
    failed = 0
    now = datetime.now(timezone.utc).isoformat()

    for url in to_process:
        try:
            img_data = _fetch_image(url)
            ocr_text = _ocr_image_bytes(img_data)
            upsert_media_ocr(db, article_id, url, ocr_text, extracted_at=now)
            processed += 1
        except ImportError:
            logger.warning("pytesseract or Pillow not installed — OCR skipped for %s", url)
            upsert_media_ocr(db, article_id, url, "", extracted_at=now)
            processed += 1
        except Exception as exc:
            logger.warning("OCR failed for %s: %s", url, exc)
            failed += 1

    if processed:
        db.commit()
        # Rebuild tweet_search_blob to include OCR text
        all_ocr = get_media_ocr_for_article(db, article_id)
        ocr_texts = [r["ocr_text"] for r in all_ocr if r["ocr_text"]]
        new_blob = compute_tweet_search_blob(meta, ocr_texts=ocr_texts)
        db.execute(
            "UPDATE articles SET tweet_search_blob = ? WHERE id = ?",
            (new_blob, article_id),
        )
        db.execute("INSERT INTO articles_fts(articles_fts) VALUES('rebuild')")
        db.commit()

    return {"article_id": article_id, "processed": processed, "failed": failed}
