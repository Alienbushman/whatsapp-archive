import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS articles (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    url           TEXT UNIQUE NOT NULL,
    status        TEXT NOT NULL DEFAULT 'pending',
    title         TEXT,
    author        TEXT,
    published_at  TEXT,
    raw_text      TEXT,
    og_image      TEXT,
    scrape_method TEXT,
    error         TEXT,
    fetched_at    TEXT
);
CREATE TABLE IF NOT EXISTS article_enrichments (
    article_id  INTEGER PRIMARY KEY REFERENCES articles(id),
    summary     TEXT,
    categories  TEXT,
    entities    TEXT,
    sentiment   TEXT,
    model       TEXT,
    enriched_at TEXT,
    status      TEXT DEFAULT 'ok'
);
"""


_FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS articles_fts
    USING fts5(title, raw_text, tweet_search_blob, content=articles, content_rowid=id);
"""

_OCR_SCHEMA = """
CREATE TABLE IF NOT EXISTS tweet_media_ocr (
    article_id   INTEGER NOT NULL,
    media_url    TEXT NOT NULL,
    ocr_text     TEXT,
    ocr_lang     TEXT,
    extracted_at TEXT,
    PRIMARY KEY (article_id, media_url),
    FOREIGN KEY (article_id) REFERENCES articles(id)
);
"""

_DIGESTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS digests (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    period       TEXT NOT NULL,
    period_start TEXT NOT NULL,
    period_end   TEXT NOT NULL,
    chat_id      TEXT,
    body_md      TEXT NOT NULL,
    citations    TEXT NOT NULL,
    model        TEXT NOT NULL,
    generated_at TEXT NOT NULL,
    UNIQUE (period, period_start, chat_id)
);
"""

_QUOTES_SCHEMA = """
CREATE TABLE IF NOT EXISTS ticker_quotes (
    symbol       TEXT NOT NULL PRIMARY KEY,
    fetched_at   TEXT NOT NULL,
    last_price   REAL,
    change_pct   REAL,
    currency     TEXT,
    history_json TEXT
);
"""

_ENTITIES_SCHEMA = """
CREATE TABLE IF NOT EXISTS entities (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    kind            TEXT NOT NULL DEFAULT 'other',
    normalized_name TEXT NOT NULL,
    canonical_id    INTEGER REFERENCES entities(id),
    merged_at       TEXT,
    UNIQUE (normalized_name, kind)
);
CREATE TABLE IF NOT EXISTS article_entities (
    article_id   INTEGER NOT NULL REFERENCES articles(id),
    entity_id    INTEGER NOT NULL REFERENCES entities(id),
    mention_text TEXT,
    assigned_by  TEXT NOT NULL DEFAULT 'llm',
    PRIMARY KEY (article_id, entity_id)
);
"""

_DOSSIERS_SCHEMA = """
CREATE TABLE IF NOT EXISTS entity_dossiers (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_name  TEXT NOT NULL UNIQUE,
    body_json    TEXT NOT NULL,
    generated_at TEXT NOT NULL
);
"""

_DEEPDIVE_SCHEMA = """
CREATE TABLE IF NOT EXISTS deepdive_reports (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_name  TEXT NOT NULL,
    body_json    TEXT NOT NULL,
    model        TEXT NOT NULL,
    generated_at TEXT NOT NULL,
    UNIQUE (entity_name)
);
"""

_TOPICS_SCHEMA = """
CREATE TABLE IF NOT EXISTS topics (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    description   TEXT,
    article_count INTEGER NOT NULL DEFAULT 0,
    generated_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS topic_articles (
    topic_id             INTEGER NOT NULL REFERENCES topics(id),
    article_id           INTEGER NOT NULL REFERENCES articles(id),
    distance_to_centroid REAL,
    PRIMARY KEY (topic_id, article_id)
);
"""

_AUTHOR_PROFILES_SCHEMA = """
CREATE TABLE IF NOT EXISTS author_profiles (
    handle             TEXT PRIMARY KEY,
    display_name       TEXT,
    is_verified        INTEGER NOT NULL DEFAULT 0,
    article_count      INTEGER NOT NULL DEFAULT 0,
    total_favorites    INTEGER NOT NULL DEFAULT 0,
    total_retweets     INTEGER NOT NULL DEFAULT 0,
    total_views        INTEGER NOT NULL DEFAULT 0,
    avg_engagement     REAL,
    primary_categories TEXT,
    primary_hashtags   TEXT,
    sentiment_mix      TEXT,
    influence_score    REAL NOT NULL DEFAULT 0,
    first_seen         TEXT,
    last_seen          TEXT,
    generated_at       TEXT NOT NULL
);
"""

_TWEET_LINKS_SCHEMA = """
CREATE TABLE IF NOT EXISTS tweet_links (
    src_article_id INTEGER NOT NULL,
    dst_tweet_id   TEXT NOT NULL,
    dst_article_id INTEGER,
    kind           TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    PRIMARY KEY (src_article_id, dst_tweet_id, kind)
);
CREATE INDEX IF NOT EXISTS idx_tweet_links_dst_tweet ON tweet_links (dst_tweet_id);
CREATE INDEX IF NOT EXISTS idx_tweet_links_dst_article ON tweet_links (dst_article_id);
"""

_AUTHOR_EDGES_SCHEMA = """
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

_BOOKMARKS_SCHEMA = """
CREATE TABLE IF NOT EXISTS bookmarks (
    article_id INTEGER PRIMARY KEY REFERENCES articles(id),
    starred    INTEGER NOT NULL DEFAULT 1,
    note       TEXT,
    added_at   TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS message_bookmarks (
    msg_key    TEXT PRIMARY KEY,
    chat_id    TEXT NOT NULL,
    sender     TEXT NOT NULL,
    ts         TEXT NOT NULL,
    body       TEXT NOT NULL,
    note       TEXT,
    added_at   TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS collections (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT UNIQUE NOT NULL,
    description TEXT,
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS collection_items (
    collection_id INTEGER NOT NULL REFERENCES collections(id),
    article_id    INTEGER NOT NULL REFERENCES articles(id),
    added_at      TEXT NOT NULL,
    PRIMARY KEY (collection_id, article_id)
);
CREATE TABLE IF NOT EXISTS research_bins (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    name                  TEXT NOT NULL,
    hypothesis            TEXT,
    seed_query            TEXT,
    seed_filters          TEXT,
    created_at            TEXT NOT NULL,
    last_accessed_at      TEXT NOT NULL,
    expires_at            TEXT NOT NULL,
    promoted_collection_id INTEGER REFERENCES collections(id)
);
CREATE TABLE IF NOT EXISTS research_bin_items (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    bin_id      INTEGER NOT NULL REFERENCES research_bins(id) ON DELETE CASCADE,
    target_kind TEXT NOT NULL CHECK(target_kind IN ('article', 'message')),
    target_id   TEXT NOT NULL,
    pinned_at   TEXT NOT NULL,
    note        TEXT,
    UNIQUE(bin_id, target_kind, target_id)
);
"""


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, decl: str) -> None:
    """Idempotent ALTER TABLE ADD COLUMN — swallow 'duplicate column' on re-run."""
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
        conn.commit()
    except sqlite3.OperationalError as exc:
        if "duplicate column" not in str(exc).lower():
            raise


