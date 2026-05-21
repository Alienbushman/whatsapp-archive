"""Render an ad-hoc group of tweet-articles into various export formats."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any


_PROMPT_TASK = {
    "analyse": (
        "Identify the key claims, evidence, and contradictions across these tweets. "
        "Group related claims together, note where authors agree vs. disagree, and flag "
        "any claims that appear unsupported."
    ),
    "summarise": (
        "Summarise the conversation these tweets represent in 3-5 bullets. "
        "Capture the main theme, key voices, and any consensus or tension."
    ),
    "extract_claims": (
        "Extract every factual claim made across these tweets. "
        "List each claim with its [N] citation. Note disagreements between claims."
    ),
}


def _ts(article: dict) -> datetime:
    raw = article.get("published_at") or article.get("fetched_at") or ""
    try:
        return datetime.fromisoformat(raw[:19])
    except (ValueError, TypeError):
        return datetime.utcnow()


def _meta(article: dict) -> dict[str, Any]:
    # _row_to_dict (API layer) deserialized tweet_meta → "tweet"; accept both
    return article.get("tweet") or article.get("tweet_meta") or {}


def _tweet_block(n: int, article: dict, max_chars: int) -> list[str]:
    meta: dict[str, Any] = _meta(article)
    handle = meta.get("author_handle") or meta.get("author_name") or article.get("url", "")
    display = meta.get("author_name") or ""
    ts = _ts(article).strftime("%Y-%m-%d %H:%M")
    likes = meta.get("favorite_count") or meta.get("total_favorites") or 0
    rts = meta.get("retweet_count") or meta.get("total_retweets") or 0
    text = (meta.get("text") or article.get("raw_text") or article.get("title") or "")[:max_chars]

    header_parts = [f"@{handle}", f"({ts})"]
    if display and display != handle:
        header_parts.append(f"· {display}")
    if likes or rts:
        header_parts.append(f"· likes: {likes} · RT: {rts}")

    lines = [f"[{n}] {' '.join(header_parts)}", f"    {text}"]

    hashtags = meta.get("hashtags") or []
    mentions = meta.get("mentioned_handles") or []
    tickers = meta.get("tickers") or []
    if hashtags or mentions or tickers:
        tags_parts = []
        if hashtags:
            tags_parts.append("hashtags: " + " ".join(f"#{h}" for h in hashtags[:10]))
        if mentions:
            tags_parts.append("mentions: " + " ".join(f"@{m}" for m in mentions[:10]))
        if tickers:
            tags_parts.append("tickers: " + " ".join(f"${t}" for t in tickers[:10]))
        lines.append("    " + " · ".join(tags_parts))

    quoted = meta.get("quoted_tweet") or {}
    if quoted:
        qtext = (quoted.get("text") or "")[:200]
        qhandle = quoted.get("author_handle") or ""
        lines.append(f'    quoted: "{qtext}" — @{qhandle}')

    return lines


def render_group_bundle(
    articles: list[dict],
    fmt: str = "md",
    prompt_template: str = "analyse",
    custom_prompt: str | None = None,
    max_tweet_chars: int = 500,
    similar_context: list[dict] | None = None,
) -> str:
    articles_sorted = sorted(articles, key=_ts)
    n = len(articles_sorted)
    today = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    if fmt == "json":
        payload = {
            "generated_at": today,
            "count": n,
            "tweets": [
                {
                    "index": i + 1,
                    "article_id": a.get("id"),
                    "url": a.get("url"),
                    "published_at": a.get("published_at"),
                    "tweet_meta": _meta(a),
                    "title": a.get("title"),
                    "raw_text": (a.get("raw_text") or "")[:max_tweet_chars],
                    "summary": a.get("summary"),
                }
                for i, a in enumerate(articles_sorted)
            ],
        }
        if similar_context:
            payload["related_context"] = similar_context
        return json.dumps(payload, default=str, ensure_ascii=False, indent=2)

    if fmt == "prompt":
        task_instr = custom_prompt or _PROMPT_TASK.get(prompt_template, _PROMPT_TASK["analyse"])
        dates = [_ts(a) for a in articles_sorted]
        from_dt = min(dates).strftime("%Y-%m-%d") if dates else "?"
        to_dt = max(dates).strftime("%Y-%m-%d") if dates else "?"
        chats: set[str] = set()
        for a in articles_sorted:
            if a.get("chat_name"):
                chats.add(a["chat_name"])
        chats_str = ", ".join(sorted(chats)) or "unknown"

        lines = [
            "You are an analyst. The following tweets were grouped together from a financial-info",
            f"group chat archive because the user identified them as related.",
            f"They span {from_dt} to {to_dt} and were shared in: {chats_str}.",
            "",
            f"Your task: {task_instr}",
            "",
            "Constraints:",
            "- Cite specific tweets by their [N] number.",
            "- Do not invent claims. If something isn't in the bundle, say so.",
            "",
            f"TWEETS ({n} total):",
            "",
        ]
        for i, a in enumerate(articles_sorted):
            lines.extend(_tweet_block(i + 1, a, max_tweet_chars))
            lines.append("")
        if similar_context:
            lines += ["", "RELATED CONTEXT (similar tweets not in the main bundle):", ""]
            for i, sc in enumerate(similar_context):
                lines.extend(_tweet_block(f"C{i+1}", sc, max_tweet_chars))
                lines.append("")
        return "\n".join(lines)

    if fmt == "txt":
        lines = [
            f"Group bundle: {n} tweets",
            f"Generated {today}",
            "",
        ]
        for i, a in enumerate(articles_sorted):
            lines.append(f"--- [{i+1}] ---")
            lines.extend(_tweet_block(i + 1, a, max_tweet_chars))
            lines.append("")
        if similar_context:
            lines += ["--- RELATED CONTEXT ---", ""]
            for i, sc in enumerate(similar_context):
                lines.extend(_tweet_block(f"C{i+1}", sc, max_tweet_chars))
                lines.append("")
        return "\n".join(lines)

    # md (default)
    short_hash = hashlib.md5(",".join(str(a.get("id", "")) for a in articles_sorted).encode()).hexdigest()[:6]
    lines = [
        f"# Group bundle: {n} tweets",
        f"_Generated {today} · hash: {short_hash}_",
        "",
    ]
    for i, a in enumerate(articles_sorted):
        meta: dict = _meta(a)
        handle = meta.get("author_handle") or ""
        ts = _ts(a).strftime("%Y-%m-%d %H:%M")
        text = (meta.get("text") or a.get("raw_text") or a.get("title") or "")[:max_tweet_chars]
        lines.append(f"## [{i+1}] @{handle} · {ts}")
        lines.append("")
        lines.append(text)
        if a.get("url"):
            lines.append("")
            lines.append(f"[Source]({a['url']})")
        if a.get("summary"):
            lines.append("")
            lines.append(f"> {a['summary']}")
        lines.append("")
    if similar_context:
        lines += ["---", "## Related context", ""]
        for sc in similar_context:
            meta = _meta(sc)
            handle = meta.get("author_handle") or ""
            ts = _ts(sc).strftime("%Y-%m-%d %H:%M")
            text = (meta.get("text") or sc.get("raw_text") or "")[:max_tweet_chars]
            score = sc.get("similarity_score", 0)
            lines.append(f"**@{handle}** · {ts} · score {score:.2f}")
            lines.append("")
            lines.append(text)
            lines.append("")
    return "\n".join(lines)


def bundle_filename(fmt: str) -> str:
    ext = {"json": "json", "txt": "txt", "prompt": "txt", "md": "md"}.get(fmt, "md")
    today = datetime.utcnow().strftime("%Y%m%d")
    return f"group-{today}.{ext}"
