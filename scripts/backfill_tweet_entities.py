"""Backfill entities + article_entities from existing tweet_meta.

Usage:
    python scripts/backfill_tweet_entities.py [--archive-dir ./sample-archive]

Idempotent — safe to run multiple times. Skips articles without tweet_meta.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Allow running from repo root without installing the package
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from whatsapp_archive.scrape.db import open_db, sync_tweet_entities


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill tweet_meta entities")
    parser.add_argument(
        "--archive-dir",
        default="./sample-archive",
        help="Path to the archive directory containing articles.db",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print counts without writing")
    args = parser.parse_args()

    archive_dir = Path(args.archive_dir)
    if not archive_dir.exists():
        print(f"Archive dir not found: {archive_dir}", file=sys.stderr)
        sys.exit(1)

    db = open_db(archive_dir)
    rows = db.execute(
        "SELECT id, tweet_meta FROM articles WHERE tweet_meta IS NOT NULL AND status='ok'"
    ).fetchall()

    total = {"authors": 0, "mentions": 0, "hashtags": 0, "tickers": 0}
    processed = 0
    skipped = 0

    for row in rows:
        try:
            meta = json.loads(row["tweet_meta"])
        except (ValueError, TypeError):
            skipped += 1
            continue

        if args.dry_run:
            processed += 1
            continue

        counts = sync_tweet_entities(db, row["id"], meta)
        for k in total:
            total[k] += counts[k]
        processed += 1

    db.close()

    print(f"Processed {processed} articles, skipped {skipped} (bad JSON)")
    if not args.dry_run:
        print(f"Entities synced — authors: {total['authors']}, mentions: {total['mentions']}, "
              f"hashtags: {total['hashtags']}, tickers: {total['tickers']}")


if __name__ == "__main__":
    main()
