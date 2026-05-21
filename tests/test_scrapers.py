from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from whatsapp_archive.models import Message
from whatsapp_archive.parser import parse_file
from whatsapp_archive.scrape.article import (
    BlockedPaywall,
    Gone404,
    ScrapedArticle,
    TransientError,
    UnsupportedMediaType,
    scrape_generic,
)
from whatsapp_archive.scrape.syndication import (
    ScrapedTweet,
    _compute_token,
    scrape_tweet,
)

FIXTURE = Path(__file__).parent / "fixtures" / "mini_chat.txt"

_LONG_BODY = "word " * 50  # 250 chars — above trafilatura 200-char threshold


# ── URL extraction ────────────────────────────────────────────────────────────


def test_url_extraction_basic():
    export = parse_file(FIXTURE)
    urls = [lnk.url for lnk in export.links]
    assert "https://example.com/article?q=test" in urls


def test_url_extraction_trailing_punct():
    export = parse_file(FIXTURE)
    urls = [lnk.url for lnk in export.links]
    assert "https://news.example.org/story" in urls
    assert not any(u.endswith(".") for u in urls)


def test_url_extraction_multiple():
    export = parse_file(FIXTURE)
    last_msg_links = [
        lnk for lnk in export.links
        if "example.com" in lnk.url or "example.org" in lnk.url
    ]
    assert len(last_msg_links) == 2


def test_url_extraction_none():
    export = parse_file(FIXTURE)
    # messages without URLs should not contribute links
    no_url_senders = {"Alice", "Bob", "+1 555 000 0001"}
    first_entries = export.entries[:5]
    first_senders = {e.sender for e in first_entries if isinstance(e, Message)}
    link_senders = {lnk.sender for lnk in export.links}
    assert not (first_senders & link_senders - {"Alice"})


def test_chat_export_links():
    export = parse_file(FIXTURE)
    assert len(export.links) >= 2
    for lnk in export.links:
        assert lnk.url.startswith("http")
        assert lnk.sender
        assert lnk.timestamp is not None


# ── Article scraper ───────────────────────────────────────────────────────────


def _mock_response(status=200, content_type="text/html; charset=utf-8", text=""):
    resp = MagicMock()
    resp.status_code = status
    resp.headers = {"content-type": content_type}
    resp.text = text
    resp.url = "https://example.com/article"
    resp.raise_for_status = MagicMock()
    return resp


_RICH_HTML = (
    "<html><head>"
    '<meta property="og:title" content="Test Article"/>'
    '<meta property="og:image" content="https://example.com/img.jpg"/>'
    "</head><body>"
    + "<p>" + ("This is a well-written article about technology and society. " * 8) + "</p>"
    + "</body></html>"
)

_OG_ONLY_HTML = (
    "<html><head>"
    '<meta property="og:title" content="OG Title"/>'
    '<meta property="og:description" content="Short desc"/>'
    "</head><body></body></html>"
)


def test_scrape_trafilatura_path():
    with patch("whatsapp_archive.scrape.article.httpx.get") as mock_get:
        mock_get.return_value = _mock_response(text=_RICH_HTML)
        result = scrape_generic("https://example.com/article")
    assert result.scrape_method == "trafilatura"
    assert len(result.raw_text) >= 200


def test_scrape_og_fallback():
    with patch("whatsapp_archive.scrape.article.httpx.get") as mock_get:
        mock_get.return_value = _mock_response(text=_OG_ONLY_HTML)
        result = scrape_generic("https://example.com/article")
    assert result.scrape_method == "og_only"
    assert result.title == "OG Title"
    assert result.raw_text == "Short desc"


def test_scrape_404():
    with patch("whatsapp_archive.scrape.article.httpx.get") as mock_get:
        mock_get.return_value = _mock_response(status=404)
        with pytest.raises(Gone404):
            scrape_generic("https://example.com/article")


def test_scrape_blocked():
    with patch("whatsapp_archive.scrape.article.httpx.get") as mock_get:
        mock_get.return_value = _mock_response(status=403)
        with pytest.raises(BlockedPaywall):
            scrape_generic("https://example.com/article")


def test_scrape_5xx():
    with patch("whatsapp_archive.scrape.article.httpx.get") as mock_get:
        mock_get.return_value = _mock_response(status=503)
        with pytest.raises(TransientError):
            scrape_generic("https://example.com/article")


def test_scrape_non_html():
    with patch("whatsapp_archive.scrape.article.httpx.get") as mock_get:
        mock_get.return_value = _mock_response(content_type="application/pdf")
        with pytest.raises(UnsupportedMediaType):
            scrape_generic("https://example.com/file.pdf")


# ── x.com syndication ─────────────────────────────────────────────────────────

_TWEET_ID = "1869193404271247827"

_TWEET_JSON = {
    "id_str": _TWEET_ID,
    "text": "China Secretly Buying Up Massive Amounts Of Gold https://t.co/abc",
    "created_at": "2024-12-18T01:30:10.000Z",
    "user": {
        "screen_name": "zerohedge",
        "name": "ZeroHedge",
    },
    "mediaDetails": [
        {"media_url_https": "https://pbs.twimg.com/media/test.jpg"}
    ],
}


def test_token_computation():
    token = _compute_token(_TWEET_ID)
    assert token
    assert isinstance(token, str)
    assert len(token) > 0


def test_scrape_tweet_mock():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = _TWEET_JSON
    mock_resp.raise_for_status = MagicMock()

    with patch("whatsapp_archive.scrape.syndication.httpx.get") as mock_get:
        mock_get.return_value = mock_resp
        result = scrape_tweet(f"https://x.com/zerohedge/status/{_TWEET_ID}")

    assert result.tweet_id == _TWEET_ID
    assert result.author_handle == "zerohedge"
    assert result.author_name == "ZeroHedge"
    assert "Gold" in result.text
    assert result.created_at is not None
    assert result.created_at.year == 2024
    assert result.media_urls == ["https://pbs.twimg.com/media/test.jpg"]


def test_scrape_tweet_url_extraction():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = _TWEET_JSON
    mock_resp.raise_for_status = MagicMock()

    with patch("whatsapp_archive.scrape.syndication.httpx.get") as mock_get:
        mock_get.return_value = mock_resp

        # x.com URL
        r1 = scrape_tweet(f"https://x.com/zerohedge/status/{_TWEET_ID}")
        assert r1.tweet_id == _TWEET_ID

        # twitter.com URL
        r2 = scrape_tweet(f"https://twitter.com/zerohedge/status/{_TWEET_ID}")
        assert r2.tweet_id == _TWEET_ID


def test_scrape_tweet_invalid_url():
    with pytest.raises(ValueError):
        scrape_tweet("https://example.com/not-a-tweet")


def test_scrape_tweet_dispatched_from_generic():
    """scrape_generic routes x.com URLs through syndication."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = _TWEET_JSON
    mock_resp.raise_for_status = MagicMock()

    with patch("whatsapp_archive.scrape.syndication.httpx.get") as mock_get:
        mock_get.return_value = mock_resp
        result = scrape_generic(f"https://x.com/zerohedge/status/{_TWEET_ID}")

    assert isinstance(result, ScrapedArticle)
    assert result.scrape_method == "syndication"
    # U2: author is now the display name (falling back to handle if absent).
    assert result.author == "ZeroHedge"
    # U2: full tweet text reaches the title (no 80-char truncation).
    assert "China Secretly Buying" in result.title
    # U2: structured tweet payload populated.
    assert result.tweet is not None
    assert result.tweet["author_handle"] == "zerohedge"
