"""R3 — Archive-wide topic clustering.

Pulls all Qdrant vectors, runs K-means, names clusters with Ollama,
and persists results to topics + topic_articles tables.
"""
from __future__ import annotations

import math
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import httpx
import numpy as np
from sklearn.cluster import KMeans

_CLUSTER_MODEL = "qwen2.5:3b-instruct"


# ── DB helpers ────────────────────────────────────────────────────────────────

def should_recluster(conn: sqlite3.Connection, max_age_days: int = 7, new_article_threshold: int = 50) -> bool:
    """Return True if clustering should run."""
    topic_count = conn.execute("SELECT COUNT(*) AS n FROM topics").fetchone()["n"]
    if topic_count == 0:
        return True

    last_gen = conn.execute("SELECT MAX(generated_at) AS t FROM topics").fetchone()["t"]
    if last_gen:
        ts = datetime.fromisoformat(last_gen)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) - ts > timedelta(days=max_age_days):
            return True

    clustered = conn.execute("SELECT COUNT(DISTINCT article_id) AS n FROM topic_articles").fetchone()["n"]
    total_ok = conn.execute("SELECT COUNT(*) AS n FROM articles WHERE status='ok'").fetchone()["n"]
    if total_ok - clustered >= new_article_threshold:
        return True

    return False


def get_topic_list(conn: sqlite3.Connection, order: str = "size") -> list[dict]:
    # Pinned topics float to the top regardless of order so the UI shows them
    # consistently and the user knows they survive reclustering.
    order_sql = "article_count DESC" if order == "size" else "generated_at DESC"
    try:
        rows = conn.execute(
            f"SELECT id, name, description, article_count, generated_at, "
            f"COALESCE(pinned, 0) AS pinned, custom_name "
            f"FROM topics ORDER BY pinned DESC, {order_sql}"
        ).fetchall()
    except sqlite3.OperationalError:
        # Migration hasn't run yet on a very old DB; degrade gracefully.
        rows = conn.execute(
            f"SELECT id, name, description, article_count, generated_at FROM topics ORDER BY {order_sql}"
        ).fetchall()
    return [dict(r) for r in rows]


def get_topic(conn: sqlite3.Connection, topic_id: int) -> dict | None:
    try:
        row = conn.execute(
            "SELECT id, name, description, article_count, generated_at, "
            "COALESCE(pinned, 0) AS pinned, custom_name FROM topics WHERE id=?",
            (topic_id,),
        ).fetchone()
    except sqlite3.OperationalError:
        row = conn.execute(
            "SELECT id, name, description, article_count, generated_at FROM topics WHERE id=?",
            (topic_id,),
        ).fetchone()
    if not row:
        return None
    result = dict(row)

    # Top hashtags in cluster
    hashtag_rows = conn.execute(
        """
        SELECT je.value AS tag, COUNT(*) AS cnt
        FROM topic_articles ta
        JOIN articles a ON a.id = ta.article_id,
        json_each(a.tweet_meta, '$.hashtags') AS je
        WHERE ta.topic_id=? AND a.tweet_meta IS NOT NULL
        GROUP BY tag ORDER BY cnt DESC LIMIT 5
        """,
        (topic_id,),
    ).fetchall()
    result["top_hashtags"] = [r["tag"] for r in hashtag_rows]

    # Entity distribution
    entity_rows = conn.execute(
        """
        SELECT e.name, e.kind, COUNT(*) AS cnt
        FROM topic_articles ta
        JOIN article_entities ae ON ae.article_id = ta.article_id
        JOIN entities e ON e.id = ae.entity_id
        WHERE ta.topic_id=?
        GROUP BY e.id ORDER BY cnt DESC LIMIT 10
        """,
        (topic_id,),
    ).fetchall()
    result["top_entities"] = [dict(r) for r in entity_rows]

    # Time-series of cluster activity (by month)
    arc_rows = conn.execute(
        """
        SELECT strftime('%Y-%m', COALESCE(a.published_at, a.fetched_at)) AS month,
               COUNT(*) AS count
        FROM topic_articles ta
        JOIN articles a ON a.id = ta.article_id
        WHERE ta.topic_id=? AND COALESCE(a.published_at, a.fetched_at) IS NOT NULL
        GROUP BY month ORDER BY month
        """,
        (topic_id,),
    ).fetchall()
    result["activity_arc"] = [dict(r) for r in arc_rows]

    return result


