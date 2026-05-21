import json
from typing import Literal

import httpx
from pydantic import BaseModel, ValidationError, field_validator

_DEFAULT_OLLAMA_URL = "http://localhost:11434"
_DEFAULT_MODEL = "qwen2.5:3b"

_SYSTEM_PROMPT = """\
You are an expert financial and news analyst. Given an article title and body, return a JSON object with exactly these fields:
- "summary": string, 2-4 sentences capturing the key facts
- "categories": array of strings (1-4 tags from: finance, politics, technology, energy, markets, crypto, macro, geopolitics, health, environment, other)
- "suggested_new_category": string or null — only if none of the above fit
- "entities": array of objects with EXACTLY these keys:
    - "name": canonical entity name
    - "kind": MUST be one of these four literal strings — "company", "ticker", "person", "other". Do NOT use any other value. Map organizations, agencies, governments, central banks, ETFs, indices, funds, and NGOs to "company". Stock symbols to "ticker". Individuals to "person". Anything that doesn't fit those three → "other". NEVER use "organization", "org", "fund", "government", or any other value.
    - "mention_text": the surface text as it appears in the article
- "sentiment": one of "bullish", "bearish", "neutral", "n/a"

Return ONLY valid JSON. No prose, no markdown fences.
"""

# Common kind variants the small Ollama model emits that we want to soft-normalize
# rather than reject. The reviewer-flagged organization=>company drift is the canonical
# case but other plausible ones get the same treatment.
_KIND_ALIASES = {
    "organization": "company",
    "organisation": "company",
    "org": "company",
    "corp": "company",
    "corporation": "company",
    "business": "company",
    "firm": "company",
    "fund": "company",
    "etf": "company",
    "index": "company",
    "bank": "company",
    "central_bank": "company",
    "agency": "other",
    "government": "other",
    "gov": "other",
    "nation": "other",
    "country": "other",
    "ngo": "other",
    "institution": "other",
    "place": "other",
    "location": "other",
    "event": "other",
    "product": "other",
    "concept": "other",
    "symbol": "ticker",
    "stock": "ticker",
}


class Entity(BaseModel):
    name: str
    kind: Literal["company", "ticker", "person", "other"]
    # mention_text is REQUESTED required by the prompt, but the small model sometimes
    # omits it — we tolerate that with a default to avoid the whole EnrichResult
    # failing pydantic validation. The post-validator fills it from `name` if missing
    # or empty. Same fail-soft pattern as the kind normalization below.
    mention_text: str = ""

    @field_validator("kind", mode="before")
    @classmethod
    def normalize_kind(cls, v):
        """Map small-model drift (e.g. 'organization') to the canonical four kinds.

        Without this normalization, every entity tagged 'organization' fails the Literal
        check and the whole EnrichResult rejects after retry — the entire enrich loop
        becomes a no-op. We tolerate drift here so the entities table doesn't fragment.
        """
        if not isinstance(v, str):
            return v
        key = v.lower().strip().replace("-", "_").replace(" ", "_")
        if key in _KIND_ALIASES:
            return _KIND_ALIASES[key]
        # Unknown kinds → 'other' rather than reject the whole enrichment.
        if key not in ("company", "ticker", "person", "other"):
            return "other"
        return key

    @field_validator("mention_text", mode="before")
    @classmethod
    def default_mention_text(cls, v, info):
        """When the small model omits `mention_text`, fall back to `name`.

        Pydantic v2 passes other-field values via `info.data` on field_validators
        with mode='before' — `name` is validated first because it's declared first.
        """
        if v is None or (isinstance(v, str) and not v.strip()):
            return (info.data.get("name") or "").strip() or ""
        return v


class EnrichResult(BaseModel):
    summary: str
    categories: list[str]
    suggested_new_category: str | None = None
    entities: list[Entity]
    sentiment: Literal["bullish", "bearish", "neutral", "n/a"]


class EnrichError(Exception):
    pass


def _build_tweet_meta_block(meta: dict) -> str:
    """Return a structured context block from tweet_meta, skipping empty fields."""
    lines = []
    author_name = meta.get("author_name") or ""
    author_handle = meta.get("author_handle") or ""
    verified = meta.get("is_verified") or meta.get("verified") or False
    if author_name or author_handle:
        parts = []
        if author_name:
            parts.append(author_name)
        if author_handle:
            parts.append(f"(@{author_handle})")
        if verified:
            parts.append("[verified]")
        lines.append(f"- Author: {' '.join(parts)}")
    created_at = meta.get("created_at") or ""
    if created_at:
        lines.append(f"- Posted: {created_at}")
    fav = meta.get("favorite_count") or 0
    rt = meta.get("retweet_count") or 0
    views = meta.get("view_count") or meta.get("views") or 0
    engagement_parts = []
    if fav:
        engagement_parts.append(f"{fav} likes")
    if rt:
        engagement_parts.append(f"{rt} retweets")
    if views:
        engagement_parts.append(f"{views} views")
    if engagement_parts:
        lines.append(f"- Engagement: {', '.join(engagement_parts)}")
    hashtags = meta.get("hashtags") or []
    if hashtags:
        lines.append(f"- Hashtags: {' '.join('#' + h.lstrip('#') for h in hashtags)}")
    mentions = meta.get("mentioned_handles") or meta.get("mentions") or []
    if mentions:
        lines.append(f"- Mentions: {' '.join('@' + m.lstrip('@') for m in mentions)}")
    tickers = meta.get("tickers") or []
    if tickers:
        lines.append(f"- Tickers: {' '.join('$' + t.lstrip('$') for t in tickers)}")
    quoted = meta.get("quoted_text") or meta.get("quoted_tweet_text") or ""
    if quoted:
        lines.append(f'- Quoted tweet: "{quoted[:300]}"')
    if not lines:
        return ""
    return "Tweet metadata (deterministic, trust this over guessing):\n" + "\n".join(lines)


def enrich_article(
    article: dict,
    ollama_url: str = _DEFAULT_OLLAMA_URL,
    model: str = _DEFAULT_MODEL,
    tweet_meta: dict | None = None,
) -> EnrichResult:
    title = article.get("title") or ""
    text = (article.get("raw_text") or "")[:4000]
    meta_block = _build_tweet_meta_block(tweet_meta) if tweet_meta else ""
    if meta_block:
        user_content = f"{meta_block}\n\nTitle: {title}\n\n{text}"
    else:
        user_content = f"Title: {title}\n\n{text}"

    def _call() -> dict:
        resp = httpx.post(
            f"{ollama_url}/api/chat",
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": user_content},
                ],
                "format": "json",
                "stream": False,
                "options": {"temperature": 0.2},
            },
            timeout=120,
        )
        resp.raise_for_status()
        content = resp.json()["message"]["content"]
        return json.loads(content)

    last_exc: Exception | None = None
    for _ in range(2):
        try:
            raw = _call()
            return EnrichResult.model_validate(raw)
        except (ValidationError, KeyError, json.JSONDecodeError, httpx.HTTPError) as exc:
            last_exc = exc

    raise EnrichError(f"Enrichment failed after 2 attempts: {last_exc}") from last_exc
