"""Build and query the author-to-author interaction graph from tweet_meta."""
from __future__ import annotations

import json
import sqlite3
from collections import defaultdict

_SCHEMA = """
CREATE TABLE IF NOT EXISTS author_edges (
    src_author      TEXT NOT NULL,
    dst_author      TEXT NOT NULL,
    kind            TEXT NOT NULL,
    weight          INTEGER NOT NULL DEFAULT 0,
    first_observed  TEXT NOT NULL,
    last_observed   TEXT NOT NULL,
    PRIMARY KEY (src_author, dst_author, kind)
);
CREATE INDEX IF NOT EXISTS idx_author_edges_dst ON author_edges (dst_author);
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA)
    conn.commit()


def rebuild_author_edges(conn: sqlite3.Connection) -> int:
    """Recompute author_edges from scratch. Returns row count."""
    ensure_schema(conn)
    conn.execute("DELETE FROM author_edges")

    # {(src, dst, kind): {article_ids, first_observed, last_observed}}
    edges: dict[tuple[str, str, str], dict] = {}

    def _add(src: str, dst: str, kind: str, ts: str) -> None:
        if not src or not dst or src == dst:
            return
        key = (src.lower(), dst.lower(), kind)
        if key not in edges:
            edges[key] = {"count": 0, "first": ts, "last": ts}
        e = edges[key]
        e["count"] += 1
        if ts and (not e["first"] or ts < e["first"]):
            e["first"] = ts
        if ts and (not e["last"] or ts > e["last"]):
            e["last"] = ts

    # Mention edges: from tweet_meta.mentioned_handles
    rows = conn.execute(
        "SELECT tweet_meta, published_at, fetched_at FROM articles WHERE tweet_meta IS NOT NULL AND status='ok'"
    ).fetchall()
    for row in rows:
        try:
            meta = json.loads(row["tweet_meta"])
        except (ValueError, TypeError):
            continue
        src = (meta.get("author_handle") or "").lower()
        if not src:
            continue
        ts = row["published_at"] or row["fetched_at"] or ""
        for h in (meta.get("mentioned_handles") or []):
            _add(src, h.lower(), "mentions", ts)

    # Quote/reply edges from tweet_links joined to article authors
    link_rows = conn.execute(
        """SELECT tl.src_article_id, tl.dst_article_id, tl.kind
           FROM tweet_links tl
           WHERE tl.kind IN ('quoted', 'reply') AND tl.dst_article_id IS NOT NULL"""
    ).fetchall()
    for lr in link_rows:
        src_row = conn.execute(
            "SELECT tweet_meta, published_at, fetched_at FROM articles WHERE id=?", (lr["src_article_id"],)
        ).fetchone()
        dst_row = conn.execute(
            "SELECT tweet_meta FROM articles WHERE id=?", (lr["dst_article_id"],)
        ).fetchone()
        if not src_row or not dst_row:
            continue
        try:
            src_meta = json.loads(src_row["tweet_meta"]) if src_row["tweet_meta"] else {}
            dst_meta = json.loads(dst_row["tweet_meta"]) if dst_row["tweet_meta"] else {}
        except (ValueError, TypeError):
            continue
        src_h = (src_meta.get("author_handle") or "").lower()
        dst_h = (dst_meta.get("author_handle") or "").lower()
        if not src_h or not dst_h:
            continue
        kind = "quotes" if lr["kind"] == "quoted" else "replies"
        ts = src_row["published_at"] or src_row["fetched_at"] or ""
        _add(src_h, dst_h, kind, ts)

    conn.executemany(
        """INSERT OR REPLACE INTO author_edges (src_author, dst_author, kind, weight, first_observed, last_observed)
           VALUES (?, ?, ?, ?, ?, ?)""",
        [
            (k[0], k[1], k[2], v["count"], v["first"], v["last"])
            for k, v in edges.items()
        ],
    )
    conn.commit()
    return len(edges)


def _pagerank(
    edges: list[tuple[str, str, int]],
    damping: float = 0.85,
    iters: int = 50,
) -> dict[str, float]:
    """Simplified PageRank over weighted directed edges."""
    nodes: set[str] = set()
    for s, t, _ in edges:
        nodes.add(s); nodes.add(t)
    if not nodes:
        return {}
    n = len(nodes)
    pr = {node: 1.0 / n for node in nodes}
    out_weight: dict[str, float] = defaultdict(float)
    for s, _, w in edges:
        out_weight[s] += w

    for _ in range(iters):
        new_pr: dict[str, float] = {node: (1 - damping) / n for node in nodes}
        for s, t, w in edges:
            if out_weight[s]:
                new_pr[t] += damping * pr[s] * (w / out_weight[s])
        pr = new_pr

    total = sum(pr.values()) or 1.0
    return {k: v / total for k, v in pr.items()}


def get_author_influence(conn: sqlite3.Connection, handle: str) -> dict | None:
    """Return influence summary for a handle. None if handle not in edges at all."""
    h = handle.lower()

    # All edges involving this handle
    all_edges_rows = conn.execute(
        "SELECT src_author, dst_author, kind, weight FROM author_edges "
        "WHERE src_author = ? OR dst_author = ?",
        (h, h),
    ).fetchall()
    if not all_edges_rows:
        return None

    inbound = [r for r in all_edges_rows if r["dst_author"] == h]
    outbound = [r for r in all_edges_rows if r["src_author"] == h]

    top_in = sorted(inbound, key=lambda r: r["weight"], reverse=True)[:5]
    top_out = sorted(outbound, key=lambda r: r["weight"], reverse=True)[:5]

    # PageRank over the full graph
    all_rows = conn.execute(
        "SELECT src_author, dst_author, weight FROM author_edges"
    ).fetchall()
    pr = _pagerank([(r["src_author"], r["dst_author"], r["weight"]) for r in all_rows])
    all_handles_sorted = sorted(pr, key=pr.__getitem__, reverse=True)
    rank = next((i + 1 for i, hh in enumerate(all_handles_sorted) if hh == h), None)
    total_authors = len(all_handles_sorted)

    return {
        "handle": handle,
        "in_degree": len({r["src_author"] for r in inbound}),
        "out_degree": len({r["dst_author"] for r in outbound}),
        "top_inbound": [{"from": r["src_author"], "kind": r["kind"], "weight": r["weight"]} for r in top_in],
        "top_outbound": [{"to": r["dst_author"], "kind": r["kind"], "weight": r["weight"]} for r in top_out],
        "centrality_score": round(pr.get(h, 0.0), 6),
        "centrality_rank": rank,
        "total_authors": total_authors,
    }


def get_author_graph(
    conn: sqlite3.Connection,
    seed: str,
    depth: int = 2,
    kind: str = "all",
    max_nodes: int = 30,
) -> dict | None:
    """Return a node-link graph of author interactions seeded from one handle."""
    h = seed.lower()
    kind_filter = "" if kind == "all" else f"AND kind = '{kind}'"

    def neighbors(handle: str) -> list[tuple[str, str, int]]:
        rows = conn.execute(
            f"""SELECT src_author, dst_author, kind, weight FROM author_edges
                WHERE (src_author = ? OR dst_author = ?) {kind_filter}""",
            (handle.lower(), handle.lower()),
        ).fetchall()
        return [(r["src_author"], r["dst_author"], r["kind"], r["weight"]) for r in rows]

    # Check seed exists
    if not neighbors(h):
        return None

    # All edges for PR
    all_rows = conn.execute("SELECT src_author, dst_author, weight FROM author_edges").fetchall()
    pr = _pagerank([(r["src_author"], r["dst_author"], r["weight"]) for r in all_rows])

    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    seen_edges: set[tuple] = set()
    visited: set[str] = {h}
    frontier: list[str] = [h]
    nodes[h] = {"id": seed, "kind": "author", "size": pr.get(h, 0), "sentiment": "neutral"}

    for _ in range(depth):
        next_f: list[str] = []
        for fh in frontier:
            for src, dst, ek, ew in neighbors(fh):
                other = dst if src == fh else src
                edge_key = (min(src, dst), max(src, dst), ek)
                if edge_key not in seen_edges:
                    seen_edges.add(edge_key)
                    edges.append({"source": src, "target": dst, "weight": ew, "kind": ek})
                if other not in visited and len(nodes) < max_nodes:
                    visited.add(other)
                    nodes[other] = {
                        "id": other,
                        "kind": "author",
                        "size": pr.get(other, 0),
                        "sentiment": "neutral",
                    }
                    next_f.append(other)
        frontier = next_f

    return {"nodes": list(nodes.values()), "edges": edges, "seed": seed}