def get_topic_articles(
    conn: sqlite3.Connection,
    topic_id: int,
    page: int = 1,
    page_size: int = 20,
    order: str = "distance",
) -> tuple[int, list[sqlite3.Row]]:
    total = conn.execute(
        "SELECT COUNT(*) AS n FROM topic_articles WHERE topic_id=?", (topic_id,)
    ).fetchone()["n"]

    offset = (page - 1) * page_size
    if order == "distance":
        order_sql = "ta.distance_to_centroid ASC"
    elif order == "date":
        order_sql = "COALESCE(a.published_at, a.fetched_at) DESC"
    else:
        order_sql = "ta.distance_to_centroid ASC"

    rows = conn.execute(
        f"""
        SELECT a.*, ta.distance_to_centroid
        FROM topic_articles ta
        JOIN articles a ON a.id = ta.article_id
        WHERE ta.topic_id=?
        ORDER BY {order_sql}
        LIMIT ? OFFSET ?
        """,
        (topic_id, page_size, offset),
    ).fetchall()
    return total, rows


# ── Vector extraction ─────────────────────────────────────────────────────────

def get_all_vectors_from_qdrant(client) -> list[tuple[int, list[float]]]:
    """Return all (article_id, vector) pairs from Qdrant."""
    from .vector import COLLECTION
    results: list[tuple[int, list[float]]] = []
    offset = None
    while True:
        points, next_offset = client.scroll(
            COLLECTION,
            limit=1000,
            with_vectors=True,
            offset=offset,
        )
        for p in points:
            if p.vector:
                results.append((int(p.id), list(p.vector)))
        if next_offset is None:
            break
        offset = next_offset
    return results


# ── LLM cluster naming ────────────────────────────────────────────────────────

def _name_cluster(articles: list[dict], ollama_url: str) -> tuple[str, str]:
    """Return (name, description) for a cluster from top article summaries."""
    summaries = "\n".join(
        f"- {a.get('summary', '')[:300]}" for a in articles if a.get("summary")
    )
    if not summaries:
        return f"Cluster ({len(articles)} articles)", ""
    prompt = (
        "Based on these article summaries, give this cluster a short theme name (3-6 words) "
        "AND a one-sentence description. Return EXACTLY two lines: first the theme name, then the description.\n\n"
        f"{summaries}\n\nTheme name:"
    )
    try:
        resp = httpx.post(
            f"{ollama_url}/api/generate",
            json={"model": _CLUSTER_MODEL, "prompt": prompt, "stream": False, "options": {"temperature": 0.3}},
            timeout=30,
        )
        resp.raise_for_status()
        lines = [l.strip() for l in resp.json().get("response", "").strip().splitlines() if l.strip()]
        name = lines[0][:100] if lines else f"Topic ({len(articles)} articles)"
        description = lines[1][:300] if len(lines) > 1 else ""
        return name, description
    except Exception:
        return f"Topic ({len(articles)} articles)", ""


# ── Main clustering entry point ───────────────────────────────────────────────