def _ensure_fts_has_tweet_blob(conn: sqlite3.Connection) -> None:
    """Drop articles_fts if it was created without tweet_search_blob so the new
    _FTS_SCHEMA (with the third column) can take its place on the next CREATE."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='articles_fts'"
    ).fetchone()
    if row and "tweet_search_blob" not in (row["sql"] or ""):
        conn.executescript("DROP TABLE IF EXISTS articles_fts;")


def compute_tweet_search_blob(tweet_meta: dict, ocr_texts: list[str] | None = None) -> str:
    """Return a space-joined searchable string from a tweet_meta dict."""
    parts: list[str] = []
    if tweet_meta.get("author_name"):
        parts.append(tweet_meta["author_name"])
    if tweet_meta.get("author_handle"):
        parts.append("@" + tweet_meta["author_handle"])
    for h in tweet_meta.get("hashtags") or []:
        parts.append("#" + h)
    for m in tweet_meta.get("mentioned_handles") or []:
        parts.append("@" + m)
    for eu in tweet_meta.get("embedded_urls") or []:
        if eu.get("expanded_url"):
            parts.append(eu["expanded_url"])
    for t in tweet_meta.get("tickers") or []:
        parts.append("$" + t)
    if tweet_meta.get("in_reply_to_screen_name"):
        parts.append("@" + tweet_meta["in_reply_to_screen_name"])
    if ocr_texts:
        for text in ocr_texts:
            if text and text.strip():
                parts.append(text.strip())
    return " ".join(parts)


def open_db(archive_dir: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(archive_dir / "articles.db", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    _ensure_fts_has_tweet_blob(conn)   # drop old FTS if schema is stale
    conn.executescript(_FTS_SCHEMA)
    conn.executescript(_OCR_SCHEMA)
    conn.executescript(_DIGESTS_SCHEMA)
    conn.executescript(_QUOTES_SCHEMA)
    conn.executescript(_ENTITIES_SCHEMA)
    conn.executescript(_DOSSIERS_SCHEMA)
    conn.executescript(_DEEPDIVE_SCHEMA)
    conn.executescript(_TOPICS_SCHEMA)
    conn.executescript(_TWEET_LINKS_SCHEMA)
    conn.executescript(_AUTHOR_PROFILES_SCHEMA)
    conn.executescript(_AUTHOR_EDGES_SCHEMA)
    conn.executescript(_BOOKMARKS_SCHEMA)
    # Migrations: additive columns added after the initial _SCHEMA shipped.
    _ensure_column(conn, "articles", "tweet_meta", "TEXT")
    _ensure_column(conn, "articles", "tweet_search_blob", "TEXT")
    # Topics: allow user-pinned + custom-named clusters that survive reclustering.
    _ensure_column(conn, "topics", "pinned", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "topics", "custom_name", "TEXT")
    conn.execute("INSERT INTO articles_fts(articles_fts) VALUES('rebuild')")
    conn.commit()
    return conn


def upsert_article(conn: sqlite3.Connection, url: str, status: str, **fields) -> int:
    # Auto-derive tweet_search_blob from tweet_meta when not explicitly provided
    if "tweet_meta" in fields and fields["tweet_meta"] and "tweet_search_blob" not in fields:
        import json as _json
        try:
            meta = fields["tweet_meta"]
            if isinstance(meta, str):
                meta = _json.loads(meta)
            fields["tweet_search_blob"] = compute_tweet_search_blob(meta)
        except (ValueError, TypeError, AttributeError):
            pass

    # Snapshot old FTS-relevant values BEFORE the upsert so we can do an
    # incremental delete+insert on the content-FTS table (avoids stale entries
    # for articles that have been re-scraped or updated).
    _old = conn.execute(
        "SELECT id, title, raw_text, tweet_search_blob FROM articles WHERE url=?", (url,)
    ).fetchone()

    cols = ["url", "status"] + list(fields.keys())
    placeholders = ", ".join(["?"] * len(cols))
    updates = ", ".join(f"{c}=excluded.{c}" for c in cols if c != "url")
    sql = (
        f"INSERT INTO articles ({', '.join(cols)}) VALUES ({placeholders}) "
        f"ON CONFLICT(url) DO UPDATE SET {updates}"
    )
    cur = conn.execute(sql, [url, status] + list(fields.values()))
    article_id = cur.lastrowid or conn.execute("SELECT id FROM articles WHERE url=?", (url,)).fetchone()[0]

    # Incremental FTS sync: remove old index entry then add new one.
    # Content-FTS tables require explicit maintenance; the full rebuild at
    # open_db() only covers articles present at startup.
    if _old:
        conn.execute(
            "INSERT INTO articles_fts(articles_fts, rowid, title, raw_text, tweet_search_blob)"
            " VALUES('delete', ?, ?, ?, ?)",
            (_old["id"], _old["title"] or "", _old["raw_text"] or "", _old["tweet_search_blob"] or ""),
        )
    conn.execute(
        "INSERT INTO articles_fts(rowid, title, raw_text, tweet_search_blob) VALUES(?, ?, ?, ?)",
        (
            article_id,
            fields.get("title") or "",
            fields.get("raw_text") or "",
            fields.get("tweet_search_blob") or "",
        ),
    )
    conn.commit()
    return article_id


def get_article_by_id(conn: sqlite3.Connection, article_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM articles WHERE id=?", (article_id,)).fetchone()


def get_article_by_url(conn: sqlite3.Connection, url: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM articles WHERE url=?", (url,)).fetchone()


def upsert_enrichment(
    conn: sqlite3.Connection,
    article_id: int,
    summary: str,
    categories: str,
    entities: str,
    sentiment: str,
    model: str,
    enriched_at: str,
) -> None:
    conn.execute(
        """
        INSERT INTO article_enrichments
            (article_id, summary, categories, entities, sentiment, model, enriched_at, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'ok')
        ON CONFLICT(article_id) DO UPDATE SET
            summary=excluded.summary, categories=excluded.categories,
            entities=excluded.entities, sentiment=excluded.sentiment,
            model=excluded.model, enriched_at=excluded.enriched_at, status='ok'
        """,
        (article_id, summary, categories, entities, sentiment, model, enriched_at),
    )
    conn.commit()


def get_enrichment(conn: sqlite3.Connection, article_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM article_enrichments WHERE article_id=?", (article_id,)
    ).fetchone()


def get_articles_with_entities(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT a.id, a.url, a.title, a.published_at, e.entities "
        "FROM articles a JOIN article_enrichments e ON e.article_id = a.id "
        "WHERE a.status='ok' AND e.entities IS NOT NULL"
    ).fetchall()


def keyword_search(conn: sqlite3.Connection, query: str, limit: int = 20) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT a.*, bm25(articles_fts) AS score "
        "FROM articles_fts "
        "JOIN articles a ON a.id = articles_fts.rowid "
        "WHERE articles_fts MATCH ? "
        "ORDER BY score LIMIT ?",
        (query, limit),
    ).fetchall()


def keyword_search_with_snippet(conn: sqlite3.Connection, query: str, limit: int = 20) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT a.*, bm25(articles_fts) AS score, "
        "snippet(articles_fts, 0, '<mark>', '</mark>', '…', 20) AS title_snippet, "
        "snippet(articles_fts, 1, '<mark>', '</mark>', '…', 30) AS body_snippet, "
        "snippet(articles_fts, 2, '<mark>', '</mark>', '…', 30) AS meta_snippet "
        "FROM articles_fts "
        "JOIN articles a ON a.id = articles_fts.rowid "
        "WHERE articles_fts MATCH ? "
        "ORDER BY score LIMIT ?",
        (query, limit),
    ).fetchall()


def search_tweet_meta_blob(conn: sqlite3.Connection, query: str, limit: int = 20) -> list[sqlite3.Row]:
    """LIKE scan on tweet_search_blob — used for #hashtag and @mention queries that
    FTS5 rejects as syntax errors."""
    like = f"%{query}%"
    return conn.execute(
        "SELECT a.* FROM articles a "
        "WHERE a.tweet_search_blob LIKE ? AND a.status='ok' LIMIT ?",
        (like, limit),
    ).fetchall()


def list_authors(
    conn: sqlite3.Connection, limit: int = 50, offset: int = 0
) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT
          json_extract(tweet_meta, '$.author_handle') AS handle,
          json_extract(tweet_meta, '$.author_name')   AS display_name,
          json_extract(tweet_meta, '$.is_verified')   AS is_verified,
          COUNT(*) AS tweet_count,
          SUM(COALESCE(CAST(json_extract(tweet_meta, '$.favorite_count') AS INTEGER), 0)) AS total_favorites,
          SUM(COALESCE(CAST(json_extract(tweet_meta, '$.retweet_count')  AS INTEGER), 0)) AS total_retweets,
          SUM(COALESCE(CAST(json_extract(tweet_meta, '$.view_count')     AS INTEGER), 0)) AS total_views,
          MIN(fetched_at) AS first_seen,
          MAX(fetched_at) AS last_seen
        FROM articles
        WHERE tweet_meta IS NOT NULL
          AND json_extract(tweet_meta, '$.author_handle') IS NOT NULL
          AND status = 'ok'
        GROUP BY handle
        ORDER BY tweet_count DESC
        LIMIT ? OFFSET ?
        """,
        (limit, offset),
    ).fetchall()


def get_author_tweet_metas(conn: sqlite3.Connection, handle: str) -> list[dict]:
    """Return parsed tweet_meta dicts for all tweets by handle (for in-Python aggregation)."""
    import json as _json
    rows = conn.execute(
        """
        SELECT tweet_meta, fetched_at FROM articles
        WHERE json_extract(tweet_meta, '$.author_handle') = ? AND status = 'ok'
        """,
        (handle,),
    ).fetchall()
    result = []
    for row in rows:
        try:
            meta = _json.loads(row["tweet_meta"])
            meta["_fetched_at"] = row["fetched_at"]
            result.append(meta)
        except (ValueError, TypeError):
            pass
    return result


def get_tweets_by_author(
    conn: sqlite3.Connection,
    handle: str,
    page: int = 1,
    page_size: int = 50,
    order: str = "date",
) -> tuple[int, list[sqlite3.Row]]:
    order_sql = {
        "likes": "COALESCE(CAST(json_extract(tweet_meta, '$.favorite_count') AS INTEGER), 0) DESC",
        "retweets": "COALESCE(CAST(json_extract(tweet_meta, '$.retweet_count') AS INTEGER), 0) DESC",
    }.get(order, "fetched_at DESC")

    total = conn.execute(
        "SELECT COUNT(*) FROM articles WHERE json_extract(tweet_meta, '$.author_handle') = ? AND status = 'ok'",
        (handle,),
    ).fetchone()[0]

    offset = (page - 1) * page_size
    rows = conn.execute(
        f"""
        SELECT * FROM articles
        WHERE json_extract(tweet_meta, '$.author_handle') = ?
          AND status = 'ok'
        ORDER BY {order_sql}
        LIMIT ? OFFSET ?
        """,
        (handle, page_size, offset),
    ).fetchall()
    return total, rows


def list_hashtags(conn: sqlite3.Connection, limit: int = 50) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT je.value AS tag, COUNT(*) AS count,
               MIN(a.fetched_at) AS first_seen, MAX(a.fetched_at) AS last_seen
        FROM articles a, json_each(a.tweet_meta, '$.hashtags') AS je
        WHERE a.tweet_meta IS NOT NULL AND a.status = 'ok'
        GROUP BY tag
        ORDER BY count DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()


def get_tweets_by_hashtag(
    conn: sqlite3.Connection,
    tag: str,
    page: int = 1,
    page_size: int = 50,
    order: str = "date",
) -> tuple[int, list[sqlite3.Row]]:
    order_sql = {
        "likes": "COALESCE(CAST(json_extract(a.tweet_meta, '$.favorite_count') AS INTEGER), 0) DESC",
        "retweets": "COALESCE(CAST(json_extract(a.tweet_meta, '$.retweet_count') AS INTEGER), 0) DESC",
    }.get(order, "a.fetched_at DESC")

    total = conn.execute(
        """
        SELECT COUNT(DISTINCT a.id) FROM articles a, json_each(a.tweet_meta, '$.hashtags') AS je
        WHERE je.value = ? AND a.status = 'ok'
        """,
        (tag,),
    ).fetchone()[0]

    offset = (page - 1) * page_size
    rows = conn.execute(
        f"""
        SELECT DISTINCT a.* FROM articles a, json_each(a.tweet_meta, '$.hashtags') AS je
        WHERE je.value = ? AND a.status = 'ok'
        ORDER BY {order_sql}
        LIMIT ? OFFSET ?
        """,
        (tag, page_size, offset),
    ).fetchall()
    return total, rows


def list_mentions(conn: sqlite3.Connection, limit: int = 50) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT je.value AS handle, COUNT(*) AS count,
               MIN(a.fetched_at) AS first_seen, MAX(a.fetched_at) AS last_seen
        FROM articles a, json_each(a.tweet_meta, '$.mentioned_handles') AS je
        WHERE a.tweet_meta IS NOT NULL AND a.status = 'ok'
        GROUP BY handle
        ORDER BY count DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()


def get_tweets_by_mention(
    conn: sqlite3.Connection,
    handle: str,
    page: int = 1,
    page_size: int = 50,
    order: str = "date",
) -> tuple[int, list[sqlite3.Row]]:
    order_sql = {
        "likes": "COALESCE(CAST(json_extract(a.tweet_meta, '$.favorite_count') AS INTEGER), 0) DESC",
        "retweets": "COALESCE(CAST(json_extract(a.tweet_meta, '$.retweet_count') AS INTEGER), 0) DESC",
    }.get(order, "a.fetched_at DESC")

    total = conn.execute(
        """
        SELECT COUNT(DISTINCT a.id) FROM articles a, json_each(a.tweet_meta, '$.mentioned_handles') AS je
        WHERE je.value = ? AND a.status = 'ok'
        """,
        (handle,),
    ).fetchone()[0]

    offset = (page - 1) * page_size
    rows = conn.execute(
        f"""
        SELECT DISTINCT a.* FROM articles a, json_each(a.tweet_meta, '$.mentioned_handles') AS je
        WHERE je.value = ? AND a.status = 'ok'
        ORDER BY {order_sql}
        LIMIT ? OFFSET ?
        """,
        (handle, page_size, offset),
    ).fetchall()
    return total, rows


def search_enrichment_summary(conn: sqlite3.Connection, query: str, limit: int = 20) -> list[sqlite3.Row]:
    like = f"%{query}%"
    return conn.execute(
        "SELECT a.*, e.summary "
        "FROM articles a "
        "JOIN article_enrichments e ON e.article_id = a.id "
        "WHERE e.summary LIKE ? AND a.status='ok' "
        "LIMIT ?",
        (like, limit),
    ).fetchall()


def get_articles_by_urls(conn: sqlite3.Connection, urls: list[str]) -> dict[str, sqlite3.Row]:
    if not urls:
        return {}
    placeholders = ",".join("?" * len(urls))
    rows = conn.execute(
        f"SELECT * FROM articles WHERE url IN ({placeholders})", urls
    ).fetchall()
    return {row["url"]: row for row in rows}


