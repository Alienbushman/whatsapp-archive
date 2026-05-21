"""R1 — Entity research workspace: build and cache a full dossier per entity."""

from __future__ import annotations

import json
import math
import sqlite3
from collections import defaultdict

import httpx
import numpy as np

from ..scrape.vector import embed
from ..scrape.db import get_dossier_cache, save_dossier_cache

_OLLAMA_URL = "http://localhost:11434"
_CLUSTER_MODEL = "qwen2.5:3b-instruct"


# ── Entity lookup ─────────────────────────────────────────────────────────────

def _get_entity_canonical(conn: sqlite3.Connection, entity_name: str) -> sqlite3.Row | None:
    normalized = entity_name.strip().lower()
    row = conn.execute(
        "SELECT * FROM entities WHERE normalized_name = ? AND canonical_id IS NULL",
        (normalized,),
    ).fetchone()
    if row:
        return row
    # Try as alias → follow to canonical
    row = conn.execute(
        """SELECT e2.* FROM entities e1
           JOIN entities e2 ON e2.id = e1.canonical_id
           WHERE e1.normalized_name = ?""",
        (normalized,),
    ).fetchone()
    if row:
        return row
    # Partial match (contains)
    return conn.execute(
        "SELECT * FROM entities WHERE normalized_name LIKE ? AND canonical_id IS NULL LIMIT 1",
        (f"%{normalized}%",),
    ).fetchone()


def _get_all_entity_ids(conn: sqlite3.Connection, canonical_id: int) -> list[int]:
    """Return canonical + all alias entity IDs."""
    rows = conn.execute(
        "SELECT id FROM entities WHERE id = ? OR canonical_id = ?",
        (canonical_id, canonical_id),
    ).fetchall()
    return [r["id"] for r in rows]


def _get_articles_with_enrichment(
    conn: sqlite3.Connection, article_ids: list[int]
) -> list[sqlite3.Row]:
    if not article_ids:
        return []
    ph = ",".join("?" * len(article_ids))
    return conn.execute(
        f"""SELECT a.id, a.url, a.title, a.author, a.published_at, a.fetched_at,
               a.tweet_meta, e.summary, e.sentiment, e.entities AS enrichment_entities
            FROM articles a
            LEFT JOIN article_enrichments e ON e.article_id = a.id
            WHERE a.id IN ({ph}) AND a.status = 'ok'""",
        article_ids,
    ).fetchall()


# ── Stats ─────────────────────────────────────────────────────────────────────

def _compute_stats(articles: list[sqlite3.Row]) -> dict:
    if not articles:
        return {"article_count": 0, "first_seen": None, "last_seen": None, "total_engagement": 0}
    dates = []
    total_engagement = 0
    for a in articles:
        d = a["published_at"] or a["fetched_at"]
        if d:
            dates.append(d)
        if a["tweet_meta"]:
            try:
                meta = json.loads(a["tweet_meta"]) if isinstance(a["tweet_meta"], str) else a["tweet_meta"]
                total_engagement += int(meta.get("favorite_count", 0) or 0)
                total_engagement += int(meta.get("retweet_count", 0) or 0)
            except (ValueError, TypeError):
                pass
    return {
        "article_count": len(articles),
        "first_seen": min(dates) if dates else None,
        "last_seen": max(dates) if dates else None,
        "total_engagement": total_engagement,
    }


# ── Narrative arc ─────────────────────────────────────────────────────────────

def _compute_narrative_arc(articles: list[sqlite3.Row]) -> list[dict]:
    buckets: dict[str, dict] = defaultdict(lambda: {"bullish": 0, "bearish": 0, "neutral": 0, "article_count": 0})
    for a in articles:
        date = a["published_at"] or a["fetched_at"]
        if not date:
            continue
        month = date[:7]
        s = a["sentiment"] or "neutral"
        if s in ("bullish", "bearish", "neutral"):
            buckets[month][s] += 1
        else:
            buckets[month]["neutral"] += 1
        buckets[month]["article_count"] += 1
    return [
        {
            "month": m,
            "article_count": buckets[m]["article_count"],
            "sentiment": {"bullish": buckets[m]["bullish"], "bearish": buckets[m]["bearish"], "neutral": buckets[m]["neutral"]},
        }
        for m in sorted(buckets)
    ]


# ── Key authors ───────────────────────────────────────────────────────────────

