"""Deduplicate entities that share the same normalized_name across different kinds.

Kind precedence (highest wins): ticker > company > other > person.
The canonical entity is whichever has the highest-ranking kind; ties broken by lowest id.

Usage:
    python scripts/dedup_entities.py [--archive-dir ./sample-archive] [--dry-run]

Idempotent: entities that already have canonical_id IS NOT NULL are excluded from
duplicate detection, so repeated runs are safe.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from whatsapp_archive.scrape.db import open_db, merge_entities

# Higher number = more specific / preferred canonical kind
_KIND_RANK: dict[str, int] = {"ticker": 3, "company": 2, "other": 1, "person": 0}


def _pick_canonical(members: list[dict]) -> dict:
    """Return the member that should become the canonical entity."""
    return max(members, key=lambda r: (_KIND_RANK.get(r["kind"], -1), -(r["id"])))


def main() -> None:
    parser = argparse.ArgumentParser(description="Deduplicate entities by normalized_name")
    parser.add_argument("--archive-dir", default="./sample-archive")
    parser.add_argument("--dry-run", action="store_true", help="Print plan without making changes")
    args = parser.parse_args()

    archive_dir = Path(args.archive_dir)
    if not archive_dir.exists():
        print(f"Archive dir not found: {archive_dir}", file=sys.stderr)
        sys.exit(1)

    db = open_db(archive_dir)

    # Fetch all unmerged entities
    rows = db.execute(
        "SELECT id, name, kind, normalized_name FROM entities WHERE canonical_id IS NULL ORDER BY normalized_name, id"
    ).fetchall()

    # Group by normalized_name
    groups: dict[str, list[dict]] = {}
    for row in rows:
        groups.setdefault(row["normalized_name"], []).append(dict(row))

    dupes = {k: v for k, v in groups.items() if len(v) > 1}

    if not dupes:
        print("No duplicate normalized_names found — nothing to do.")
        db.close()
        return

    print(f"Found {len(dupes)} normalized_name(s) with duplicates:")
    total_merged = 0

    for norm_name, members in sorted(dupes.items()):
        canonical = _pick_canonical(members)
        sources = [m for m in members if m["id"] != canonical["id"]]
        source_ids = [m["id"] for m in sources]
        source_desc = ", ".join(
            f"{m['name']} ({m['kind']}, id={m['id']})" for m in sources
        )
        print(
            f"  '{norm_name}': canonical={canonical['name']} ({canonical['kind']}, id={canonical['id']})"
            f"  ← {source_desc}"
        )
        if not args.dry_run:
            result = merge_entities(db, source_ids, canonical["id"])
            print(
                f"    → merged {result['merged']} alias(es), "
                f"{result['target_article_count']} total articles on canonical"
            )
            total_merged += result["merged"]

    alias_count = sum(len(v) - 1 for v in dupes.values())
    if args.dry_run:
        print(f"\n[dry-run] Would merge {alias_count} alias(es) across {len(dupes)} group(s).")
    else:
        print(f"\nDone. Merged {total_merged} alias(es) across {len(dupes)} group(s).")

    db.close()


if __name__ == "__main__":
    main()