def list_top_authors_windowed(
    conn: sqlite3.Connection, limit: int = 20, since: str | None = None, weighted: bool = False
) -> list[sqlite3.Row]:
    params: list = []
    date_filter = ""
    if since:
        date_filter = "AND COALESCE(published_at, fetched_at) >= ?"
        params.append(since)
    order_col = "(total_favorites + 5 * total_retweets)" if weighted else "tweet_count"
    return conn.execute(
        f"""
        SELECT
          json_extract(tweet_meta, '$.author_handle') AS handle,
          json_extract(tweet_meta, '$.author_name') AS display_name,
          json_extract(tweet_meta, '$.is_verified') AS is_verified,
          COUNT(*) AS tweet_count,
          SUM(COALESCE(CAST(json_extract(tweet_meta, '$.favorite_count') AS INTEGER), 0)) AS total_favorites,
          SUM(COALESCE(CAST(json_extract(tweet_meta, '$.retweet_count') AS INTEGER), 0)) AS total_retweets,
          SUM(COALESCE(CAST(json_extract(tweet_meta, '$.view_count') AS INTEGER), 0)) AS total_views
        FROM articles
        WHERE tweet_meta IS NOT NULL
          AND json_extract(tweet_meta, '$.author_handle') IS NOT NULL
          AND status = 'ok'
          {date_filter}
        GROUP BY handle
        ORDER BY {order_col} DESC
        LIMIT ?
        """,
        params + [limit],
    ).fetchall()


def list_top_hashtags_windowed(
    conn: sqlite3.Connection, limit: int = 20, since: str | None = None, weighted: bool = False
) -> list[sqlite3.Row]:
    params: list = []
    date_filter = ""
    if since:
        date_filter = "AND COALESCE(a.published_at, a.fetched_at) >= ?"
        params.append(since)
    order_col = (
        "SUM(COALESCE(CAST(json_extract(a.tweet_meta,'$.favorite_count') AS INTEGER),0)"
        " + 5*COALESCE(CAST(json_extract(a.tweet_meta,'$.retweet_count') AS INTEGER),0))"
    ) if weighted else "COUNT(*)"
    return conn.execute(
        f"""
        SELECT je.value AS tag, COUNT(*) AS count,
               SUM(COALESCE(CAST(json_extract(a.tweet_meta,'$.favorite_count') AS INTEGER),0)
                   + COALESCE(CAST(json_extract(a.tweet_meta,'$.retweet_count') AS INTEGER),0)) AS total_engagement
        FROM articles a, json_each(a.tweet_meta, '$.hashtags') AS je
        WHERE a.tweet_meta IS NOT NULL AND a.status = 'ok'
          {date_filter}
        GROUP BY tag
        ORDER BY {order_col} DESC
        LIMIT ?
        """,
        params + [limit],
    ).fetchall()


def list_top_entities_windowed(
    conn: sqlite3.Connection, limit: int = 20, since: str | None = None
) -> list[sqlite3.Row]:
    params: list = []
    date_filter = ""
    if since:
        date_filter = "AND COALESCE(a.published_at, a.fetched_at) >= ?"
        params.append(since)
    return conn.execute(
        f"""
        SELECT
          json_extract(je.value, '$.name') AS entity_name,
          json_extract(je.value, '$.kind') AS entity_kind,
          COUNT(*) AS count
        FROM article_enrichments e
        JOIN articles a ON a.id = e.article_id,
        json_each(e.entities) AS je
        WHERE e.entities IS NOT NULL AND a.status = 'ok'
          {date_filter}
        GROUP BY entity_name, entity_kind
        ORDER BY count DESC
        LIMIT ?
        """,
        params + [limit],
    ).fetchall()


def list_top_tweets_windowed(
    conn: sqlite3.Connection,
    by: str = "favorite_count",
    limit: int = 10,
    since: str | None = None,
) -> list[sqlite3.Row]:
    allowed = {"favorite_count", "retweet_count", "view_count"}
    if by not in allowed:
        by = "favorite_count"
    params: list = []
    date_filter = ""
    if since:
        date_filter = "AND COALESCE(published_at, fetched_at) >= ?"
        params.append(since)
    return conn.execute(
        f"""
        SELECT * FROM articles
        WHERE tweet_meta IS NOT NULL AND status = 'ok'
          AND CAST(json_extract(tweet_meta, '$.{by}') AS INTEGER) > 0
          {date_filter}
        ORDER BY CAST(json_extract(tweet_meta, '$.{by}') AS INTEGER) DESC
        LIMIT ?
        """,
        params + [limit],
    ).fetchall()


def list_sentiment_rows(
    conn: sqlite3.Connection, since: str | None = None
) -> list[sqlite3.Row]:
    params: list = []
    date_filter = ""
    if since:
        date_filter = "AND COALESCE(a.published_at, a.fetched_at) >= ?"
        params.append(since)
    return conn.execute(
        f"""
        SELECT a.url, a.tweet_meta, e.sentiment
        FROM articles a
        JOIN article_enrichments e ON e.article_id = a.id
        WHERE e.sentiment IS NOT NULL AND a.status = 'ok'
          {date_filter}
        """,
        params,
    ).fetchall()


def get_enriched_articles_for_keyword(
    conn: sqlite3.Connection,
    query: str,
    from_date: str | None = None,
    to_date: str | None = None,
    limit: int = 500,
) -> list[sqlite3.Row]:
    fts_rows = conn.execute(
        "SELECT a.id FROM articles_fts "
        "JOIN articles a ON a.id = articles_fts.rowid "
        "WHERE articles_fts MATCH ? LIMIT ?",
        (query, limit),
    ).fetchall()
    fts_ids = {r["id"] for r in fts_rows}

    like = f"%{query}%"
    summary_rows = conn.execute(
        "SELECT a.id FROM articles a "
        "JOIN article_enrichments e ON e.article_id = a.id "
        "WHERE e.summary LIKE ? AND a.status='ok' LIMIT ?",
        (like, limit),
    ).fetchall()
    all_ids = fts_ids | {r["id"] for r in summary_rows}
    if not all_ids:
        return []

    placeholders = ",".join("?" * len(all_ids))
    params: list = list(all_ids)
    date_filter = ""
    if from_date:
        date_filter += " AND a.published_at >= ?"
        params.append(from_date)
    if to_date:
        date_filter += " AND a.published_at <= ?"
        params.append(to_date + "T23:59:59")
    return conn.execute(
        f"SELECT a.*, e.summary, e.categories, e.entities, e.sentiment "
        f"FROM articles a "
        f"LEFT JOIN article_enrichments e ON e.article_id = a.id "
        f"WHERE a.id IN ({placeholders}){date_filter} "
        f"ORDER BY a.published_at ASC",
        params,
    ).fetchall()


_STRFTIME_FMT: dict[str, str] = {
    "day": "%Y-%m-%d",
    "week": "%Y-W%W",
    "month": "%Y-%m",
}


def timeseries_tweets(
    conn: sqlite3.Connection,
    group_by: str = "month",
    author: str | None = None,
    hashtag: str | None = None,
    ticker: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    metric: str = "count",
) -> list[sqlite3.Row]:
    fmt = _STRFTIME_FMT.get(group_by, "%Y-%m")
    allowed_metrics = {"count", "favorites", "retweets", "views"}
    if metric not in allowed_metrics:
        metric = "count"
    metric_sql = "COUNT(DISTINCT a.id)" if metric == "count" else {
        "favorites": "SUM(COALESCE(CAST(json_extract(a.tweet_meta, '$.favorite_count') AS INTEGER), 0))",
        "retweets": "SUM(COALESCE(CAST(json_extract(a.tweet_meta, '$.retweet_count') AS INTEGER), 0))",
        "views": "SUM(COALESCE(CAST(json_extract(a.tweet_meta, '$.view_count') AS INTEGER), 0))",
    }[metric]

    params: list = [fmt]
    joins = ""
    conditions = ["a.tweet_meta IS NOT NULL", "a.status = 'ok'"]

    if hashtag:
        joins += ", json_each(a.tweet_meta, '$.hashtags') AS je_h"
        conditions.append("je_h.value = ?")
        params.append(hashtag)
    if ticker:
        joins += ", json_each(a.tweet_meta, '$.tickers') AS je_t"
        conditions.append("je_t.value = ?")
        params.append(ticker)
    if author:
        conditions.append("json_extract(a.tweet_meta, '$.author_handle') = ?")
        params.append(author)
    if from_date:
        conditions.append("COALESCE(a.published_at, a.fetched_at) >= ?")
        params.append(from_date)
    if to_date:
        conditions.append("COALESCE(a.published_at, a.fetched_at) <= ?")
        params.append(to_date)

    where = " AND ".join(conditions)
    sql = (
        f"SELECT strftime(?, COALESCE(a.published_at, a.fetched_at)) AS bucket,"
        f" {metric_sql} AS value"
        f" FROM articles a{joins}"
        f" WHERE {where}"
        f" GROUP BY bucket ORDER BY bucket"
    )
    return conn.execute(sql, params).fetchall()


def timeseries_sentiment(
    conn: sqlite3.Connection,
    group_by: str = "month",
    author: str | None = None,
    hashtag: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
) -> list[sqlite3.Row]:
    fmt = _STRFTIME_FMT.get(group_by, "%Y-%m")
    params: list = [fmt]
    joins = "JOIN article_enrichments e ON e.article_id = a.id"
    conditions = ["e.sentiment IS NOT NULL", "a.status = 'ok'"]

    if hashtag:
        joins += ", json_each(a.tweet_meta, '$.hashtags') AS je_h"
        conditions.append("je_h.value = ?")
        params.append(hashtag)
    if author:
        conditions.append("json_extract(a.tweet_meta, '$.author_handle') = ?")
        params.append(author)
    if from_date:
        conditions.append("COALESCE(a.published_at, a.fetched_at) >= ?")
        params.append(from_date)
    if to_date:
        conditions.append("COALESCE(a.published_at, a.fetched_at) <= ?")
        params.append(to_date)

    where = " AND ".join(conditions)
    sql = (
        f"SELECT strftime(?, COALESCE(a.published_at, a.fetched_at)) AS bucket,"
        f" SUM(CASE WHEN e.sentiment='positive' THEN 1 ELSE 0 END) AS positive,"
        f" SUM(CASE WHEN e.sentiment='negative' THEN 1 ELSE 0 END) AS negative,"
        f" SUM(CASE WHEN e.sentiment='neutral' THEN 1 ELSE 0 END) AS neutral"
        f" FROM articles a {joins}"
        f" WHERE {where}"
        f" GROUP BY bucket ORDER BY bucket"
    )
    return conn.execute(sql, params).fetchall()


def upsert_media_ocr(
    conn: sqlite3.Connection,
    article_id: int,
    media_url: str,
    ocr_text: str,
    ocr_lang: str = "eng",
    extracted_at: str | None = None,
) -> None:
    if extracted_at is None:
        from datetime import datetime, timezone
        extracted_at = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT OR REPLACE INTO tweet_media_ocr (article_id, media_url, ocr_text, ocr_lang, extracted_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (article_id, media_url, ocr_text, ocr_lang, extracted_at),
    )


