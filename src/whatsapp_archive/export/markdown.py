from datetime import datetime


def render_keyword_bundle(
    query: str,
    msg_hits: list[dict],
    article_hits: list[dict],
    url_to_article: dict[str, dict],
    generated_at: str | None = None,
) -> str:
    if generated_at is None:
        generated_at = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    lines: list[str] = [
        f'# Keyword bundle: "{query}"',
        f"_Generated {generated_at} · "
        f"{len(msg_hits)} message{'s' if len(msg_hits) != 1 else ''} · "
        f"{len(article_hits)} article{'s' if len(article_hits) != 1 else ''}_",
        "",
    ]

    all_items: list[dict] = []
    for m in msg_hits:
        all_items.append({"kind": "message", **m})
    for a in article_hits:
        if a.get("published_at"):
            try:
                ts = datetime.fromisoformat(a["published_at"][:19])
            except ValueError:
                ts = datetime.utcnow()
        else:
            ts = datetime.utcnow()
        all_items.append({"kind": "article", "ts": ts, **a})

    all_items.sort(key=lambda x: x.get("ts") or datetime.utcnow())

    for item in all_items:
        lines.append("---")
        if item["kind"] == "message":
            ts = item.get("ts") or datetime.utcnow()
            lines.append(
                f"## {ts.strftime('%Y-%m-%d %H:%M')} · {item.get('chat_name', '')} · {item.get('sender', '')}"
            )
            lines.append("")
            lines.append(item.get("body", ""))
            url_in_body = next((u for u in url_to_article if u in (item.get("body") or "")), None)
            if url_in_body:
                art = url_to_article[url_in_body]
                summary = art.get("summary") or (art.get("raw_text") or "")[:300]
                lines.append("")
                lines.append(f"[Source link]({url_in_body}) — *{art.get('title', '')}*")
                lines.append(f"> {summary}")
        else:
            ts = item.get("ts") or datetime.utcnow()
            lines.append(
                f"## {ts.strftime('%Y-%m-%d %H:%M')} · Article · {item.get('title', '')}"
            )
            lines.append("")
            lines.append(f"URL: {item.get('url', '')}")
            summary = item.get("summary") or (item.get("raw_text") or "")[:300]
            if summary:
                lines.append("")
                lines.append(f"> {summary}")
        lines.append("")

    return "\n".join(lines)
