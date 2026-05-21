"""E5 — Materialised author profiles with engagement stats and influence score."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone


def _compute_influence_score(
    total_favorites: int,
    total_retweets: int,
    is_verified: bool,
    display_name: str | None,
    last_seen: str | None,
    article_count: int,
    max_engagement: float,
) -> float:
    """Compute 0-100 influence score."""
    # 40% engagement (normalised to archive max)
    eng = total_favorites + 5 * total_retweets
    eng_pct = min(1.0, eng / max_engagement) if max_engagement > 0 else 0.0
    eng_score = eng_pct * 40.0

    # 30% identity signals
    identity_score = (20.0 if is_verified else 0.0) + (10.0 if display_name else 0.0)

    # 20% recency
    recency_score = 0.0
    if last_seen:
        try:
            ts = datetime.fromisoformat(last_seen.rstrip("Z"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            days_ago = (datetime.now(timezone.utc) - ts).days
            if days_ago <= 7:
                recency_score = 20.0
            elif days_ago <= 30:
                recency_score = 14.0
            elif days_ago <= 90:
                recency_score = 8.0
            else:
                recency_score = 2.0
        except (ValueError, OverflowError):
            pass

    # 10% volume diversity
    diversity_score = min(10.0, article_count / 5.0)

    return round(eng_score + identity_score + recency_score + diversity_score, 2)


def rebuild_all_profiles(conn: sqlite3.Connection) -> int:
    """Recompute all author_profiles from scratch. Returns number of profiles written."""
    now = datetime.now(timezone.utc).isoformat()

    # Step 1: base stats per author
    rows = conn.execute("""
        SELECT
          json_extract(tweet_meta, '$.author_handle') AS handle,
          MAX(json_extract(tweet_meta, '$.author_name')) AS display_name,
          MAX(CAST(COALESCE(json_extract(tweet_meta, '$.is_verified'), 0) AS INTEGER)) AS is_verified,
          COUNT(*) AS article_count,
          SUM(COALESCE(CAST(json_extract(tweet_meta, '$.favorite_count') AS INTEGER), 0)) AS total_favorites,
          SUM(COALESCE(CAST(json_extract(tweet_meta, '$.retweet_count') AS INTEGER), 0)) AS total_retweets,
          SUM(COALESCE(CAST(json_extract(tweet_meta, '$.view_count') AS INTEGER), 0)) AS total_views,
          MIN(COALESCE(published_at, fetched_at)) AS first_seen,
          MAX(COALESCE(published_at, fetched_at)) AS last_seen
        FROM articles
        WHERE tweet_meta IS NOT NULL AND status = 'ok'
          AND json_extract(tweet_meta, '$.author_handle') IS NOT NULL
        GROUP BY handle
    """).fetchall()

    if not rows:
        return 0

    max_engagement = max(
        (r["total_favorites"] + 5 * r["total_retweets"]) for r in rows
    ) or 1.0

    # Step 2: per-author enrichment stats (categories, sentiment)
    def _author_enrichment(handle: str) -> tuple[list, dict]:
        cats_rows = conn.execute("""
            SELECT je.value AS cat, COUNT(*) AS cnt
            FROM articles a
            JOIN article_enrichments e ON e.article_id = a.id,
            json_each(e.categories) AS je
            WHERE json_extract(a.tweet_meta, '$.author_handle') = ?
              AND a.status = 'ok'
            GROUP BY cat ORDER BY cnt DESC LIMIT 3
        """, (handle,)).fetchall()
        categories = [r["cat"] for r in cats_rows]

        sent_row = conn.execute("""
            SELECT
              SUM(CASE WHEN e.sentiment='bullish' THEN 1 ELSE 0 END) AS bullish,
              SUM(CASE WHEN e.sentiment='bearish' THEN 1 ELSE 0 END) AS bearish,
              SUM(CASE WHEN e.sentiment='neutral' THEN 1 ELSE 0 END) AS neutral
            FROM articles a
            JOIN article_enrichments e ON e.article_id = a.id
            WHERE json_extract(a.tweet_meta, '$.author_handle') = ?
              AND a.status = 'ok'
        """, (handle,)).fetchone()
        sentiment = {
            "bullish": sent_row["bullish"] or 0,
            "bearish": sent_row["bearish"] or 0,
            "neutral": sent_row["neutral"] or 0,
        }
        return categories, sentiment

    def _author_hashtags(handle: str) -> list[str]:
        ht_rows = conn.execute("""
            SELECT je.value AS tag, COUNT(*) AS cnt
            FROM articles a, json_each(a.tweet_meta, '$.hashtags') AS je
            WHERE json_extract(a.tweet_meta, '$.author_handle') = ?
              AND a.tweet_meta IS NOT NULL AND a.status = 'ok'
            GROUP BY tag ORDER BY cnt DESC LIMIT 5
        """, (handle,)).fetchall()
        return [r["tag"] for r in ht_rows]

    # Step 3: write profiles
    conn.execute("DELETE FROM author_profiles")
    count = 0
    for r in rows:
        handle = r["handle"]
        cats, sentiment = _author_enrichment(handle)
        hashtags = _author_hashtags(handle)
        avg_eng = ((r["total_favorites"] + 5 * r["total_retweets"]) / r["article_count"]
                   if r["article_count"] else 0.0)
        score = _compute_influence_score(
            r["total_favorites"],
            r["total_retweets"],
            bool(r["is_verified"]),
            r["display_name"],
            r["last_seen"],
            r["article_count"],
            max_engagement,
        )
        conn.execute(
            """INSERT OR REPLACE INTO author_profiles
               (handle, display_name, is_verified, article_count, total_favorites,
                total_retweets, total_views, avg_engagement, primary_categories,
                primary_hashtags, sentiment_mix, influence_score, first_seen, last_seen, generated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                handle,
                r["display_name"],
                int(r["is_verified"] or 0),
                r["article_count"],
                r["total_favorites"],
                r["total_retweets"],
                r["total_views"],
                round(avg_eng, 2),
                json.dumps(cats),
                json.dumps(hashtags),
                json.dumps(sentiment),
                score,
                r["first_seen"],
                r["last_seen"],
                now,
            ),
        )
        count += 1
    conn.commit()
    return count


def get_author_profile(conn: sqlite3.Connection, handle: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM author_profiles WHERE handle=?", (handle.lower().lstrip("@"),)
    ).fetchone()
    if not row:
        return None
    d = dict(row)
    for key in ("primary_categories", "primary_hashtags", "sentiment_mix"):
        try:
            d[key] = json.loads(d[key]) if d[key] else ([] if key != "sentiment_mix" else {})
        except (ValueError, TypeError):
            d[key] = [] if key != "sentiment_mix" else {}
    return d
