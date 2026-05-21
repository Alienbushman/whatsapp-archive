"""Backfill tweet_search_blob for existing rows that have tweet_meta but no blob.

Run this once after upgrading to the U4 schema so existing tweet rows become
searchable by author / hashtag / mention.

Usage (from inside the web container):
    docker compose exec web python scripts/rebuild_tweet_search_blob.py [--archive-dir /app/data/incoming]

Idempotent: only touches rows where tweet_meta IS NOT NULL AND tweet_search_blob IS NULL.
Finishes by rebuilding articles_fts so the new blobs are indexed immediately.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from whatsapp_archive.scrape.db import compute_tweet_search_blob, open_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("rebuild_tweet_search_blob")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive-dir", type=Path, default=Path("/app/data/incoming"))
    args = parser.parse_args()

    if not args.archive_dir.exists():
        logger.error("Archive dir %s does not exist", args.archive_dir)
        return 1

    db = open_db(args.archive_dir)

    rows = db.execute(
        "SELECT id, tweet_meta FROM articles "
        "WHERE tweet_meta IS NOT NULL AND tweet_search_blob IS NULL"
    ).fetchall()

    logger.info("Found %d rows to backfill", len(rows))
    updated = 0
    for row in rows:
        try:
            meta = json.loads(row["tweet_meta"])
            blob = compute_tweet_search_blob(meta)
            db.execute("UPDATE articles SET tweet_search_blob=? WHERE id=?", (blob, row["id"]))
            updated += 1
        except (ValueError, TypeError) as exc:
            logger.warning("Row %d: could not parse tweet_meta — %s", row["id"], exc)

    if updated:
        db.commit()
        db.execute("INSERT INTO articles_fts(articles_fts) VALUES('rebuild')")
        db.commit()
        logger.info("Backfilled %d rows and rebuilt FTS index", updated)
    else:
        logger.info("Nothing to backfill")

    return 0


if __name__ == "__main__":
    sys.exit(main())