def get_media_ocr_for_article(conn: sqlite3.Connection, article_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM tweet_media_ocr WHERE article_id = ? ORDER BY media_url",
        (article_id,),
    ).fetchall()


def get_articles_with_unprocessed_media(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Return articles that have media_urls with at least one unprocessed URL."""
    return conn.execute(
        """
        SELECT DISTINCT a.id FROM articles a, json_each(a.tweet_meta, '$.media_urls') AS je_u
        WHERE a.status = 'ok' AND a.tweet_meta IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM tweet_media_ocr ocr
              WHERE ocr.article_id = a.id AND ocr.media_url = je_u.value
          )
        """
    ).fetchall()


def get_archive_db_stats(conn: sqlite3.Connection) -> dict:
    """Single-query aggregate snapshot for the dashboard."""
    row = conn.execute(
        """
        SELECT
            COUNT(CASE WHEN status = 'ok' THEN 1 END) AS total_articles,
            COUNT(CASE WHEN status = 'ok' AND tweet_meta IS NOT NULL THEN 1 END) AS total_tweets,
            COUNT(CASE WHEN status = 'pending' THEN 1 END) AS pending_scrape,
            COUNT(CASE WHEN status = 'blocked' THEN 1 END) AS blocked_scrape,
            COUNT(CASE WHEN status = 'failed' THEN 1 END) AS failed_scrape
        FROM articles
        """
    ).fetchone()

    authors_row = conn.execute(
        """
        SELECT COUNT(DISTINCT json_extract(tweet_meta, '$.author_handle')) AS v
        FROM articles
        WHERE tweet_meta IS NOT NULL AND status = 'ok'
          AND json_extract(tweet_meta, '$.author_handle') IS NOT NULL
        """
    ).fetchone()

    hashtags_row = conn.execute(
        """
        SELECT COUNT(DISTINCT je.value) AS v
        FROM articles a, json_each(a.tweet_meta, '$.hashtags') AS je
        WHERE a.status = 'ok' AND a.tweet_meta IS NOT NULL
        """
    ).fetchone()

    enriched_row = conn.execute(
        """
        SELECT COUNT(*) AS v FROM article_enrichments e
        JOIN articles a ON a.id = e.article_id
        WHERE a.status = 'ok' AND e.status = 'ok'
        """
    ).fetchone()

    latest_tweet_row = conn.execute(
        "SELECT * FROM articles WHERE tweet_meta IS NOT NULL AND status = 'ok'"
        " ORDER BY fetched_at DESC LIMIT 1"
    ).fetchone()

    return {
        "total_articles": row["total_articles"] or 0,
        "total_tweets": row["total_tweets"] or 0,
        "pending_scrape": row["pending_scrape"] or 0,
        "blocked_scrape": row["blocked_scrape"] or 0,
        "failed_scrape": row["failed_scrape"] or 0,
        "distinct_authors": authors_row["v"] or 0,
        "distinct_hashtags": hashtags_row["v"] or 0,
        "enriched": enriched_row["v"] or 0,
        "latest_tweet_row": dict(latest_tweet_row) if latest_tweet_row else None,
    }


def get_article_by_tweet_id(conn: sqlite3.Connection, tweet_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM articles WHERE json_extract(tweet_meta, '$.tweet_id') = ? AND status = 'ok'",
        (tweet_id,),
    ).fetchone()


def get_replies_to_tweet(conn: sqlite3.Connection, tweet_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM articles"
        " WHERE json_extract(tweet_meta, '$.in_reply_to_status_id') = ? AND status = 'ok'"
        " ORDER BY COALESCE(published_at, fetched_at) ASC",
        (tweet_id,),
    ).fetchall()


def get_articles_for_export(
    conn: sqlite3.Connection,
    from_date: str | None = None,
    to_date: str | None = None,
    author: str | None = None,
    hashtag: str | None = None,
    mention: str | None = None,
    ticker: str | None = None,
) -> list[sqlite3.Row]:
    """Fetch all ok tweet-articles matching scope filters for export."""
    wheres = ["a.status = 'ok'", "a.tweet_meta IS NOT NULL"]
    joins = ""
    params: list = []

    if author is not None:
        wheres.append("json_extract(a.tweet_meta, '$.author_handle') = ?")
        params.append(author)
    if hashtag is not None:
        joins += ", json_each(a.tweet_meta, '$.hashtags') AS je_h"
        wheres.append("je_h.value = ?")
        params.append(hashtag)
    if mention is not None:
        joins += ", json_each(a.tweet_meta, '$.mentioned_handles') AS je_m"
        wheres.append("je_m.value = ?")
        params.append(mention)
    if ticker is not None:
        joins += ", json_each(a.tweet_meta, '$.tickers') AS je_t"
        wheres.append("je_t.value = ?")
        params.append(ticker)
    if from_date:
        wheres.append("COALESCE(a.published_at, a.fetched_at) >= ?")
        params.append(from_date)
    if to_date:
        wheres.append("COALESCE(a.published_at, a.fetched_at) <= ?")
        params.append(to_date + "T23:59:59")

    where_clause = " AND ".join(wheres)
    sql = (
        f"SELECT DISTINCT a.* FROM articles a{joins}"
        f" WHERE {where_clause}"
        f" ORDER BY COALESCE(a.published_at, a.fetched_at) ASC"
    )
    return conn.execute(sql, params).fetchall()


def get_articles_in_timerange(
    conn: sqlite3.Connection,
    from_date: str | None = None,
    to_date: str | None = None,
) -> list[sqlite3.Row]:
    """Fetch all ok articles within the given date range."""
    wheres = ["status = 'ok'"]
    params: list = []
    if from_date:
        wheres.append("COALESCE(published_at, fetched_at) >= ?")
        params.append(from_date)
    if to_date:
        wheres.append("COALESCE(published_at, fetched_at) <= ?")
        params.append(to_date + "T23:59:59")
    where_clause = " AND ".join(wheres)
    return conn.execute(
        f"SELECT * FROM articles WHERE {where_clause} ORDER BY COALESCE(published_at, fetched_at) ASC",
        params,
    ).fetchall()


def get_engagement_refresh_candidates(
    conn: sqlite3.Connection,
    batch_size: int = 50,
    min_age_hours: int = 6,
    recent_days: int = 7,
) -> list[sqlite3.Row]:
    """Return articles that should have their engagement stats refreshed.

    Selection rules (union):
    1. Top `batch_size` tweets by total engagement (favorites + retweets + views).
    2. Any tweet published within `recent_days` days (still growing fast).

    Both sets exclude tweets refreshed within `min_age_hours` hours to avoid
    hammering the syndication endpoint.  Returns the distinct union, deduped by id.
    """
    threshold = (
        datetime.now(timezone.utc) - timedelta(hours=min_age_hours)
    ).isoformat()
    recent_cutoff = (
        datetime.now(timezone.utc) - timedelta(days=recent_days)
    ).isoformat()

    rows = conn.execute(
        f"""
        SELECT DISTINCT a.*
        FROM articles a
        WHERE a.status = 'ok'
          AND a.tweet_meta IS NOT NULL
          AND (
            json_extract(a.tweet_meta, '$.refreshed_at') IS NULL
            OR json_extract(a.tweet_meta, '$.refreshed_at') < ?
          )
          AND (
            -- top-N by engagement
            a.id IN (
              SELECT id FROM articles
              WHERE status = 'ok' AND tweet_meta IS NOT NULL
              ORDER BY (
                COALESCE(CAST(json_extract(tweet_meta, '$.favorite_count') AS INTEGER), 0)
                + COALESCE(CAST(json_extract(tweet_meta, '$.retweet_count') AS INTEGER), 0)
                + COALESCE(CAST(json_extract(tweet_meta, '$.view_count') AS INTEGER), 0)
              ) DESC
              LIMIT ?
            )
            OR
            -- recently published
            COALESCE(a.published_at, a.fetched_at) >= ?
          )
        """,
        (threshold, batch_size, recent_cutoff),
    ).fetchall()
    return rows


def _normalize_entity_name(name: str) -> str:
    return name.strip().lower()


# Higher = more specific / preferred canonical kind (mirrors scripts/dedup_entities.py)
_KIND_RANK: dict[str, int] = {"ticker": 3, "company": 2, "other": 1, "person": 0}


def upsert_entity(conn: sqlite3.Connection, name: str, kind: str) -> int:
    """Insert or return existing entity id.

    If an unmerged entity with the same normalized_name already exists under a
    different kind, return its id — upgrading its kind if the incoming kind is
    more specific (ticker > company > other > person).  This prevents the same
    real-world concept from accumulating duplicate rows at insert time.
    """
    norm = _normalize_entity_name(name)
    row = conn.execute(
        "SELECT id, kind FROM entities WHERE normalized_name=? AND canonical_id IS NULL LIMIT 1",
        (norm,),
    ).fetchone()
    if row:
        incoming_rank = _KIND_RANK.get(kind, -1)
        existing_rank = _KIND_RANK.get(row["kind"], -1)
        if incoming_rank > existing_rank:
            conn.execute("UPDATE entities SET kind=? WHERE id=?", (kind, row["id"]))
            conn.commit()
        return row["id"]
    cur = conn.execute(
        "INSERT INTO entities (name, kind, normalized_name) VALUES (?,?,?)",
        (name.strip(), kind, norm),
    )
    conn.commit()
    return cur.lastrowid


def link_article_entity(
    conn: sqlite3.Connection,
    article_id: int,
    entity_id: int,
    mention_text: str = "",
    assigned_by: str = "llm",
) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO article_entities (article_id, entity_id, mention_text, assigned_by)"
        " VALUES (?,?,?,?)",
        (article_id, entity_id, mention_text, assigned_by),
    )
    conn.commit()


def backfill_entities(conn: sqlite3.Connection) -> int:
    """Populate entities + article_entities from existing article_enrichments.entities JSON.

    Idempotent: INSERT OR IGNORE skips rows that already exist.
    Returns number of article_entities rows inserted.
    """
    import json as _json

    rows = conn.execute(
        "SELECT article_id, entities FROM article_enrichments WHERE entities IS NOT NULL"
    ).fetchall()
    inserted = 0
    for row in rows:
        try:
            ents = _json.loads(row["entities"])
        except (ValueError, TypeError):
            continue
        for e in ents:
            name = (e.get("name") or "").strip()
            kind = e.get("kind") or "other"
            mention = e.get("mention_text") or name
            if not name:
                continue
            eid = upsert_entity(conn, name, kind)
            cur = conn.execute(
                "INSERT OR IGNORE INTO article_entities (article_id, entity_id, mention_text, assigned_by)"
                " VALUES (?,?,?,'llm')",
                (row["article_id"], eid, mention),
            )
            inserted += cur.rowcount
    conn.commit()
    return inserted


def list_entities(
    conn: sqlite3.Connection,
    kind: str | None = None,
    search: str | None = None,
    order: str = "count",
    limit: int = 100,
    offset: int = 0,
    include_aliases: bool = False,
) -> list[dict]:
    """List entities with their article counts.

    By default excludes merged aliases (canonical_id IS NOT NULL).
    """
    conditions = []
    params: list = []
    if not include_aliases:
        conditions.append("e.canonical_id IS NULL")
    if kind:
        conditions.append("e.kind = ?")
        params.append(kind)
    if search:
        conditions.append("(e.name LIKE ? OR e.normalized_name LIKE ?)")
        like = f"%{search.lower()}%"
        params.extend([like, like])

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    order_sql = "article_count DESC" if order == "count" else "e.name COLLATE NOCASE ASC"
    params.extend([limit, offset])

    rows = conn.execute(
        f"""
        SELECT e.id, e.name, e.kind, e.normalized_name, e.canonical_id, e.merged_at,
               COUNT(ae.article_id) AS article_count
        FROM entities e
        LEFT JOIN article_entities ae ON ae.entity_id = e.id
        {where}
        GROUP BY e.id
        ORDER BY {order_sql}
        LIMIT ? OFFSET ?
        """,
        params,
    ).fetchall()
    return [dict(r) for r in rows]


def get_entity(conn: sqlite3.Connection, entity_id: int) -> dict | None:
    row = conn.execute(
        """
        SELECT e.id, e.name, e.kind, e.normalized_name, e.canonical_id, e.merged_at,
               COUNT(ae.article_id) AS article_count
        FROM entities e
        LEFT JOIN article_entities ae ON ae.entity_id = e.id
        WHERE e.id = ?
        GROUP BY e.id
        """,
        (entity_id,),
    ).fetchone()
    if not row:
        return None
    result = dict(row)
    # Aliases pointing to this entity
    aliases = conn.execute(
        "SELECT id, name, kind FROM entities WHERE canonical_id=?", (entity_id,)
    ).fetchall()
    result["aliases"] = [dict(a) for a in aliases]
    return result


def merge_entities(
    conn: sqlite3.Connection,
    source_ids: list[int],
    target_id: int,
) -> dict:
    """Redirect all article_entities from source_ids to target_id and mark sources as aliases."""
    now = datetime.now(timezone.utc).isoformat()
    # Exclude target from source_ids just in case
    source_ids = [s for s in source_ids if s != target_id]
    if not source_ids:
        return {"merged": 0, "rows_moved": 0}

    placeholders = ",".join("?" * len(source_ids))
    # Move article_entities — INSERT OR IGNORE so duplicates are dropped
    conn.execute(
        f"""
        INSERT OR IGNORE INTO article_entities (article_id, entity_id, mention_text, assigned_by)
        SELECT article_id, ?, mention_text, assigned_by
        FROM article_entities
        WHERE entity_id IN ({placeholders})
        """,
        [target_id, *source_ids],
    )
    # Delete old article_entities for sources
    conn.execute(
        f"DELETE FROM article_entities WHERE entity_id IN ({placeholders})",
        source_ids,
    )
    # Mark source entities as aliases
    conn.execute(
        f"UPDATE entities SET canonical_id=?, merged_at=? WHERE id IN ({placeholders})",
        [target_id, now, *source_ids],
    )
    conn.commit()

    target = conn.execute(
        "SELECT COUNT(*) AS c FROM article_entities WHERE entity_id=?", (target_id,)
    ).fetchone()
    return {"merged": len(source_ids), "target_article_count": target["c"]}


def unmerge_entity(conn: sqlite3.Connection, source_id: int) -> dict:
    """Clear canonical_id for source_id (note: article_entities remain on target)."""
    conn.execute(
        "UPDATE entities SET canonical_id=NULL, merged_at=NULL WHERE id=?", (source_id,)
    )
    conn.commit()
    return {"unmerged": source_id, "warning": "article_entities remain on the target entity"}


def rename_entity(conn: sqlite3.Connection, entity_id: int, new_name: str) -> dict | None:
    new_name = new_name.strip()
    if not new_name:
        return None
    conn.execute(
        "UPDATE entities SET name=?, normalized_name=? WHERE id=?",
        (new_name, _normalize_entity_name(new_name), entity_id),
    )
    conn.commit()
    return get_entity(conn, entity_id)


def get_dossier_cache(conn: sqlite3.Connection, entity_name: str) -> sqlite3.Row | None:
    """Return cached dossier if it exists and is younger than 24 hours."""
    row = conn.execute(
        "SELECT * FROM entity_dossiers WHERE entity_name = ?",
        (entity_name.strip().lower(),),
    ).fetchone()
    if not row:
        return None
    generated_at = datetime.fromisoformat(row["generated_at"])
    if generated_at.tzinfo is None:
        generated_at = generated_at.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) - generated_at > timedelta(hours=24):
        return None
    return row


def save_dossier_cache(conn: sqlite3.Connection, entity_name: str, body_json: str) -> None:
    conn.execute(
        """INSERT INTO entity_dossiers (entity_name, body_json, generated_at)
           VALUES (?, ?, ?)
           ON CONFLICT(entity_name) DO UPDATE SET body_json=excluded.body_json, generated_at=excluded.generated_at""",
        (entity_name.strip().lower(), body_json, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()


def backfill_tickers(conn: sqlite3.Connection) -> int:
    """Walk all articles with tweet_meta, add/update the tickers field. Idempotent.

    Returns the number of rows updated.
    """
    import json as _json
    from ..enrich.tickers import extract_tickers

    rows = conn.execute(
        "SELECT id, tweet_meta FROM articles WHERE tweet_meta IS NOT NULL AND status = 'ok'"
    ).fetchall()
    updated = 0
    for row in rows:
        try:
            meta = _json.loads(row["tweet_meta"])
        except (ValueError, TypeError):
            continue
        text = meta.get("text") or ""
        tickers = extract_tickers(text)
        existing = meta.get("tickers")
        if existing == tickers:
            continue
        meta["tickers"] = tickers
        new_blob = compute_tweet_search_blob(meta)
        conn.execute(
            "UPDATE articles SET tweet_meta = ?, tweet_search_blob = ? WHERE id = ?",
            (_json.dumps(meta), new_blob, row["id"]),
        )
        updated += 1
    conn.commit()
    return updated


# ── Tweet-meta entity sync (E1) ──────────────────────────────────────────────

def sync_tweet_entities(
    conn: sqlite3.Connection,
    article_id: int,
    tweet_meta: dict,
) -> dict:
    """Promote tweet_meta fields into entities + article_entities.

    Creates entity rows for author_handle (kind='author'), mentioned_handles
    (kind='mention'), hashtags (kind='hashtag'), and tickers (kind='ticker').
    Links each to the article via article_entities with assigned_by='tweet_meta'.

    INSERT OR IGNORE semantics throughout — idempotent, never touches canonical_id.
    Returns counts: {authors, mentions, hashtags, tickers}.
    """
    counts = {"authors": 0, "mentions": 0, "hashtags": 0, "tickers": 0}

    def _ensure(name: str, kind: str) -> int | None:
        name = name.strip()
        if not name:
            return None
        norm = name.lower()
        conn.execute(
            "INSERT OR IGNORE INTO entities (name, kind, normalized_name) VALUES (?, ?, ?)",
            (name, kind, norm),
        )
        row = conn.execute(
            "SELECT id FROM entities WHERE normalized_name=? AND kind=?",
            (norm, kind),
        ).fetchone()
        return row["id"] if row else None

    def _link(entity_id: int) -> None:
        conn.execute(
            "INSERT OR IGNORE INTO article_entities (article_id, entity_id, assigned_by)"
            " VALUES (?, ?, 'tweet_meta')",
            (article_id, entity_id),
        )

    handle = (tweet_meta.get("author_handle") or "").strip()
    if handle:
        eid = _ensure(handle, "author")
        if eid:
            _link(eid)
            counts["authors"] += 1

    for m in tweet_meta.get("mentioned_handles") or []:
        m = (m or "").strip()
        if m:
            eid = _ensure(m, "mention")
            if eid:
                _link(eid)
                counts["mentions"] += 1

    for h in tweet_meta.get("hashtags") or []:
        h = (h or "").strip()
        if h:
            eid = _ensure(h, "hashtag")
            if eid:
                _link(eid)
                counts["hashtags"] += 1

    for t in tweet_meta.get("tickers") or []:
        t = (t or "").strip()
        if t:
            eid = _ensure(t, "ticker")
            if eid:
                _link(eid)
                counts["tickers"] += 1

    conn.commit()
    return counts


# ── Entity home cards (R6) ────────────────────────────────────────────────────

def get_entity_home_cards(
    conn: sqlite3.Connection,
    kind: str | None = None,
    search: str | None = None,
    sort: str = "count",
    limit: int = 50,
    offset: int = 0,
    now: str | None = None,
) -> dict:
    """Return paginated entity cards for the entity-first home page."""
    import json as _json

    ref_now = datetime.fromisoformat(now) if now else datetime.now(timezone.utc)
    date_30d = (ref_now - timedelta(days=30)).isoformat()
    date_60d = (ref_now - timedelta(days=60)).isoformat()

    kind_filter = "AND e.kind = ?" if kind else ""
    search_filter = "AND (e.name LIKE ? OR e.normalized_name LIKE ?)" if search else ""

    # CTE needs: date_30d, date_60d, date_30d (recent/prior counts) +
    # date_30d, date_60d, date_30d, date_60d, date_30d (trend_pct numerator + denominator)
    cte_params: list = [date_30d, date_60d, date_30d, date_30d, date_60d, date_30d, date_60d, date_30d]
    filter_params: list = []
    if kind:
        filter_params.append(kind)
    if search:
        like = f"%{search}%"
        filter_params += [like, like]

    order_clauses = {
        "count": "es.article_count DESC",
        "recent": "es.recent_count DESC, es.article_count DESC",
        "sentiment": "es.bullish_count DESC, es.article_count DESC",
        "trending": "es.trend_pct DESC, es.recent_count DESC",
    }
    order_by = order_clauses.get(sort, "es.article_count DESC")

    rows = conn.execute(
        f"""
        WITH alias_map AS (
            SELECT id AS entity_id, COALESCE(canonical_id, id) AS canon_id FROM entities
        ),
        entity_articles AS (
            SELECT am.canon_id, ae.article_id
            FROM article_entities ae
            JOIN alias_map am ON am.entity_id = ae.entity_id
        ),
        entity_stats AS (
            SELECT
                ea.canon_id,
                COUNT(DISTINCT ea.article_id) AS article_count,
                SUM(CASE WHEN COALESCE(a.published_at, a.fetched_at) >= ? THEN 1 ELSE 0 END) AS recent_count,
                SUM(CASE WHEN COALESCE(a.published_at, a.fetched_at) >= ?
                         AND COALESCE(a.published_at, a.fetched_at) < ? THEN 1 ELSE 0 END) AS prior_count,
                MIN(COALESCE(a.published_at, a.fetched_at)) AS first_seen,
                MAX(COALESCE(a.published_at, a.fetched_at)) AS last_seen,
                SUM(CASE WHEN enr.sentiment = 'bullish' THEN 1 ELSE 0 END) AS bullish_count,
                SUM(CASE WHEN enr.sentiment = 'bearish' THEN 1 ELSE 0 END) AS bearish_count,
                CAST(
                    100.0 * (
                        SUM(CASE WHEN COALESCE(a.published_at, a.fetched_at) >= ? THEN 1 ELSE 0 END)
                        - SUM(CASE WHEN COALESCE(a.published_at, a.fetched_at) >= ?
                                   AND COALESCE(a.published_at, a.fetched_at) < ? THEN 1 ELSE 0 END)
                    ) / MAX(
                        SUM(CASE WHEN COALESCE(a.published_at, a.fetched_at) >= ?
                                 AND COALESCE(a.published_at, a.fetched_at) < ? THEN 1 ELSE 0 END), 1
                    ) AS INTEGER
                ) AS trend_pct,
                MAX(a.id) AS recent_article_id
            FROM entity_articles ea
            JOIN articles a ON a.id = ea.article_id AND a.status = 'ok'
            LEFT JOIN article_enrichments enr ON enr.article_id = a.id
            GROUP BY ea.canon_id
        )
        SELECT
            e.id AS canonical_id, e.name, e.kind,
            es.article_count, es.recent_count, es.prior_count,
            es.first_seen, es.last_seen,
            es.bullish_count, es.bearish_count,
            es.trend_pct, es.recent_article_id
        FROM entities e
        JOIN entity_stats es ON es.canon_id = e.id
        WHERE e.canonical_id IS NULL
          {kind_filter}
          {search_filter}
        ORDER BY {order_by}
        LIMIT ? OFFSET ?
        """,
        cte_params + filter_params + [limit, offset],
    ).fetchall()

    total_row = conn.execute(
        f"""
        SELECT COUNT(*) AS n FROM entities e
        WHERE e.canonical_id IS NULL
          {kind_filter}
          {search_filter}
        """,
        ([kind] if kind else []) + ([f"%{search}%", f"%{search}%"] if search else []),
    ).fetchone()
    total = total_row["n"] if total_row else 0

    if not rows:
        return {"total": total, "entities": []}

    canon_ids = [r["canonical_id"] for r in rows]
    ph = ",".join("?" * len(canon_ids))

    # Batch-fetch top 3 authors per entity
    author_rows = conn.execute(
        f"""
        SELECT am.canon_id,
               json_extract(a.tweet_meta, '$.author_handle') AS handle,
               COUNT(*) AS cnt
        FROM article_entities ae
        JOIN (SELECT id AS entity_id, COALESCE(canonical_id, id) AS canon_id FROM entities) am
          ON am.entity_id = ae.entity_id
        JOIN articles a ON a.id = ae.article_id AND a.status = 'ok'
          AND a.tweet_meta IS NOT NULL
        WHERE am.canon_id IN ({ph}) AND json_extract(a.tweet_meta, '$.author_handle') IS NOT NULL
        GROUP BY am.canon_id, handle
        ORDER BY cnt DESC
        """,
        canon_ids,
    ).fetchall()

    authors_by_entity: dict[int, list] = {}
    for ar in author_rows:
        lst = authors_by_entity.setdefault(ar["canon_id"], [])
        if len(lst) < 3:
            lst.append({"handle": ar["handle"], "count": ar["cnt"]})

    # Batch-fetch top 3 co-occurring canonical entities per entity
    cooc_rows = conn.execute(
        f"""
        SELECT am1.canon_id AS source_id,
               COALESCE(e2.canonical_id, ae2.entity_id) AS cooc_canon_id,
               COALESCE(e3.name, e2.name) AS cooc_name,
               e2.kind AS cooc_kind,
               COUNT(*) AS cnt
        FROM article_entities ae1
        JOIN (SELECT id AS entity_id, COALESCE(canonical_id, id) AS canon_id FROM entities) am1
          ON am1.entity_id = ae1.entity_id
        JOIN article_entities ae2 ON ae2.article_id = ae1.article_id
          AND ae2.entity_id != ae1.entity_id
        JOIN entities e2 ON e2.id = ae2.entity_id
        LEFT JOIN entities e3 ON e3.id = e2.canonical_id
        WHERE am1.canon_id IN ({ph})
        GROUP BY source_id, cooc_canon_id
        ORDER BY cnt DESC
        """,
        canon_ids,
    ).fetchall()

    cooc_by_entity: dict[int, list] = {}
    for cr in cooc_rows:
        lst = cooc_by_entity.setdefault(cr["source_id"], [])
        if len(lst) < 3 and cr["cooc_canon_id"] not in [c["canonical_id"] for c in lst]:
            lst.append({
                "name": cr["cooc_name"],
                "kind": cr["cooc_kind"],
                "canonical_id": cr["cooc_canon_id"],
                "count": cr["cnt"],
            })

    # Check has_deepdive (entity_dossiers cache — any age counts)
    deepdive_rows = conn.execute(
        f"SELECT entity_name FROM entity_dossiers WHERE entity_name IN ({ph})",
        [str(cid).lower() for cid in canon_ids],
    ).fetchall()
    # Also check by normalized name
    name_map = {r["name"].strip().lower(): r["canonical_id"] for r in rows}
    deepdive_names = {dr["entity_name"] for dr in deepdive_rows}
    has_deepdive_set = {
        name_map[dn] for dn in deepdive_names if dn in name_map
    }

    def _sentiment_majority(row) -> str:
        b = row["bullish_count"] or 0
        br = row["bearish_count"] or 0
        if b >= br and b > 0:
            return "bullish"
        if br > b:
            return "bearish"
        return "neutral"

    entities = []
    for r in rows:
        cid = r["canonical_id"]
        trend_pct = r["trend_pct"] or 0
        trend_str = (f"+{trend_pct}%" if trend_pct >= 0 else f"{trend_pct}%") if r["recent_count"] else None
        entities.append({
            "canonical_id": cid,
            "name": r["name"],
            "kind": r["kind"],
            "article_count": r["article_count"] or 0,
            "recent_count": r["recent_count"] or 0,
            "first_seen": r["first_seen"],
            "last_seen": r["last_seen"],
            "sentiment_majority": _sentiment_majority(r),
            "trend": trend_str,
            "top_authors": authors_by_entity.get(cid, []),
            "top_cooccurring_entities": cooc_by_entity.get(cid, []),
            "recent_article_id": r["recent_article_id"],
            "has_deepdive": cid in has_deepdive_set,
        })

    return {"total": total, "entities": entities}


# ── Bookmarks ─────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def upsert_bookmark(
    conn: sqlite3.Connection,
    article_id: int,
    starred: int = 1,
    note: str | None = None,
) -> sqlite3.Row:
    now = _now_iso()
    existing = conn.execute("SELECT * FROM bookmarks WHERE article_id = ?", (article_id,)).fetchone()
    if existing:
        conn.execute(
            "UPDATE bookmarks SET starred = ?, note = ?, updated_at = ? WHERE article_id = ?",
            (starred, note if note is not None else existing["note"], now, article_id),
        )
    else:
        conn.execute(
            "INSERT INTO bookmarks (article_id, starred, note, added_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (article_id, starred, note, now, now),
        )
    conn.commit()
    return conn.execute("SELECT * FROM bookmarks WHERE article_id = ?", (article_id,)).fetchone()


def delete_bookmark(conn: sqlite3.Connection, article_id: int) -> bool:
    cur = conn.execute("DELETE FROM bookmarks WHERE article_id = ?", (article_id,))
    conn.commit()
    return cur.rowcount > 0


def get_bookmark(conn: sqlite3.Connection, article_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM bookmarks WHERE article_id = ?", (article_id,)).fetchone()


def get_bookmarks(
    conn: sqlite3.Connection,
    has_note: bool = False,
    since: str | None = None,
    limit: int = 100,
) -> list[sqlite3.Row]:
    conditions = ["b.starred = 1"]
    params: list = []
    if has_note:
        conditions.append("b.note IS NOT NULL AND b.note != ''")
    if since:
        conditions.append("b.added_at >= ?")
        params.append(since)
    where = " AND ".join(conditions)
    params.append(limit)
    return conn.execute(
        f"SELECT b.*, a.url, a.title, a.tweet_meta FROM bookmarks b"
        f" JOIN articles a ON a.id = b.article_id"
        f" WHERE {where} ORDER BY b.added_at DESC LIMIT ?",
        params,
    ).fetchall()


def upsert_message_bookmark(
    conn: sqlite3.Connection,
    msg_key: str,
    chat_id: str,
    sender: str,
    ts: str,
    body: str,
    note: str | None = None,
) -> sqlite3.Row:
    now = _now_iso()
    existing = conn.execute("SELECT * FROM message_bookmarks WHERE msg_key = ?", (msg_key,)).fetchone()
    if existing:
        conn.execute(
            "UPDATE message_bookmarks SET note = ?, updated_at = ? WHERE msg_key = ?",
            (note if note is not None else existing["note"], now, msg_key),
        )
    else:
        conn.execute(
            "INSERT INTO message_bookmarks (msg_key, chat_id, sender, ts, body, note, added_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (msg_key, chat_id, sender, ts, body, note, now, now),
        )
    conn.commit()
    return conn.execute("SELECT * FROM message_bookmarks WHERE msg_key = ?", (msg_key,)).fetchone()


def delete_message_bookmark(conn: sqlite3.Connection, msg_key: str) -> bool:
    cur = conn.execute("DELETE FROM message_bookmarks WHERE msg_key = ?", (msg_key,))
    conn.commit()
    return cur.rowcount > 0


def get_message_bookmark(conn: sqlite3.Connection, msg_key: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM message_bookmarks WHERE msg_key = ?", (msg_key,)).fetchone()


def get_message_bookmarks(conn: sqlite3.Connection, limit: int = 100) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM message_bookmarks ORDER BY added_at DESC LIMIT ?", (limit,)
    ).fetchall()


# ── Research bins ─────────────────────────────────────────────────────────────

def create_research_bin(
    conn: sqlite3.Connection,
    name: str,
    hypothesis: str | None = None,
    seed_query: str | None = None,
    seed_filters: str | None = None,
    expiry_days: int = 7,
) -> sqlite3.Row:
    now = _now_iso()
    expires = (datetime.fromisoformat(now) + timedelta(days=expiry_days)).isoformat()
    conn.execute(
        """INSERT INTO research_bins (name, hypothesis, seed_query, seed_filters,
           created_at, last_accessed_at, expires_at)
           VALUES (?,?,?,?,?,?,?)""",
        (name, hypothesis, seed_query, seed_filters, now, now, expires),
    )
    conn.commit()
    return conn.execute(
        "SELECT * FROM research_bins WHERE id=last_insert_rowid()"
    ).fetchone()


def get_research_bins(
    conn: sqlite3.Connection,
    include_expired: bool = False,
) -> list[sqlite3.Row]:
    now = _now_iso()
    if include_expired:
        rows = conn.execute(
            "SELECT rb.*, COUNT(rbi.id) AS item_count"
            " FROM research_bins rb"
            " LEFT JOIN research_bin_items rbi ON rbi.bin_id = rb.id"
            " GROUP BY rb.id ORDER BY rb.last_accessed_at DESC"
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT rb.*, COUNT(rbi.id) AS item_count"
            " FROM research_bins rb"
            " LEFT JOIN research_bin_items rbi ON rbi.bin_id = rb.id"
            " WHERE rb.expires_at > ?"
            " GROUP BY rb.id ORDER BY rb.last_accessed_at DESC",
            (now,),
        ).fetchall()
    return rows


def get_research_bin(conn: sqlite3.Connection, bin_id: int) -> sqlite3.Row | None:
    row = conn.execute("SELECT * FROM research_bins WHERE id=?", (bin_id,)).fetchone()
    if row:
        conn.execute(
            "UPDATE research_bins SET last_accessed_at=? WHERE id=?", (_now_iso(), bin_id)
        )
        conn.commit()
    return row


def get_research_bin_items(conn: sqlite3.Connection, bin_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM research_bin_items WHERE bin_id=? ORDER BY pinned_at ASC", (bin_id,)
    ).fetchall()
    result = []
    for r in rows:
        item = dict(r)
        if item["target_kind"] == "article":
            try:
                art = conn.execute(
                    "SELECT id, url, title, published_at, tweet_meta FROM articles WHERE id=?",
                    (item["target_id"],),
                ).fetchone()
                item["article"] = dict(art) if art else None
            except Exception:
                item["article"] = None
        result.append(item)
    return result


def add_research_bin_item(
    conn: sqlite3.Connection,
    bin_id: int,
    target_kind: str,
    target_id: str,
    note: str | None = None,
) -> sqlite3.Row:
    now = _now_iso()
    conn.execute(
        """INSERT OR IGNORE INTO research_bin_items (bin_id, target_kind, target_id, pinned_at, note)
           VALUES (?,?,?,?,?)""",
        (bin_id, target_kind, target_id, now, note),
    )
    conn.commit()
    return conn.execute(
        "SELECT * FROM research_bin_items WHERE bin_id=? AND target_kind=? AND target_id=?",
        (bin_id, target_kind, target_id),
    ).fetchone()


def delete_research_bin_item(conn: sqlite3.Connection, item_id: int) -> bool:
    cur = conn.execute("DELETE FROM research_bin_items WHERE id=?", (item_id,))
    conn.commit()
    return cur.rowcount > 0


def promote_research_bin_to_collection(
    conn: sqlite3.Connection,
    bin_id: int,
) -> sqlite3.Row | None:
    """Create a Collection from the bin's article items and link it back."""
    bin_row = conn.execute("SELECT * FROM research_bins WHERE id=?", (bin_id,)).fetchone()
    if not bin_row:
        return None
    col = create_collection(conn, bin_row["name"], description=bin_row["hypothesis"] or "")
    article_items = conn.execute(
        "SELECT target_id FROM research_bin_items WHERE bin_id=? AND target_kind='article'",
        (bin_id,),
    ).fetchall()
    for item in article_items:
        try:
            add_collection_item(conn, col["id"], int(item["target_id"]))
        except Exception:
            pass
    conn.execute(
        "UPDATE research_bins SET promoted_collection_id=? WHERE id=?", (col["id"], bin_id)
    )
    conn.commit()
    return col


