"""Re-enrich existing articles using the tweet_meta-aware prompt.

Usage:
    python scripts/reenrich_with_meta.py --archive-dir ./data --limit 10 [--force]

Opt-in and slow — runs Ollama for every article. Use --limit to process in batches.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from whatsapp_archive.scrape.db import open_db, upsert_enrichment
from whatsapp_archive.enrich.ollama import EnrichError, enrich_article, _DEFAULT_MODEL
from datetime import datetime, timezone


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive-dir", required=True)
    parser.add_argument("--ollama-url", default="http://localhost:11434")
    parser.add_argument("--model", default=_DEFAULT_MODEL)
    parser.add_argument("--limit", type=int, default=10, help="Articles to process (0 = all)")
    parser.add_argument("--force", action="store_true", help="Re-enrich even if already enriched")
    args = parser.parse_args()

    db = open_db(Path(args.archive_dir))

    query = """
        SELECT a.id, a.url, a.title, a.raw_text, a.tweet_meta
        FROM articles a
        WHERE a.status = 'ok'
          AND a.tweet_meta IS NOT NULL
    """
    if not args.force:
        query += " AND a.id NOT IN (SELECT article_id FROM article_enrichments)"
    if args.limit:
        query += f" LIMIT {args.limit}"

    rows = db.execute(query).fetchall()
    print(f"Processing {len(rows)} articles…")

    ok = fail = skip = 0
    for row in rows:
        tweet_meta: dict | None = None
        try:
            raw = row["tweet_meta"]
            if raw:
                tweet_meta = json.loads(raw)
        except (ValueError, TypeError):
            pass

        try:
            result = enrich_article(dict(row), args.ollama_url, model=args.model, tweet_meta=tweet_meta)
        except EnrichError as exc:
            print(f"  FAIL {row['id']}: {exc}")
            fail += 1
            continue

        upsert_enrichment(
            db,
            row["id"],
            result.summary,
            json.dumps(result.categories),
            json.dumps([e.model_dump() for e in result.entities]),
            result.sentiment,
            args.model,
            _now_iso(),
        )
        ok += 1
        print(f"  OK   {row['id']} — {result.sentiment}")

    db.commit()
    print(f"\nDone: {ok} ok, {fail} failed, {skip} skipped")


if __name__ == "__main__":
    main()
