"""U2 — full tweet metadata extraction from the syndication endpoint."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from whatsapp_archive.scrape.article import ScrapedArticle, scrape_generic
from whatsapp_archive.scrape.syndication import (
    ScrapedTweet,
    scrape_tweet,
    to_jsonable,
)


_TWEET_ID = "1881017134114824509"

# Rich syndication payload — covers every field we surface.
_RICH_TWEET_JSON = {
    "id_str": _TWEET_ID,
    "text": "Multi-line tweet about $GOLD\nSecond line stays intact.\nhttps://t.co/abc",
    "lang": "en",
    "created_at": "2025-01-19T22:55:00.000Z",
    "user": {
        "screen_name": "charliebilello",
        "name": "Charlie Bilello",
        "verified": True,
        "is_blue_verified": False,
    },
    "favorite_count": 1234,
    "retweet_count": 250,
    "reply_count": 42,
    "view_count": 98765,
    "mediaDetails": [
        {"media_url_https": "https://pbs.twimg.com/media/aaa.jpg"},
        {"media_url_https": "https://pbs.twimg.com/media/bbb.jpg"},
    ],
    "entities": {
        "hashtags": [{"text": "Gold"}, {"text": "Macro"}],
        "user_mentions": [{"screen_name": "ZeroHedge"}],
        "urls": [
            {"display_url": "example.com/article", "expanded_url": "https://example.com/article"},
            {"display_url": "fool.com/x", "expanded_url": "https://fool.com/x"},
        ],
    },
    "quoted_tweet": {
        "id_str": "9999999",
        "text": "The original quoted post text.",
        "user": {"screen_name": "JanGold_", "name": "Jan Nieuwenhuijs"},
        "created_at": "2025-01-18T10:00:00.000Z",
    },
}


def _mock_resp(payload: dict, status: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = payload
    resp.raise_for_status = MagicMock()
    return resp


def test_scrape_tweet_surfaces_all_fields() -> None:
    with patch("whatsapp_archive.scrape.syndication.httpx.get") as mock_get:
        mock_get.return_value = _mock_resp(_RICH_TWEET_JSON)
        tweet = scrape_tweet(f"https://x.com/charliebilello/status/{_TWEET_ID}")

    assert isinstance(tweet, ScrapedTweet)
    assert tweet.tweet_id == _TWEET_ID
    assert tweet.author_handle == "charliebilello"
    assert tweet.author_name == "Charlie Bilello"
    assert tweet.text.startswith("Multi-line tweet about $GOLD")
    assert tweet.created_at and tweet.created_at.year == 2025
    assert tweet.is_verified is True
    assert tweet.favorite_count == 1234
    assert tweet.retweet_count == 250
    assert tweet.reply_count == 42
    assert tweet.view_count == 98765
    assert tweet.media_urls == [
        "https://pbs.twimg.com/media/aaa.jpg",
        "https://pbs.twimg.com/media/bbb.jpg",
    ]
    assert tweet.hashtags == ["Gold", "Macro"]
    assert tweet.mentioned_handles == ["ZeroHedge"]
    assert tweet.embedded_urls == [
        {"display_url": "example.com/article", "expanded_url": "https://example.com/article"},
        {"display_url": "fool.com/x", "expanded_url": "https://fool.com/x"},
    ]
    assert tweet.lang == "en"


def test_scrape_tweet_parses_quoted_tweet() -> None:
    with patch("whatsapp_archive.scrape.syndication.httpx.get") as mock_get:
        mock_get.return_value = _mock_resp(_RICH_TWEET_JSON)
        tweet = scrape_tweet(f"https://x.com/charliebilello/status/{_TWEET_ID}")

    assert tweet.quoted_tweet is not None
    assert tweet.quoted_tweet.author_handle == "JanGold_"
    assert tweet.quoted_tweet.author_name == "Jan Nieuwenhuijs"
    assert "The original quoted post text" in tweet.quoted_tweet.text


def test_to_jsonable_strips_raw_json_and_serialises_datetime() -> None:
    with patch("whatsapp_archive.scrape.syndication.httpx.get") as mock_get:
        mock_get.return_value = _mock_resp(_RICH_TWEET_JSON)
        tweet = scrape_tweet(f"https://x.com/charliebilello/status/{_TWEET_ID}")

    payload = to_jsonable(tweet)
    assert "raw_json" not in payload, "raw_json must be dropped to keep tweet_meta compact"
    assert payload["created_at"] == "2025-01-19T22:55:00"  # ISO string, not datetime
    # Quoted tweet nested in the same shape.
    assert payload["quoted_tweet"]["author_handle"] == "JanGold_"
    assert "raw_json" not in payload["quoted_tweet"]


def test_scrape_generic_for_x_url_returns_full_text_and_tweet_dict() -> None:
    with patch("whatsapp_archive.scrape.syndication.httpx.get") as mock_get:
        mock_get.return_value = _mock_resp(_RICH_TWEET_JSON)
        article = scrape_generic(f"https://x.com/charliebilello/status/{_TWEET_ID}")

    assert isinstance(article, ScrapedArticle)
    assert article.scrape_method == "syndication"
    # Title no longer truncated at 80 chars.
    assert len(article.title) > 80
    assert article.title.startswith("@charliebilello:")
    # Author is now the display name (or handle fallback).
    assert article.author == "Charlie Bilello"
    # raw_text is the full tweet text including newlines.
    assert "Second line stays intact." in article.raw_text
    # og_image_url still gets the first media URL for the legacy/fallback path.
    assert article.og_image_url == "https://pbs.twimg.com/media/aaa.jpg"
    # The structured tweet dict is populated and JSON-clean.
    assert article.tweet is not None
    assert article.tweet["author_handle"] == "charliebilello"
    assert article.tweet["author_name"] == "Charlie Bilello"
    assert article.tweet["is_verified"] is True
    assert article.tweet["favorite_count"] == 1234
    assert article.tweet["media_urls"] == [
        "https://pbs.twimg.com/media/aaa.jpg",
        "https://pbs.twimg.com/media/bbb.jpg",
    ]
    assert article.tweet["quoted_tweet"]["author_handle"] == "JanGold_"


def test_scrape_tweet_handles_minimal_payload() -> None:
    """A tweet with only the basics shouldn't crash; missing fields are None / []."""
    minimal = {
        "id_str": "1",
        "text": "hello",
        "user": {"screen_name": "alice"},
    }
    with patch("whatsapp_archive.scrape.syndication.httpx.get") as mock_get:
        mock_get.return_value = _mock_resp(minimal)
        tweet = scrape_tweet("https://x.com/alice/status/1")

    assert tweet.author_handle == "alice"
    assert tweet.author_name == ""
    assert tweet.is_verified is False
    assert tweet.favorite_count is None
    assert tweet.media_urls == []
    assert tweet.hashtags == []
    assert tweet.quoted_tweet is None
