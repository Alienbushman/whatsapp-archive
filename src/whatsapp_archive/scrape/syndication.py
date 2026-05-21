import math
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Optional

import httpx

from .article import Gone404, TransientError, _USER_AGENT
from ..enrich.tickers import extract_tickers as _extract_tickers


def _extract_tickers_safe(text: str) -> list[str]:
    try:
        return _extract_tickers(text)
    except Exception:
        return []

_TWEET_ID_RE = re.compile(r"https?://(?:www\.)?(?:x|twitter)\.com/\w+/status/(\d+)")
_SYNDICATION_URL = "https://cdn.syndication.twimg.com/tweet-result"


def _compute_token(tweet_id: str) -> str:
    n = int(tweet_id) / 1e15 * math.pi
    chars = "0123456789abcdefghijklmnopqrstuvwxyz"
    result = ""
    int_part = int(n)
    if int_part == 0:
        result = "0"
    else:
        while int_part:
            result = chars[int_part % 36] + result
            int_part //= 36
    frac = n - int(n)
    if frac:
        result += "."
        for _ in range(8):
            frac *= 36
            digit = int(frac)
            result += chars[digit]
            frac -= digit
            if frac < 1e-10:
                break
    result = re.sub(r"^0+", "", result).lstrip(".")
    return result


@dataclass
class ScrapedTweet:
    tweet_id: str
    author_handle: str
    author_name: str
    text: str
    created_at: datetime | None
    media_urls: list[str] = field(default_factory=list)
    raw_json: dict = field(default_factory=dict)

    # Richer fields surfaced from the syndication payload.
    is_verified: bool = False
    favorite_count: int | None = None
    retweet_count: int | None = None
    reply_count: int | None = None
    view_count: int | None = None
    hashtags: list[str] = field(default_factory=list)
    mentioned_handles: list[str] = field(default_factory=list)
    embedded_urls: list[dict[str, str]] = field(default_factory=list)
    lang: str | None = None
    quoted_tweet: Optional["ScrapedTweet"] = None
    tickers: list[str] = field(default_factory=list)
    in_reply_to_status_id: str | None = None
    in_reply_to_screen_name: str | None = None


def _to_dict(tweet: ScrapedTweet) -> dict[str, Any]:
    """Like asdict() but handles datetime → isoformat for JSON-safety."""
    d = asdict(tweet)
    if tweet.created_at is not None:
        d["created_at"] = tweet.created_at.isoformat()
    if d.get("quoted_tweet") and tweet.quoted_tweet is not None:
        d["quoted_tweet"] = _to_dict(tweet.quoted_tweet)
    # Drop the big raw_json blob from the public dict — keep the structured fields only.
    d.pop("raw_json", None)
    return d


def to_jsonable(tweet: ScrapedTweet) -> dict[str, Any]:
    """Public helper: convert a ScrapedTweet (and any nested quoted_tweet) to a
    JSON-serialisable dict suitable for persistence in `articles.tweet_meta`."""
    return _to_dict(tweet)


def _parse_created_at(raw_date: str | None) -> datetime | None:
    if not raw_date:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%a %b %d %H:%M:%S +0000 %Y"):
        try:
            return datetime.strptime(raw_date, fmt)
        except ValueError:
            continue
    return None


def _parse_tweet_payload(data: dict) -> ScrapedTweet:
    """Build a ScrapedTweet from one syndication-API payload dict.

    Reused for both the top-level tweet and any nested `quoted_tweet`.
    """
    user = (
        data.get("user")
        or data.get("core", {}).get("user_results", {}).get("result", {}).get("legacy", {})
        or {}
    )
    media_details = data.get("mediaDetails") or []
    entities = data.get("entities") or {}

    is_verified = bool(
        user.get("verified")
        or user.get("is_blue_verified")
        or user.get("isBlueVerified")
        or data.get("user", {}).get("verified")
    )

    quoted = None
    raw_quoted = data.get("quoted_tweet") or data.get("quotedTweet")
    if isinstance(raw_quoted, dict):
        try:
            quoted = _parse_tweet_payload(raw_quoted)
        except Exception:
            # Defensive — never let a malformed quoted tweet block the top-level parse.
            quoted = None

    return ScrapedTweet(
        tweet_id=str(data.get("id_str") or data.get("id") or ""),
        author_handle=user.get("screen_name") or user.get("screenName", ""),
        author_name=user.get("name", ""),
        text=data.get("text") or data.get("full_text", ""),
        created_at=_parse_created_at(data.get("created_at")),
        media_urls=[m.get("media_url_https", "") for m in media_details if m.get("media_url_https")],
        raw_json=data,
        is_verified=is_verified,
        favorite_count=data.get("favorite_count"),
        retweet_count=data.get("retweet_count"),
        reply_count=data.get("reply_count") or data.get("conversation_count"),
        view_count=(
            int(data["view_count"]) if isinstance(data.get("view_count"), (int, str)) and str(data.get("view_count")).isdigit()
            else None
        ),
        hashtags=[h.get("text") for h in (entities.get("hashtags") or []) if h.get("text")],
        mentioned_handles=[m.get("screen_name") for m in (entities.get("user_mentions") or []) if m.get("screen_name")],
        embedded_urls=[
            {"display_url": u.get("display_url", ""), "expanded_url": u.get("expanded_url", "")}
            for u in (entities.get("urls") or [])
            if u.get("expanded_url") or u.get("display_url")
        ],
        lang=data.get("lang"),
        quoted_tweet=quoted,
        tickers=_extract_tickers_safe(data.get("text") or data.get("full_text", "")),
        in_reply_to_status_id=str(data["in_reply_to_status_id_str"]) if data.get("in_reply_to_status_id_str") else None,
        in_reply_to_screen_name=data.get("in_reply_to_screen_name") or None,
    )


def scrape_tweet(url: str) -> ScrapedTweet:
    m = _TWEET_ID_RE.search(url)
    if not m:
        raise ValueError(f"No tweet ID found in URL: {url}")
    tweet_id = m.group(1)
    token = _compute_token(tweet_id)

    try:
        resp = httpx.get(
            _SYNDICATION_URL,
            params={"id": tweet_id, "token": token, "lang": "en"},
            timeout=10,
            headers={"User-Agent": _USER_AGENT},
        )
    except (httpx.TimeoutException, httpx.TransportError) as exc:
        raise TransientError(str(exc)) from exc

    if resp.status_code == 404:
        raise Gone404(f"Tweet not found: {tweet_id}")
    if resp.status_code >= 500:
        raise TransientError(f"HTTP {resp.status_code}")
    resp.raise_for_status()

    data = resp.json()
    tweet = _parse_tweet_payload(data)
    # If the syndication response didn't carry the id we asked for, prefer the URL's id.
    if not tweet.tweet_id:
        tweet.tweet_id = tweet_id
    return tweet