def delete_research_bin(conn: sqlite3.Connection, bin_id: int) -> bool:
    conn.execute("DELETE FROM research_bin_items WHERE bin_id=?", (bin_id,))
    cur = conn.execute("DELETE FROM research_bins WHERE id=?", (bin_id,))
    conn.commit()
    return cur.rowcount > 0


def update_research_bin(
    conn: sqlite3.Connection,
    bin_id: int,
    name: str | None = None,
    hypothesis: str | None = None,
) -> sqlite3.Row | None:
    if name is not None:
        conn.execute("UPDATE research_bins SET name=? WHERE id=?", (name, bin_id))
    if hypothesis is not None:
        conn.execute("UPDATE research_bins SET hypothesis=? WHERE id=?", (hypothesis, bin_id))
    conn.commit()
    return conn.execute("SELECT * FROM research_bins WHERE id=?", (bin_id,)).fetchone()


# ── Collections ───────────────────────────────────────────────────────────────

def create_collection(
    conn: sqlite3.Connection,
    name: str,
    description: str | None = None,
) -> sqlite3.Row:
    now = _now_iso()
    conn.execute(
        "INSERT INTO collections (name, description, created_at) VALUES (?, ?, ?)",
        (name, description, now),
    )
    conn.commit()
    return conn.execute("SELECT * FROM collections WHERE name = ?", (name,)).fetchone()


