"""Backfill entities + article_entities from existing LLM-extracted enrichments.

Usage:
    python scripts/backfill_llm_entities.py [--archive-dir ./sample-archive]

Idempotent — safe to run multiple times. Skips articles without enrichments.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from whatsapp_archive.enrich.entity_sync import sync_llm_entities
from whatsapp_archive.scrape.db import open_db


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill LLM entities from enrichments")
    parser.add_argument("--archive-dir", default="./sample-archive")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    archive_dir = Path(args.archive_dir)
    if not archive_dir.exists():
        print(f"Archive dir not found: {archive_dir}", file=sys.stderr)
        sys.exit(1)

    db = open_db(archive_dir)
    rows = db.execute(
        "SELECT article_id, entities FROM article_enrichments WHERE entities IS NOT NULL"
    ).fetchall()

    total: dict[str, int] = {"company": 0, "ticker": 0, "person": 0, "other": 0}
    processed = 0
    skipped = 0

    for row in rows:
        try:
            entities_list = json.loads(row["entities"])
            if not isinstance(entities_list, list):
                skipped += 1
                continue
        except (ValueError, TypeError):
            skipped += 1
            continue

        if args.dry_run:
            processed += 1
            continue

        counts = sync_llm_entities(db, row["article_id"], entities_list)
        for k in ("company", "ticker", "person", "other"):
            total[k] += counts.get(k, 0)
        processed += 1

    db.close()

    print(f"Processed {processed} enrichments, skipped {skipped}")
    if not args.dry_run:
        print(f"Entities synced — company: {total['company']}, ticker: {total['ticker']}, "
              f"person: {total['person']}, other: {total['other']}")


if __name__ == "__main__":
    main()
