"""Backfill tweet_links from existing articles.

Usage:
    python scripts/backfill_tweet_links.py --archive-dir ./data
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from whatsapp_archive.scrape.db import open_db, sync_tweet_links


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive-dir", required=True)
    args = parser.parse_args()

    db = open_db(Path(args.archive_dir))
    rows = db.execute(
        "SELECT id, tweet_meta FROM articles WHERE tweet_meta IS NOT NULL AND status='ok'"
    ).fetchall()
    print(f"Processing {len(rows)} articles with tweet_meta…")

    quoted = reply = skip = 0
    for row in rows:
        try:
            meta = json.loads(row["tweet_meta"])
        except (ValueError, TypeError):
            skip += 1
            continue
        counts = sync_tweet_links(db, row["id"], meta)
        quoted += counts["quoted"]
        reply += counts["reply"]

    print(f"Done: {quoted} quoted edges, {reply} reply edges, {skip} skipped")


if __name__ == "__main__":
    main()
