"""One-shot backfill: re-scrape every x.com/twitter.com article that's missing
the new `tweet_meta` JSON (the rich payload introduced by U2).

Usage (from inside the web container):
    docker compose exec web python scripts/backfill_tweet_meta.py [--archive-dir /app/data/incoming] [--limit N]

Idempotent: only touches rows where status='ok' AND tweet_meta IS NULL AND the URL
points at x.com or twitter.com. Reuses the same `_DB_LOCK` as the background
scraper so it's safe to run alongside the live worker loop.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

from whatsapp_archive.scrape.article import scrape_generic
from whatsapp_archive.scrape.background import _DB_LOCK
from whatsapp_archive.scrape.db import open_db, upsert_article

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("backfill")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive-dir", type=Path, default=Path("/app/data/incoming"))
    parser.add_argument("--limit", type=int, default=None, help="Cap the number of rows to process")
    parser.add_argument("--sleep", type=float, default=0.1, help="Delay between calls (be nice to syndication)")
    args = parser.parse_args()

    if not args.archive_dir.exists():
        logger.error("Archive dir %s does not exist", args.archive_dir)
        return 1

    db = open_db(args.archive_dir)
    rows = db.execute(
        "SELECT id, url FROM articles "
        "WHERE status='ok' "
        "  AND tweet_meta IS NULL "
        "  AND (url LIKE '%x.com/%/status/%' OR url LIKE '%twitter.com/%/status/%')"
    ).fetchall()
    if args.limit:
        rows = rows[: args.limit]

    logger.info("Backfilling %d tweet rows", len(rows))
    ok = 0
    failed = 0
    for i, row in enumerate(rows, 1):
        try:
            result = scrape_generic(row["url"])
        except Exception as exc:
            failed += 1
            logger.warning("[%d/%d] %s — scrape failed: %s", i, len(rows), row["url"], exc)
            time.sleep(args.sleep)
            continue

        if result.tweet is None:
            # Non-tweet URL that happens to match — skip.
            time.sleep(args.sleep)
            continue

        with _DB_LOCK:
            upsert_article(
                db,
                result.url,
                "ok",
                title=result.title,
                author=result.author,
                published_at=result.published_at.isoformat() if result.published_at else None,
                raw_text=result.raw_text,
                og_image=result.og_image_url,
                scrape_method=result.scrape_method,
                tweet_meta=json.dumps(result.tweet),
            )
        ok += 1
        if i % 25 == 0:
            logger.info("Progress: %d/%d (%d ok, %d failed)", i, len(rows), ok, failed)
        time.sleep(args.sleep)

    logger.info("Done. %d ok, %d failed out of %d total.", ok, failed, len(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
