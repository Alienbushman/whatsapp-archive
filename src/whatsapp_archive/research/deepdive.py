"""R5 — Structured deep-dive research report for a single entity.

Generates 5 structured sections via separate Ollama calls:
  1. Claims    — specific factual assertions with article citations
  2. Authors   — top authors with stance + sample quote
  3. Time arc  — inflection points in the narrative
  4. Contradictions — conflicting claim pairs with citations
  5. Open questions — analyst gaps not addressed in the archive

Results are persisted to deepdive_reports (7-day cache).
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from ..research.dossier import _get_entity_canonical, _get_all_entity_ids

_OLLAMA_MODEL = "qwen2.5:3b-instruct"
_CACHE_HOURS = 168  # 7 days
_MAX_ARTICLES = 40

# Patterns that indicate the model drifted into summary mode instead of claim extraction
_SUMMARY_PATTERNS = re.compile(
    r"\b(overall(?=[,\s])|in\s+summary|in\s+conclusion|the\s+consensus|most\s+people|"
    r"generally\s+speaking|sentiment\s+is\s+(bullish|bearish)|to\s+summarize)\b",
    re.IGNORECASE,
)


def _has_summary_language(text: str) -> bool:
    return bool(_SUMMARY_PATTERNS.search(text))


# ── Cache ──────────────────────────────────────────────────────────────────────

def get_deepdive_cache(conn: sqlite3.Connection, entity_name: str) -> dict | None:
    row = conn.execute(
        "SELECT body_json, generated_at FROM deepdive_reports WHERE entity_name=?",
        (entity_name.strip().lower(),),
    ).fetchone()
    if not row:
        return None
    ts = datetime.fromisoformat(row["generated_at"])
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) - ts > timedelta(hours=_CACHE_HOURS):
        return None
    return json.loads(row["body_json"])


def save_deepdive_cache(conn: sqlite3.Connection, entity_name: str, report: dict, model: str) -> None:
    conn.execute(
        """INSERT INTO deepdive_reports (entity_name, body_json, model, generated_at)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(entity_name) DO UPDATE SET
             body_json=excluded.body_json, model=excluded.model, generated_at=excluded.generated_at""",
        (entity_name.strip().lower(), json.dumps(report), model, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()


# ── Article retrieval ─────────────────────────────────────────────────────────

def _get_top_articles(conn: sqlite3.Connection, entity_name: str) -> tuple[list[dict], str | None, str | None]:
    """Return (articles, first_seen, last_seen) sorted by engagement desc."""
    entity = _get_entity_canonical(conn, entity_name)
    if not entity:
        return [], None, None

    all_ids = _get_all_entity_ids(conn, entity["id"])
    ph = ",".join("?" * len(all_ids))
    rows = conn.execute(
        f"""SELECT a.id, a.url, a.title, a.published_at, a.fetched_at,
                   a.tweet_meta, e.summary, e.sentiment
            FROM article_entities ae
            JOIN articles a ON a.id = ae.article_id AND a.status='ok'
            LEFT JOIN article_enrichments e ON e.article_id = a.id
            WHERE ae.entity_id IN ({ph})
            GROUP BY a.id
            ORDER BY (
                COALESCE(CAST(json_extract(a.tweet_meta,'$.favorite_count') AS INTEGER), 0) +
                COALESCE(CAST(json_extract(a.tweet_meta,'$.retweet_count') AS INTEGER), 0)
            ) DESC
            LIMIT {_MAX_ARTICLES}""",
        all_ids,
    ).fetchall()

    articles = []
    dates = []
    for r in rows:
        meta = {}
        try:
            meta = json.loads(r["tweet_meta"]) if r["tweet_meta"] else {}
        except (ValueError, TypeError):
            pass
        d = r["published_at"] or r["fetched_at"] or ""
        if d:
            dates.append(d)
        articles.append({
            "id": r["id"],
            "url": r["url"],
            "title": r["title"] or "",
            "published_at": d,
            "summary": r["summary"] or "",
            "sentiment": r["sentiment"] or "neutral",
            "handle": meta.get("author_handle") or "",
            "favorite_count": int(meta.get("favorite_count") or 0),
            "retweet_count": int(meta.get("retweet_count") or 0),
        })

    first_seen = min(dates) if dates else None
    last_seen = max(dates) if dates else None
    return articles, first_seen, last_seen


# ── LLM calls ────────────────────────────────────────────────────────────────

def _llm_json(
    ollama_url: str,
    prompt: str,
    model: str = _OLLAMA_MODEL,
    max_retries: int = 2,
) -> Any:
    """Call Ollama, parse JSON from response. Raises on hard failure."""
    for attempt in range(max_retries):
        try:
            resp = httpx.post(
                f"{ollama_url}/api/generate",
                json={"model": model, "prompt": prompt, "stream": False, "options": {"temperature": 0.2}},
                timeout=60,
            )
            resp.raise_for_status()
            text = resp.json().get("response", "").strip()
            # Extract JSON block
            m = re.search(r"(\[[\s\S]*\]|\{[\s\S]*\})", text)
            if not m:
                raise ValueError(f"No JSON found in: {text[:200]}")
            parsed = json.loads(m.group(1))
            if _has_summary_language(text) and attempt < max_retries - 1:
                # Retry with stricter prompt
                prompt = prompt + "\n\nCRITICAL: Do NOT write any summary sentences. Only structured JSON."
                continue
            return parsed
        except (httpx.HTTPError, ValueError) as exc:
            if attempt == max_retries - 1:
                raise
    raise RuntimeError("LLM call failed after retries")


def _build_article_context(articles: list[dict], max_chars: int = 8000) -> str:
    lines = []
    total = 0
    for i, a in enumerate(articles, start=1):
        line = f"[{i}] {a['title']} | {a['summary'][:200]} | {a['published_at'][:7] if a['published_at'] else '?'}"
        total += len(line)
        if total > max_chars:
            break
        lines.append(line)
    return "\n".join(lines)


# ── Section generators ────────────────────────────────────────────────────────

def _gen_claims(articles: list[dict], entity_name: str, ollama_url: str, llm_fn=None) -> list[dict]:
    ctx = _build_article_context(articles)
    prompt = (
        f"Given these articles about {entity_name}:\n\n{ctx}\n\n"
        f"List up to 10 distinct SPECIFIC factual claims made about {entity_name}. "
        "Each claim MUST cite at least one article by its [N] number. "
        "Do NOT summarize or give opinions — only extract specific factual assertions. "
        "Output ONLY a JSON array:\n"
        '[{"claim": "specific assertion", "evidence_article_ids": [1, 3], "mention_count": 2}]\n\n'
        "JSON array:"
    )
    fn = llm_fn or _llm_json
    result = fn(ollama_url, prompt)
    if not isinstance(result, list):
        result = []
    valid_ids = {a["id"] for a in articles}
    id_map = {i + 1: a["id"] for i, a in enumerate(articles)}
    for item in result:
        if not isinstance(item, dict):
            continue
        item.setdefault("claim", "")
        raw_ids = item.get("evidence_article_ids") or []
        item["evidence_article_ids"] = [
            id_map[n] for n in raw_ids if isinstance(n, int) and n in id_map
        ]
        item.setdefault("mention_count", 1)
    return [r for r in result if isinstance(r, dict) and r.get("claim")]


def _gen_authors(articles: list[dict], entity_name: str, ollama_url: str, llm_fn=None) -> list[dict]:
    by_author: dict[str, list] = {}
    for a in articles:
        h = a.get("handle")
        if h:
            by_author.setdefault(h, []).append(a)

    lines = []
    for h, arts in list(by_author.items())[:15]:
        best = sorted(arts, key=lambda x: x["favorite_count"], reverse=True)[0]
        lines.append(f"@{h} ({len(arts)} articles, best: {best['summary'][:150]})")

    if not lines:
        return []

    ctx = "\n".join(lines)
    prompt = (
        f"Based on these Twitter authors who posted about {entity_name}:\n\n{ctx}\n\n"
        "For the top 5 authors, identify their stance toward the entity and give a representative quote. "
        "Output ONLY a JSON array:\n"
        '[{"handle": "...", "stance": "bullish|bearish|neutral", "sample_quote": "...", "article_count": N}]\n\n'
        "JSON array:"
    )
    fn = llm_fn or _llm_json
    result = fn(ollama_url, prompt)
    if not isinstance(result, list):
        return []
    return result[:5]


def _gen_time_arc(articles: list[dict], entity_name: str, first_seen: str | None, last_seen: str | None, ollama_url: str, llm_fn=None) -> list[dict]:
    by_month: dict[str, list] = {}
    for a in articles:
        m = (a.get("published_at") or "")[:7]
        if m:
            by_month.setdefault(m, []).append(a)

    lines = []
    for m in sorted(by_month)[:20]:
        arts = by_month[m]
        sample = arts[0]["summary"][:150] if arts else ""
        lines.append(f"{m} ({len(arts)} articles): {sample}")

    if not lines:
        return []

    ctx = "\n".join(lines)
    span = f"{first_seen[:7] if first_seen else '?'} to {last_seen[:7] if last_seen else '?'}"
    prompt = (
        f"Based on these monthly article summaries about {entity_name} from {span}:\n\n{ctx}\n\n"
        "Identify 3-5 key inflection points in how the narrative evolved. "
        "Each inflection point must be a specific event or development, not a summary. "
        "Output ONLY a JSON array:\n"
        '[{"month": "YYYY-MM", "event": "specific development", "supporting_article_ids": [1, 2]}]\n\n'
        "JSON array:"
    )
    fn = llm_fn or _llm_json
    result = fn(ollama_url, prompt)
    if not isinstance(result, list):
        return []
    id_map = {i + 1: a["id"] for i, a in enumerate(articles)}
    for item in result:
        if not isinstance(item, dict):
            continue
        raw_ids = item.get("supporting_article_ids") or []
        item["supporting_article_ids"] = [
            id_map[n] for n in raw_ids if isinstance(n, int) and n in id_map
        ]
    return [r for r in result if isinstance(r, dict) and r.get("event")]


def _gen_contradictions(articles: list[dict], entity_name: str, ollama_url: str, llm_fn=None) -> list[dict]:
    ctx = _build_article_context(articles, max_chars=6000)
    prompt = (
        f"Based on these articles about {entity_name}:\n\n{ctx}\n\n"
        f"Identify pairs of CONFLICTING specific claims about {entity_name} from different articles. "
        "Only include genuine factual contradictions, not different opinions. "
        "Output ONLY a JSON array (empty array if no contradictions):\n"
        '[{"claim_a": "...", "claim_b": "...", "article_ids_a": [1], "article_ids_b": [3]}]\n\n'
        "JSON array:"
    )
    fn = llm_fn or _llm_json
    try:
        result = fn(ollama_url, prompt)
    except Exception:
        return []
    if not isinstance(result, list):
        return []
    id_map = {i + 1: a["id"] for i, a in enumerate(articles)}
    for item in result:
        if not isinstance(item, dict):
            continue
        for key in ("article_ids_a", "article_ids_b"):
            raw = item.get(key) or []
            item[key] = [id_map[n] for n in raw if isinstance(n, int) and n in id_map]
    return [r for r in result if isinstance(r, dict) and r.get("claim_a") and r.get("claim_b")][:5]


def _gen_open_questions(articles: list[dict], entity_name: str, ollama_url: str, llm_fn=None) -> list[str]:
    covered = "\n".join(f"- {a['summary'][:100]}" for a in articles[:20] if a["summary"])
    prompt = (
        f"An analyst is researching {entity_name} based on these covered topics:\n\n{covered}\n\n"
        "What important questions about the entity are NOT addressed in this archive? "
        "List up to 5 specific analytical gaps an investor or researcher would want answered. "
        "Output ONLY a JSON array of strings:\n"
        '["Question 1", "Question 2"]\n\n'
        "JSON array:"
    )
    fn = llm_fn or _llm_json
    try:
        result = fn(ollama_url, prompt)
    except Exception:
        return []
    if isinstance(result, list):
        return [str(q) for q in result if q][:5]
    return []


# ── Main entry point ──────────────────────────────────────────────────────────

def generate_deepdive(
    conn: sqlite3.Connection,
    entity_name: str,
    ollama_url: str,
    model: str = _OLLAMA_MODEL,
    force_refresh: bool = False,
    llm_fn=None,
) -> dict | None:
    """Generate or return cached deep-dive report for entity_name.

    `llm_fn` is injectable for tests: callable(ollama_url, prompt) -> parsed JSON.
    Returns None if entity not found.
    """
    if not force_refresh:
        cached = get_deepdive_cache(conn, entity_name)
        if cached:
            return cached

    articles, first_seen, last_seen = _get_top_articles(conn, entity_name)
    if not articles:
        return None

    report: dict[str, Any] = {
        "entity_name": entity_name,
        "article_count": len(articles),
        "first_seen": first_seen,
        "last_seen": last_seen,
        "sections": {
            "claims": [],
            "authors": [],
            "time_arc": [],
            "contradictions": [],
            "open_questions": [],
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": model,
    }

    try:
        report["sections"]["claims"] = _gen_claims(articles, entity_name, ollama_url, llm_fn)
    except Exception as exc:
        report["sections"]["claims_error"] = str(exc)

    try:
        report["sections"]["authors"] = _gen_authors(articles, entity_name, ollama_url, llm_fn)
    except Exception as exc:
        report["sections"]["authors_error"] = str(exc)

    try:
        report["sections"]["time_arc"] = _gen_time_arc(articles, entity_name, first_seen, last_seen, ollama_url, llm_fn)
    except Exception as exc:
        report["sections"]["time_arc_error"] = str(exc)

    try:
        report["sections"]["contradictions"] = _gen_contradictions(articles, entity_name, ollama_url, llm_fn)
    except Exception as exc:
        report["sections"]["contradictions_error"] = str(exc)

    try:
        report["sections"]["open_questions"] = _gen_open_questions(articles, entity_name, ollama_url, llm_fn)
    except Exception as exc:
        report["sections"]["open_questions_error"] = str(exc)

    save_deepdive_cache(conn, entity_name, report, model)
    return report
