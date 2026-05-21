import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx
import trafilatura
from selectolax.parser import HTMLParser

_XCOM_RE = re.compile(r"https?://(?:www\.)?(?:x|twitter)\.com/")

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


class UnsupportedMediaType(Exception):
    pass


class BlockedPaywall(Exception):
    pass


class Gone404(Exception):
    pass


class TransientError(Exception):
    pass


@dataclass
class ScrapedArticle:
    url: str
    title: str | None
    author: str | None
    published_at: datetime | None
    raw_text: str
    og_image_url: str | None
    scrape_method: str  # "trafilatura" | "og_only" | "syndication"
    # Structured tweet payload preserved alongside the generic shape so x.com
    # links can be rendered as full TweetCards downstream. None for non-tweets.
    tweet: dict[str, Any] | None = None


def scrape_generic(url: str) -> ScrapedArticle:
    if _XCOM_RE.match(url):
        from .syndication import scrape_tweet, to_jsonable
        tweet = scrape_tweet(url)
        author = (tweet.author_name or tweet.author_handle).strip() or tweet.author_handle
        # Title is "@handle: full text"; downstream UI is expected to use
        # `article.tweet` for rich rendering, so the title is just a fallback.
        title = f"@{tweet.author_handle}: {tweet.text}" if tweet.text else f"@{tweet.author_handle}"
        return ScrapedArticle(
            url=url,
            title=title,
            author=author,
            published_at=tweet.created_at,
            raw_text=tweet.text,
            og_image_url=tweet.media_urls[0] if tweet.media_urls else None,
            scrape_method="syndication",
            tweet=to_jsonable(tweet),
        )

    try:
        resp = httpx.get(
            url,
            timeout=15,
            follow_redirects=True,
            headers={"User-Agent": _USER_AGENT},
        )
    except (httpx.TimeoutException, httpx.TransportError) as exc:
        raise TransientError(str(exc)) from exc

    if resp.status_code in (401, 402, 403):
        raise BlockedPaywall(f"HTTP {resp.status_code}")
    if resp.status_code == 404:
        raise Gone404("HTTP 404")
    if resp.status_code >= 500:
        raise TransientError(f"HTTP {resp.status_code}")
    resp.raise_for_status()

    content_type = resp.headers.get("content-type", "")
    if "html" not in content_type:
        raise UnsupportedMediaType(content_type)

    html = resp.text
    final_url = str(resp.url)

    # Try trafilatura
    extracted = trafilatura.extract(
        html,
        output_format="txt",
        url=final_url,
        favor_precision=True,
    )
    if extracted and len(extracted) >= 200:
        meta = trafilatura.extract_metadata(html, default_url=final_url)
        return ScrapedArticle(
            url=final_url,
            title=meta.title if meta else None,
            author=meta.author if meta else None,
            published_at=_parse_date(meta.date if meta else None),
            raw_text=extracted,
            og_image_url=_og_image(html),
            scrape_method="trafilatura",
        )

    # OG fallback
    tree = HTMLParser(html)
    og_title = _meta(tree, "og:title") or _meta(tree, "title")
    og_desc = _meta(tree, "og:description") or _meta(tree, "description") or ""
    og_img = _meta(tree, "og:image")
    return ScrapedArticle(
        url=final_url,
        title=og_title,
        author=None,
        published_at=None,
        raw_text=og_desc,
        og_image_url=og_img,
        scrape_method="og_only",
    )


def _meta(tree: HTMLParser, prop: str) -> str | None:
    for node in tree.css(f'meta[property="{prop}"], meta[name="{prop}"]'):
        val = node.attributes.get("content")
        if val:
            return val.strip()
    return None


def _og_image(html: str) -> str | None:
    return _meta(HTMLParser(html), "og:image")


def _parse_date(s: str | None) -> datetime | None:
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.strptime(s[:19], fmt)
        except ValueError:
            continue
    return None