def _compute_key_authors(conn: sqlite3.Connection, article_ids: list[int]) -> list[dict]:
    if not article_ids:
        return []
    ph = ",".join("?" * len(article_ids))
    rows = conn.execute(
        f"""SELECT
               json_extract(a.tweet_meta, '$.author_handle') AS handle,
               json_extract(a.tweet_meta, '$.author_name')   AS display_name,
               COUNT(*) AS article_count,
               SUM(CASE WHEN e.sentiment = 'bullish' THEN 1 ELSE 0 END) AS bullish_count,
               SUM(CASE WHEN e.sentiment = 'bearish' THEN 1 ELSE 0 END) AS bearish_count
            FROM articles a
            LEFT JOIN article_enrichments e ON e.article_id = a.id
            WHERE a.id IN ({ph}) AND a.tweet_meta IS NOT NULL
            GROUP BY handle
            HAVING handle IS NOT NULL
            ORDER BY article_count DESC
            LIMIT 10""",
        article_ids,
    ).fetchall()
    result = []
    for r in rows:
        bullish = r["bullish_count"] or 0
        bearish = r["bearish_count"] or 0
        avg_sentiment = "bullish" if bullish > bearish else ("bearish" if bearish > bullish else "neutral")
        result.append({
            "handle": r["handle"],
            "display_name": r["display_name"],
            "article_count": r["article_count"],
            "avg_sentiment": avg_sentiment,
        })
    return result


# ── Related entities ──────────────────────────────────────────────────────────

def _compute_related_entities(
    conn: sqlite3.Connection, canonical_id: int, article_ids: list[int]
) -> list[dict]:
    if not article_ids:
        return []
    self_ids = set(_get_all_entity_ids(conn, canonical_id))
    ph = ",".join("?" * len(article_ids))
    rows = conn.execute(
        f"""SELECT
               COALESCE(e.canonical_id, ae.entity_id) AS grouped_id,
               COALESCE(canon.name, e.name)           AS name,
               e.kind,
               COUNT(*) AS cooccurrence_count
            FROM article_entities ae
            JOIN entities e ON e.id = ae.entity_id
            LEFT JOIN entities canon ON canon.id = e.canonical_id
            WHERE ae.article_id IN ({ph})
            GROUP BY grouped_id
            ORDER BY cooccurrence_count DESC
            LIMIT 20""",
        article_ids,
    ).fetchall()
    return [
        {"name": r["name"], "kind": r["kind"], "cooccurrence_count": r["cooccurrence_count"]}
        for r in rows
        if r["grouped_id"] not in self_ids
    ][:15]


# ── Key articles ──────────────────────────────────────────────────────────────

def _article_dict(a: sqlite3.Row) -> dict:
    meta = None
    if a["tweet_meta"]:
        try:
            meta = json.loads(a["tweet_meta"]) if isinstance(a["tweet_meta"], str) else a["tweet_meta"]
        except (ValueError, TypeError):
            pass
    return {
        "id": a["id"],
        "url": a["url"],
        "title": a["title"],
        "published_at": a["published_at"],
        "summary": a["summary"],
        "sentiment": a["sentiment"],
        "author_handle": meta.get("author_handle") if meta else None,
        "favorite_count": int(meta.get("favorite_count", 0) or 0) if meta else 0,
    }


def _compute_key_articles(articles: list[sqlite3.Row], entity_name: str) -> dict:
    dicts = [_article_dict(a) for a in articles]
    most_engaged = sorted(dicts, key=lambda d: d["favorite_count"], reverse=True)[:5]
    most_recent = sorted(
        [d for d in dicts if d["published_at"]], key=lambda d: d["published_at"], reverse=True
    )[:5]

    entity_lower = entity_name.lower()
    most_quoted = []
    for a in articles:
        if not a["tweet_meta"]:
            continue
        try:
            meta = json.loads(a["tweet_meta"]) if isinstance(a["tweet_meta"], str) else a["tweet_meta"]
            qt = meta.get("quoted_tweet") or {}
            qt_text = (qt.get("full_text") or qt.get("text") or "").lower()
            if entity_lower in qt_text:
                most_quoted.append(_article_dict(a))
        except (ValueError, TypeError):
            pass

    return {"most_engaged": most_engaged, "most_recent": most_recent, "most_quoted": most_quoted[:5]}


