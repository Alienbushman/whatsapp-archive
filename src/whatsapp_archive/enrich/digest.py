"""Daily/weekly digest generation via Ollama."""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import date, datetime, timedelta, timezone

import httpx

logger = logging.getLogger(__name__)

_GEN_MODEL = "qwen2.5:3b-instruct"
_MAX_CONTEXT_CHARS = 8000

_DIGEST_SYSTEM = """\
You are summarising a financial-info group chat. Below are the messages and linked articles \
from the given period. Produce a Markdown digest with exactly these 4 sections:

# Headlines
- 3-5 bullet points, each capturing a major topic or market development discussed.

# Top discussions
- Per-chat or per-topic grouping, 1-3 bullets each.

# Notable links
- List the most significant articles/tweets shared, one per bullet, with a brief note.

# Sentiment summary
- Overall mood (bullish/bearish/mixed), 1-2 sentences.

Be concise. Cite sources by author and date where helpful.
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _period_dates(period: str, end_date: date) -> tuple[str, str]:
    """Return (period_start, period_end) as ISO date strings."""
    if period == "weekly":
        start = end_date - timedelta(days=7)
    else:  # daily
        start = end_date - timedelta(days=1)
    return start.isoformat(), end_date.isoformat()


def _build_context(
    db: sqlite3.Connection,
    chats: dict,
    period_start: str,
    period_end: str,
    chat_id: str | None,
) -> tuple[str, list[dict]]:
    """Build a text context block and citations list for the digest."""
    from ..scrape.db import get_articles_in_timerange

    parts: list[str] = []
    citations: list[dict] = []
    chars_used = 0

    # Messages from chats in date window
    for cid, chat in chats.items():
        if chat_id and cid != chat_id:
            continue
        # chats values are ChatExport Pydantic objects with an .entries attribute
        entries = getattr(chat, "entries", None) or []
        for msg in entries:
            if not hasattr(msg, "body"):
                continue  # skip SystemEvent (no body attribute)
            ts_str = msg.timestamp.isoformat() if msg.timestamp else ""
            if ts_str < period_start or ts_str > period_end + "T23:59:59":
                continue
            line = f"[{ts_str[:16]} {msg.sender}] {msg.body}"
            if chars_used + len(line) > _MAX_CONTEXT_CHARS:
                break
            parts.append(line)
            chars_used += len(line)

    parts.append("\n--- Linked articles ---")

    # Articles in date window
    rows = get_articles_in_timerange(db, from_date=period_start, to_date=period_end)
    for row in rows:
        try:
            tweet_meta = json.loads(row["tweet_meta"]) if row["tweet_meta"] else None
        except (ValueError, TypeError):
            tweet_meta = None

        url = row["url"] or ""
        date_str = (row["published_at"] or row["fetched_at"] or "")[:10]

        if tweet_meta:
            author = tweet_meta.get("author_handle") or tweet_meta.get("author_name") or "unknown"
            tweet_text = (tweet_meta.get("text") or row["raw_text"] or "")[:280]
            likes = tweet_meta.get("favorite_count") or 0
            rts = tweet_meta.get("retweet_count") or 0
            tags = " ".join(f"#{h}" for h in (tweet_meta.get("hashtags") or [])[:5])
            tickers = " ".join(f"${t}" for t in (tweet_meta.get("tickers") or [])[:5])
            engagement = f"{likes} likes · {rts} RTs" if likes or rts else ""
            meta_line = " · ".join(filter(None, [f"@{author}", date_str, engagement]))
            tag_line = " ".join(filter(None, [tags, tickers]))
            chunk = f"\n**{meta_line}**\n> {tweet_text}"
            if tag_line:
                chunk += f"\n{tag_line}"
            chunk += f"\n[source ↗]({url})"
            snippet = tweet_text[:120]
        else:
            title = row["title"] or url
            raw = (row["raw_text"] or "")[:300]
            chunk = f"\n[Article: {title}]({url}) {raw}"
            snippet = raw[:120]

        if chars_used + len(chunk) > _MAX_CONTEXT_CHARS:
            break
        parts.append(chunk)
        chars_used += len(chunk)
        citations.append({
            "article_id": row["id"],
            "url": url,
            "snippet": snippet,
            "author": tweet_meta.get("author_handle") if tweet_meta else None,
            "date": date_str,
            "tweet_text": (tweet_meta.get("text") or "")[:280] if tweet_meta else None,
            "likes": tweet_meta.get("favorite_count", 0) if tweet_meta else 0,
            "hashtags": (tweet_meta.get("hashtags") or [])[:5] if tweet_meta else [],
        })

    return "\n".join(parts), citations


def _call_ollama(ollama_url: str, context: str, period_start: str, period_end: str, model: str) -> str:
    user_msg = f"Period: {period_start} to {period_end}\n\n{context}"
    resp = httpx.post(
        f"{ollama_url}/api/chat",
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": _DIGEST_SYSTEM},
                {"role": "user", "content": user_msg},
            ],
            "stream": False,
            "options": {"temperature": 0.3},
        },
        timeout=180,
    )
    resp.raise_for_status()
    return resp.json()["message"]["content"]


def generate_digest(
    db: sqlite3.Connection,
    ollama_url: str,
    period: str,
    chats: dict,
    end_date: date | None = None,
    chat_id: str | None = None,
    model: str = _GEN_MODEL,
) -> dict:
    """Generate (or return cached) digest for the given period.

    Returns a dict with all digest fields.
    """
    if end_date is None:
        end_date = datetime.now(timezone.utc).date()

    period_start, period_end = _period_dates(period, end_date)

    # Return existing row if already generated for this window
    existing = db.execute(
        "SELECT * FROM digests WHERE period = ? AND period_start = ? AND chat_id IS ?",
        (period, period_start, chat_id),
    ).fetchone()
    if existing:
        return dict(existing)

    context, citations = _build_context(db, chats, period_start, period_end, chat_id)

    try:
        body_md = _call_ollama(ollama_url, context, period_start, period_end, model)
    except Exception as exc:
        logger.warning("digest generation failed: %s", exc)
        body_md = f"_Digest generation failed: {exc}_"

    now = _now_iso()
    db.execute(
        "INSERT OR IGNORE INTO digests (period, period_start, period_end, chat_id, body_md, citations, model, generated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (period, period_start, period_end, chat_id, body_md, json.dumps(citations), model, now),
    )
    db.commit()

    row = db.execute(
        "SELECT * FROM digests WHERE period = ? AND period_start = ? AND chat_id IS ?",
        (period, period_start, chat_id),
    ).fetchone()
    return dict(row)