def run_clustering(
    conn: sqlite3.Connection,
    vectors: list[tuple[int, list[float]]],
    ollama_url: str,
    name_fn=None,
) -> dict:
    """Run K-means on vectors, persist results, return stats dict.

    `name_fn` is injectable for tests: callable(articles, ollama_url) -> (name, description).
    """
    if len(vectors) < 10:
        return {"topics": 0, "articles": 0, "skipped": True, "reason": "not enough vectors"}

    article_ids = [v[0] for v in vectors]
    X = np.array([v[1] for v in vectors], dtype=np.float32)

    n = len(vectors)
    k = max(8, int(math.sqrt(n) * 0.6))
    k = min(k, n)

    kmeans = KMeans(n_clusters=k, random_state=42, n_init=10)
    labels = kmeans.fit_predict(X)
    centroids = kmeans.cluster_centers_

    clusters: dict[int, list[tuple[int, float]]] = defaultdict(list)
    for i, label in enumerate(labels):
        dist = float(np.linalg.norm(X[i] - centroids[label]))
        clusters[label].append((article_ids[i], dist))

    for label in clusters:
        clusters[label].sort(key=lambda x: x[1])

    _name_fn = name_fn if name_fn is not None else _name_cluster
    now = datetime.now(timezone.utc).isoformat()

    # Stage the cluster results first (in memory). Don't touch the live topics
    # table until we have evidence the run produced meaningful output. This
    # prevents a degraded clustering run (e.g., ollama mid-warmup → every
    # cluster gets the fallback "Topic (N articles)" name) from wiping out a
    # previously-good set of topics. Bug #42.
    staged: list[tuple[str, str, int, list[tuple[int, float]]]] = []
    for label in sorted(clusters):
        cluster_pairs = clusters[label]
        top_ids = [aid for aid, _ in cluster_pairs[:5]]

        if top_ids:
            ph = ",".join("?" * len(top_ids))
            art_rows = conn.execute(
                f"SELECT id, title, summary FROM articles a "
                f"LEFT JOIN article_enrichments e ON e.article_id = a.id "
                f"WHERE a.id IN ({ph})",
                top_ids,
            ).fetchall()
            articles = [dict(r) for r in art_rows]
        else:
            articles = []

        name, description = _name_fn(articles, ollama_url)
        staged.append((name, description, len(cluster_pairs), cluster_pairs))

    # Quality gate: count clusters with a non-fallback name (the LLM produced a
    # real theme). If fewer than half the clusters got named, treat the whole
    # run as degraded and KEEP the existing topics rather than swapping.
    def _is_fallback(name: str) -> bool:
        n = (name or "").strip().lower()
        return n.startswith("topic (") or n.startswith("cluster (")

    real_named = sum(1 for s in staged if not _is_fallback(s[0]))
    existing_topics = conn.execute("SELECT COUNT(*) FROM topics").fetchone()[0]
    if existing_topics > 0 and real_named < max(2, len(staged) // 2):
        # Degraded run — leave the existing good topics in place.
        return {
            "topics": existing_topics,
            "articles": conn.execute("SELECT COUNT(*) FROM topic_articles").fetchone()[0],
            "skipped": True,
            "reason": (
                f"degraded run: {real_named}/{len(staged)} clusters got real names; "
                f"keeping existing {existing_topics} topics"
            ),
        }

    # Capture user-pinned topics + their custom_name overrides BEFORE we wipe.
    # The recluster process keeps them in place: their article_count gets
    # recomputed from any of their existing articles that are still in the new
    # clustering's article set, but otherwise they survive the swap untouched.
    try:
        pinned_rows = conn.execute(
            "SELECT id, name, description, article_count, custom_name "
            "FROM topics WHERE pinned = 1"
        ).fetchall()
    except sqlite3.OperationalError:
        # `pinned` column hasn't migrated yet on a very old DB.
        pinned_rows = []
    pinned_existing = [dict(r) for r in pinned_rows]
    pinned_article_ids: dict[int, list[tuple[int, float]]] = {}
    if pinned_existing:
        for p in pinned_existing:
            rows = conn.execute(
                "SELECT article_id, distance_to_centroid FROM topic_articles WHERE topic_id = ?",
                (p["id"],),
            ).fetchall()
            pinned_article_ids[p["id"]] = [(r["article_id"], r["distance_to_centroid"]) for r in rows]

    # Commit: atomic swap of the live topics under a transaction.
    conn.execute("BEGIN")
    try:
        conn.execute("DELETE FROM topic_articles")
        conn.execute("DELETE FROM topics")
        inserted_topics = 0
        inserted_articles = 0

        # Re-insert pinned topics first so their ids are stable (low ids = old).
        # Their generated_at stays at "now" so the UI shows the recluster touched
        # them, but custom_name overrides the LLM-generated name.
        for p in pinned_existing:
            effective_name = (p.get("custom_name") or p["name"] or "").strip() or "Pinned"
            cur = conn.execute(
                "INSERT INTO topics (name, description, article_count, generated_at, pinned, custom_name)"
                " VALUES (?, ?, ?, ?, 1, ?)",
                (effective_name, p.get("description") or "", p.get("article_count") or 0, now, p.get("custom_name")),
            )
            new_topic_id = cur.lastrowid
            inserted_topics += 1
            arts = pinned_article_ids.get(p["id"], [])
            if arts:
                conn.executemany(
                    "INSERT INTO topic_articles (topic_id, article_id, distance_to_centroid) VALUES (?, ?, ?)",
                    [(new_topic_id, aid, dist) for aid, dist in arts],
                )
                inserted_articles += len(arts)

        for name, description, count, cluster_pairs in staged:
            cur = conn.execute(
                "INSERT INTO topics (name, description, article_count, generated_at) VALUES (?, ?, ?, ?)",
                (name, description, count, now),
            )
            topic_id = cur.lastrowid
            inserted_topics += 1
            conn.executemany(
                "INSERT INTO topic_articles (topic_id, article_id, distance_to_centroid) VALUES (?, ?, ?)",
                [(topic_id, aid, dist) for aid, dist in cluster_pairs],
            )
            inserted_articles += len(cluster_pairs)
        conn.commit()
    except Exception:
        conn.execute("ROLLBACK")
        raise

    return {"topics": inserted_topics, "articles": inserted_articles, "skipped": False}
