"""Sync LLM-extracted entities from article_enrichments into the entities table."""
from __future__ import annotations

import sqlite3


def sync_llm_entities(
    conn: sqlite3.Connection,
    article_id: int,
    entities_list: list[dict],
) -> dict:
    """Upsert LLM-extracted entities into entities + article_entities.

    Each entry in entities_list should have {name, kind, mention_text?}.

    Critical: delegates to upsert_entity() which enforces the kind-precedence
    dedup invariant. Doing INSERT OR IGNORE here bypassed that — the entities
    table's UNIQUE constraint is on (normalized_name, kind), so two rows with
    the same normalized_name but different kinds slipped through (e.g. `gold`
    as both `ticker` and `company`), recreating exactly the fragmentation Q12
    was meant to fix. See bug #41.

    Returns per-kind counts: {company, ticker, person, other, total}.
    """
    # Import here to avoid an enrich → scrape.db circular at module-load time.
    from whatsapp_archive.scrape.db import upsert_entity

    counts: dict[str, int] = {"company": 0, "ticker": 0, "person": 0, "other": 0}

    for ent in entities_list:
        name = (ent.get("name") or "").strip()
        if not name:
            continue
        kind = (ent.get("kind") or "other").strip()
        mention_text = (ent.get("mention_text") or name).strip()

        entity_id = upsert_entity(conn, name, kind)
        if entity_id is None:
            continue

        conn.execute(
            "INSERT OR IGNORE INTO article_entities (article_id, entity_id, mention_text, assigned_by)"
            " VALUES (?, ?, ?, 'llm')",
            (article_id, entity_id, mention_text),
        )
        counts[kind] = counts.get(kind, 0) + 1

    conn.commit()
    counts["total"] = sum(counts.values())
    return counts