def get_collections(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT c.*, COUNT(ci.article_id) AS item_count"
        " FROM collections c"
        " LEFT JOIN collection_items ci ON ci.collection_id = c.id"
        " GROUP BY c.id ORDER BY c.created_at DESC"
    ).fetchall()


def get_collection(conn: sqlite3.Connection, collection_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM collections WHERE id = ?", (collection_id,)).fetchone()


def add_collection_item(conn: sqlite3.Connection, collection_id: int, article_id: int) -> None:
    now = _now_iso()
    conn.execute(
        "INSERT OR IGNORE INTO collection_items (collection_id, article_id, added_at) VALUES (?, ?, ?)",
        (collection_id, article_id, now),
    )
    conn.commit()


def remove_collection_item(conn: sqlite3.Connection, collection_id: int, article_id: int) -> bool:
    cur = conn.execute(
        "DELETE FROM collection_items WHERE collection_id = ? AND article_id = ?",
        (collection_id, article_id),
    )
    conn.commit()
    return cur.rowcount > 0


def delete_collection(conn: sqlite3.Connection, collection_id: int) -> bool:
    """Delete a collection and all its items. Returns True if removed."""
    # Items first (FK doesn't cascade in our schema, so do it explicitly).
    conn.execute("DELETE FROM collection_items WHERE collection_id = ?", (collection_id,))
    cur = conn.execute("DELETE FROM collections WHERE id = ?", (collection_id,))
    conn.commit()
    return cur.rowcount > 0


def get_collection_items(
    conn: sqlite3.Connection,
    collection_id: int,
    page: int = 1,
    page_size: int = 20,
    order: str = "desc",
) -> list[sqlite3.Row]:
    direction = "DESC" if order.lower() == "desc" else "ASC"
    offset = (page - 1) * page_size
    return conn.execute(
        f"SELECT a.*, ci.added_at AS collection_added_at"
        f" FROM collection_items ci JOIN articles a ON a.id = ci.article_id"
        f" WHERE ci.collection_id = ? ORDER BY ci.added_at {direction} LIMIT ? OFFSET ?",
        (collection_id, page_size, offset),
    ).fetchall()


# ── Co-occurrence ─────────────────────────────────────────────────────────────

_SEED_CONDITION = {
    "hashtag":  "EXISTS (SELECT 1 FROM json_each(a.tweet_meta, '$.hashtags') WHERE value = ?)",
    "mention":  "EXISTS (SELECT 1 FROM json_each(a.tweet_meta, '$.mentioned_handles') WHERE value = ?)",
    "ticker":   "EXISTS (SELECT 1 FROM json_each(a.tweet_meta, '$.tickers') WHERE value = ?)",
    "author":   "json_extract(a.tweet_meta, '$.author_handle') = ?",
}

_TARGET_SELECT = {
    "hashtag": (
        "SELECT je.value AS value, COUNT(*) AS cnt"
        " FROM articles a, json_each(a.tweet_meta, '$.hashtags') AS je"
        " WHERE a.status='ok' AND a.tweet_meta IS NOT NULL AND {seed_cond}"
        "   AND je.value != ?"
        " GROUP BY je.value ORDER BY cnt DESC LIMIT ?"
    ),
    "mention": (
        "SELECT je.value AS value, COUNT(*) AS cnt"
        " FROM articles a, json_each(a.tweet_meta, '$.mentioned_handles') AS je"
        " WHERE a.status='ok' AND a.tweet_meta IS NOT NULL AND {seed_cond}"
        "   AND je.value != ?"
        " GROUP BY je.value ORDER BY cnt DESC LIMIT ?"
    ),
    "ticker": (
        "SELECT je.value AS value, COUNT(*) AS cnt"
        " FROM articles a, json_each(a.tweet_meta, '$.tickers') AS je"
        " WHERE a.status='ok' AND a.tweet_meta IS NOT NULL AND {seed_cond}"
        "   AND je.value != ?"
        " GROUP BY je.value ORDER BY cnt DESC LIMIT ?"
    ),
    "author": (
        "SELECT json_extract(a.tweet_meta, '$.author_handle') AS value, COUNT(*) AS cnt"
        " FROM articles a"
        " WHERE a.status='ok' AND a.tweet_meta IS NOT NULL AND {seed_cond}"
        "   AND json_extract(a.tweet_meta, '$.author_handle') != ?"
        " GROUP BY value ORDER BY cnt DESC LIMIT ?"
    ),
}


def get_cooccurrence(
    conn: sqlite3.Connection,
    seed_kind: str,
    seed_value: str,
    kind: str,
    limit: int = 20,
) -> list[dict]:
    """Return items of `kind` that co-occur with the given seed, sorted by frequency."""
    seed_cond = _SEED_CONDITION.get(seed_kind)
    tpl = _TARGET_SELECT.get(kind)
    if not seed_cond or not tpl:
        return []

    # Exclude self when seed_kind == kind
    exclude_value = seed_value if seed_kind == kind else ""
    sql = tpl.format(seed_cond=seed_cond)
    rows = conn.execute(sql, (seed_value, exclude_value, limit)).fetchall()
    return [{"value": r["value"], "count": r["cnt"], "kind": kind} for r in rows if r["value"]]


# ── Tweet link helpers (E4) ────────────────────────────────────────────────────

def _resolve_dst_article(conn: sqlite3.Connection, dst_tweet_id: str) -> int | None:
    """Return article_id for an article whose tweet_meta.tweet_id matches dst_tweet_id."""
    row = conn.execute(
        "SELECT id FROM articles WHERE json_extract(tweet_meta, '$.tweet_id') = ?",
        (dst_tweet_id,),
    ).fetchone()
    return row["id"] if row else None


def sync_tweet_links(conn: sqlite3.Connection, article_id: int, tweet_meta: dict) -> dict:
    """Insert tweet_links rows for quoted and reply edges in tweet_meta.

    Returns counts: {"quoted": N, "reply": N}.
    """
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    counts = {"quoted": 0, "reply": 0}

    # Quoted tweet edge
    quoted = tweet_meta.get("quoted_tweet") or {}
    if isinstance(quoted, dict):
        dst_id = str(quoted.get("tweet_id") or "").strip()
        if dst_id:
            dst_article_id = _resolve_dst_article(conn, dst_id)
            conn.execute(
                """INSERT OR IGNORE INTO tweet_links (src_article_id, dst_tweet_id, dst_article_id, kind, created_at)
                   VALUES (?, ?, ?, 'quoted', ?)""",
                (article_id, dst_id, dst_article_id, now),
            )
            counts["quoted"] += 1

    # Reply edge
    reply_id = str(tweet_meta.get("in_reply_to_status_id") or "").strip()
    if reply_id:
        dst_article_id = _resolve_dst_article(conn, reply_id)
        conn.execute(
            """INSERT OR IGNORE INTO tweet_links (src_article_id, dst_tweet_id, dst_article_id, kind, created_at)
               VALUES (?, ?, ?, 'reply', ?)""",
            (article_id, reply_id, dst_article_id, now),
        )
        counts["reply"] += 1

    conn.commit()
    return counts


def resolve_dangling_tweet_links(conn: sqlite3.Connection, article_id: int, tweet_id: str) -> int:
    """When a new article with `tweet_id` is stored, fill in dst_article_id for dangling links."""
    if not tweet_id:
        return 0
    cursor = conn.execute(
        "UPDATE tweet_links SET dst_article_id=? WHERE dst_tweet_id=? AND dst_article_id IS NULL",
        (article_id, tweet_id),
    )
    conn.commit()
    return cursor.rowcount


def _article_rows_for_ids(conn: sqlite3.Connection, ids: list[int]) -> list[dict]:
    if not ids:
        return []
    ph = ",".join("?" * len(ids))
    rows = conn.execute(
        f"SELECT id, url, title, tweet_meta, published_at FROM articles WHERE id IN ({ph})",
        ids,
    ).fetchall()
    return [dict(r) for r in rows]


def get_quoted_by(conn: sqlite3.Connection, article_id: int) -> list[dict]:
    """Articles that quote article_id."""
    rows = conn.execute(
        """SELECT tl.src_article_id, a.url, a.title, a.tweet_meta, a.published_at
           FROM tweet_links tl
           JOIN articles a ON a.id = tl.src_article_id
           WHERE tl.dst_article_id = ? AND tl.kind = 'quoted'""",
        (article_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_replied_by(conn: sqlite3.Connection, article_id: int) -> list[dict]:
    """Articles that reply to article_id."""
    rows = conn.execute(
        """SELECT tl.src_article_id, a.url, a.title, a.tweet_meta, a.published_at
           FROM tweet_links tl
           JOIN articles a ON a.id = tl.src_article_id
           WHERE tl.dst_article_id = ? AND tl.kind = 'reply'""",
        (article_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_article_tweet_links(conn: sqlite3.Connection, article_id: int) -> dict:
    """Return outbound (what this tweet quotes/replies) and inbound (who quotes/replies to it)."""
    outbound = conn.execute(
        """SELECT tl.dst_tweet_id, tl.dst_article_id, tl.kind,
                  a.url, a.title, a.tweet_meta
           FROM tweet_links tl
           LEFT JOIN articles a ON a.id = tl.dst_article_id
           WHERE tl.src_article_id = ?""",
        (article_id,),
    ).fetchall()
    quoted_by = get_quoted_by(conn, article_id)
    replied_by = get_replied_by(conn, article_id)
    return {
        "outbound": [dict(r) for r in outbound],
        "quoted_by": quoted_by,
        "replied_by": replied_by,
    }


def _window_since(window: str) -> str | None:
    """Convert '365d'/'180d'/'90d'/'all' to an ISO date string or None."""
    if not window or window == "all":
        return None
    try:
        days = int(window.rstrip("d"))
        from datetime import timezone
        return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    except ValueError:
        return None


def _entity_articles(conn: sqlite3.Connection, entity_id: int, since: str | None) -> list[int]:
    """Return article IDs linked to entity_id, optionally filtered by date."""
    date_filter = ""
    params: list = [entity_id]
    if since:
        date_filter = "AND COALESCE(a.published_at, a.fetched_at) >= ?"
        params.append(since)
    rows = conn.execute(
        f"""SELECT ae.article_id FROM article_entities ae
            JOIN articles a ON a.id = ae.article_id
            WHERE ae.entity_id = ? AND a.status = 'ok' {date_filter}""",
        params,
    ).fetchall()
    return [r[0] for r in rows]


def _entity_sentiment(conn: sqlite3.Connection, entity_id: int, since: str | None) -> str:
    """Return majority sentiment ('bullish'/'bearish'/'neutral') for an entity."""
    date_filter = ""
    params: list = [entity_id]
    if since:
        date_filter = "AND COALESCE(a.published_at, a.fetched_at) >= ?"
        params.append(since)
    row = conn.execute(
        f"""SELECT enr.sentiment, COUNT(*) AS c
            FROM article_entities ae
            JOIN articles a ON a.id = ae.article_id
            JOIN article_enrichments enr ON enr.article_id = ae.article_id
            WHERE ae.entity_id = ? AND enr.sentiment IS NOT NULL AND a.status = 'ok' {date_filter}
            GROUP BY enr.sentiment ORDER BY c DESC LIMIT 1""",
        params,
    ).fetchone()
    return row[0] if row else "neutral"


def _entities_in_articles(
    conn: sqlite3.Connection, article_ids: list[int]
) -> list[tuple[int, str, str]]:
    """Return (entity_id, name, kind) for all entities in the given article IDs."""
    if not article_ids:
        return []
    ph = ",".join("?" * len(article_ids))
    rows = conn.execute(
        f"""SELECT DISTINCT ae.entity_id, e.name, e.kind
            FROM article_entities ae
            JOIN entities e ON e.id = ae.entity_id
            WHERE ae.article_id IN ({ph}) AND e.canonical_id IS NULL""",
        article_ids,
    ).fetchall()
    return [(r[0], r[1], r[2]) for r in rows]


def _cooccurrence_count(
    conn: sqlite3.Connection, entity_a: int, entity_b: int, since: str | None
) -> list[dict]:
    """Return (count, top3 articles) for articles containing both entity_a and entity_b."""
    date_filter = ""
    params: list = [entity_a, entity_b]
    if since:
        date_filter = "AND COALESCE(a.published_at, a.fetched_at) >= ?"
        params.append(since)
    rows = conn.execute(
        f"""SELECT a.id, a.url, a.title, a.tweet_meta,
                   CAST(COALESCE(json_extract(a.tweet_meta,'$.favorite_count'),0) AS INTEGER)
                   + 5 * CAST(COALESCE(json_extract(a.tweet_meta,'$.retweet_count'),0) AS INTEGER)
                   AS score
            FROM article_entities ae1
            JOIN article_entities ae2 ON ae2.article_id = ae1.article_id
            JOIN articles a ON a.id = ae1.article_id
            WHERE ae1.entity_id = ? AND ae2.entity_id = ? AND a.status = 'ok' {date_filter}
            ORDER BY score DESC""",
        params,
    ).fetchall()
    top3 = []
    for r in rows[:3]:
        meta = None
        try:
            import json as _j
            meta = _j.loads(r["tweet_meta"]) if r["tweet_meta"] else None
        except (ValueError, TypeError):
            pass
        top3.append({
            "article_id": r["id"],
            "url": r["url"],
            "title": r["title"] or (meta or {}).get("text", ""),
        })
    return {"count": len(rows), "supporting_articles": top3}


def get_entity_graph(
    conn: sqlite3.Connection,
    seed_name: str,
    depth: int = 2,
    window: str = "all",
    min_edge_weight: int = 3,
    max_nodes: int = 30,
) -> dict | None:
    """Build a node-link co-occurrence graph seeded from one entity name.

    Returns None when the seed entity is not found.
    """
    # Resolve seed entity (case-insensitive)
    norm = seed_name.strip().lower()
    row = conn.execute(
        "SELECT id, name, kind FROM entities WHERE normalized_name = ? AND canonical_id IS NULL",
        (norm,),
    ).fetchone()
    if not row:
        # Try partial match
        row = conn.execute(
            "SELECT id, name, kind FROM entities WHERE normalized_name LIKE ? AND canonical_id IS NULL LIMIT 1",
            (f"%{norm}%",),
        ).fetchone()
    if not row:
        return None

    since = _window_since(window)
    seed_id, seed_label, seed_kind = row["id"], row["name"], row["kind"]

    nodes: dict[int, dict] = {}
    edges: dict[tuple[int, int], dict] = {}
    visited: set[int] = {seed_id}
    frontier: list[int] = [seed_id]

    seed_articles = _entity_articles(conn, seed_id, since)
    nodes[seed_id] = {
        "id": seed_label,
        "kind": seed_kind,
        "size": len(seed_articles),
        "sentiment": _entity_sentiment(conn, seed_id, since),
    }

    for _depth in range(depth):
        next_frontier: list[int] = []
        for fid in frontier:
            f_articles = _entity_articles(conn, fid, since)
            neighbors = _entities_in_articles(conn, f_articles)
            for nid, nname, nkind in neighbors:
                if nid == fid:
                    continue
                if len(nodes) >= max_nodes:
                    break
                edge_key = (min(fid, nid), max(fid, nid))
                edge_qualifies = False
                if edge_key not in edges:
                    coo = _cooccurrence_count(conn, fid, nid, since)
                    if coo["count"] >= min_edge_weight:
                        edge_qualifies = True
                        edges[edge_key] = {
                            "source": nodes.get(fid, {}).get("id", str(fid)),
                            "target": nname,
                            "weight": coo["count"],
                            "kind": "cooccurrence",
                            "supporting_articles": coo["supporting_articles"],
                        }
                else:
                    edge_qualifies = True
                # Only add node if it has at least one qualifying edge
                if edge_qualifies and nid not in visited:
                    visited.add(nid)
                    n_articles = _entity_articles(conn, nid, since)
                    nodes[nid] = {
                        "id": nname,
                        "kind": nkind,
                        "size": len(n_articles),
                        "sentiment": _entity_sentiment(conn, nid, since),
                    }
                    if _depth + 1 < depth:
                        next_frontier.append(nid)
            if len(nodes) >= max_nodes:
                break
        frontier = next_frontier

    return {
        "nodes": list(nodes.values()),
        "edges": [e for e in edges.values() if e["weight"] >= min_edge_weight],
        "seed": seed_label,
        "window": window,
    }


def get_articles_for_entity(
    conn: sqlite3.Connection,
    entity_id: int,
    page: int = 1,
    page_size: int = 20,
    order: str = "date",
) -> dict:
    """Paginated articles linked to entity_id (via article_entities).

    Follows canonical_id so merging an alias still shows all articles.
    order: 'date' | 'engagement'
    """
    # Resolve all entity IDs in the canonical group (the entity + its aliases)
    canon_row = conn.execute(
        "SELECT COALESCE(canonical_id, id) AS canon FROM entities WHERE id=?", (entity_id,)
    ).fetchone()
    if not canon_row:
        return {"total": 0, "page": page, "page_size": page_size, "articles": []}
    canon_id = canon_row["canon"]
    member_ids = [canon_id] + [
        r["id"] for r in conn.execute(
            "SELECT id FROM entities WHERE canonical_id=?", (canon_id,)
        ).fetchall()
    ]
    ph = ",".join("?" * len(member_ids))

    if order == "engagement":
        order_sql = (
            "CAST(COALESCE(json_extract(a.tweet_meta,'$.favorite_count'),0) AS INTEGER)"
            " + 5 * CAST(COALESCE(json_extract(a.tweet_meta,'$.retweet_count'),0) AS INTEGER) DESC"
        )
    else:
        order_sql = "COALESCE(a.published_at, a.fetched_at) DESC"

    total = conn.execute(
        f"SELECT COUNT(DISTINCT ae.article_id) FROM article_entities ae WHERE ae.entity_id IN ({ph})",
        member_ids,
    ).fetchone()[0]

    offset = (page - 1) * page_size
    rows = conn.execute(
        f"""SELECT DISTINCT a.*, ae.assigned_by
            FROM article_entities ae
            JOIN articles a ON a.id = ae.article_id
            WHERE ae.entity_id IN ({ph}) AND a.status = 'ok'
            ORDER BY {order_sql}
            LIMIT ? OFFSET ?""",
        member_ids + [page_size, offset],
    ).fetchall()

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "articles": [dict(r) for r in rows],
    }