# ── Claim buckets (K-means + LLM naming) ─────────────────────────────────────

def _name_cluster_with_llm(articles: list, ollama_url: str) -> str:
    summaries = "\n".join(
        f"- {a['summary'][:300]}" for a in articles if a.get("summary")
    )
    prompt = (
        "Based on these article summaries, give this cluster a short descriptive theme name "
        "(3-6 words). Return ONLY the theme name, nothing else.\n\n"
        f"{summaries}\n\nTheme:"
    )
    try:
        resp = httpx.post(
            f"{ollama_url}/api/generate",
            json={"model": _CLUSTER_MODEL, "prompt": prompt, "stream": False, "options": {"temperature": 0.3}},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json().get("response", "").strip().splitlines()[0][:100]
    except Exception:
        return f"Theme ({len(articles)} articles)"


def _compute_claim_buckets(
    articles: list[sqlite3.Row], ollama_url: str
) -> list[dict]:
    from sklearn.cluster import KMeans

    enriched = [a for a in articles if a["summary"]]
    if not enriched:
        return []

    n = len(enriched)
    k = max(1, min(8, int(math.sqrt(n))))

    vectors = []
    for a in enriched:
        try:
            vec = embed(a["summary"], ollama_url)
            vectors.append(vec)
        except Exception:
            vectors.append(None)

    valid_pairs = [(enriched[i], vectors[i]) for i in range(len(vectors)) if vectors[i] is not None]
    if not valid_pairs:
        return []

    valid_articles, valid_vectors = zip(*valid_pairs)
    X = np.array(valid_vectors)
    k = min(k, len(valid_articles))

    kmeans = KMeans(n_clusters=k, random_state=42, n_init=10)
    labels = kmeans.fit_predict(X)

    clusters: dict[int, list] = defaultdict(list)
    for i, label in enumerate(labels):
        clusters[label].append(valid_articles[i])

    buckets = []
    for label in sorted(clusters):
        cluster_arts = clusters[label]
        theme = _name_cluster_with_llm(cluster_arts[:5], ollama_url)
        art_dicts = [_article_dict(a) for a in cluster_arts]
        buckets.append({
            "theme": theme,
            "article_ids": [a["id"] for a in cluster_arts],
            "articles": art_dicts,
            "sample_quotes": [a["summary"][:200] for a in cluster_arts[:3] if a["summary"]],
        })
    return buckets


# ── Main entry point ──────────────────────────────────────────────────────────

def build_entity_dossier(
    conn: sqlite3.Connection,
    entity_name: str,
    ollama_url: str = _OLLAMA_URL,
    force_refresh: bool = False,
) -> dict | None:
    """Build (or return cached) entity research dossier.

    Returns None when the entity is not found in the entities table.
    """
    if not force_refresh:
        cached = get_dossier_cache(conn, entity_name)
        if cached:
            return json.loads(cached["body_json"])

    entity = _get_entity_canonical(conn, entity_name)
    if not entity:
        return None

    all_entity_ids = _get_all_entity_ids(conn, entity["id"])
    ph = ",".join("?" * len(all_entity_ids))
    article_id_rows = conn.execute(
        f"SELECT DISTINCT article_id FROM article_entities WHERE entity_id IN ({ph})",
        all_entity_ids,
    ).fetchall()
    article_ids = [r["article_id"] for r in article_id_rows]

    articles = _get_articles_with_enrichment(conn, article_ids)

    aliases = [
        r["name"]
        for r in conn.execute(
            "SELECT name FROM entities WHERE canonical_id = ?", (entity["id"],)
        ).fetchall()
    ]

    aid_list = [a["id"] for a in articles]

    try:
        claim_buckets = _compute_claim_buckets(articles, ollama_url)
    except Exception:
        claim_buckets = []

    dossier = {
        "entity": {
            "name": entity["name"],
            "kind": entity["kind"],
            "aliases": aliases,
            "canonical_id": entity["id"],
        },
        "stats": _compute_stats(articles),
        "narrative_arc": _compute_narrative_arc(articles),
        "key_authors": _compute_key_authors(conn, aid_list),
        "related_entities": _compute_related_entities(conn, entity["id"], aid_list),
        "key_articles": _compute_key_articles(articles, entity["name"]),
        "claim_buckets": claim_buckets,
    }

    save_dossier_cache(conn, entity_name, json.dumps(dossier))
    return dossier
