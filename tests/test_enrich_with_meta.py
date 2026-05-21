"""E3 — Tests for tweet_meta-aware enrichment prompt."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from whatsapp_archive.enrich.ollama import _build_tweet_meta_block, enrich_article


_MOCK_RESULT = {
    "summary": "TestCorp announced a merger.",
    "categories": ["finance"],
    "suggested_new_category": None,
    "entities": [{"name": "TestCorp", "kind": "company", "mention_text": "TestCorp"}],
    "sentiment": "bullish",
}

_ARTICLE = {
    "title": "TestCorp Merges With BigCo",
    "raw_text": "TestCorp announced today that it will merge with BigCo in a $1B deal.",
}

_RICH_META = {
    "author_name": "Jane Doe",
    "author_handle": "janedoe",
    "is_verified": True,
    "created_at": "2024-03-15T10:00:00Z",
    "favorite_count": 500,
    "retweet_count": 100,
    "hashtags": ["Finance", "Mergers"],
    "mentioned_handles": ["BigCo", "FinanceDesk"],
    "tickers": ["TCORP", "BCO"],
    "quoted_text": "This merger will reshape the sector",
}


# ── _build_tweet_meta_block ───────────────────────────────────────────────────

def test_meta_block_includes_author():
    block = _build_tweet_meta_block(_RICH_META)
    assert "Jane Doe" in block
    assert "@janedoe" in block


def test_meta_block_includes_verified():
    block = _build_tweet_meta_block(_RICH_META)
    assert "[verified]" in block


def test_meta_block_includes_engagement():
    block = _build_tweet_meta_block(_RICH_META)
    assert "500 likes" in block
    assert "100 retweets" in block


def test_meta_block_includes_hashtags():
    block = _build_tweet_meta_block(_RICH_META)
    assert "#Finance" in block
    assert "#Mergers" in block


def test_meta_block_includes_tickers():
    block = _build_tweet_meta_block(_RICH_META)
    assert "$TCORP" in block


def test_meta_block_includes_quoted_text():
    block = _build_tweet_meta_block(_RICH_META)
    assert "reshape the sector" in block


def test_meta_block_empty_dict_returns_empty():
    block = _build_tweet_meta_block({})
    assert block == ""


def test_meta_block_partial_skips_missing_lines():
    block = _build_tweet_meta_block({"author_handle": "alice"})
    assert "@alice" in block
    assert "Hashtags" not in block
    assert "Mentions" not in block


# ── enrich_article prompt injection ──────────────────────────────────────────

def _mock_ollama(content: dict):
    """Return a mock for httpx.post that yields canned JSON."""
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {"message": {"content": json.dumps(content)}}
    return resp


def test_prompt_contains_meta_block_when_meta_provided():
    captured = {}

    def fake_post(url, json=None, timeout=None):
        captured["body"] = json
        return _mock_ollama(_MOCK_RESULT)

    with patch("httpx.post", side_effect=fake_post):
        enrich_article(_ARTICLE, tweet_meta=_RICH_META)

    user_msg = captured["body"]["messages"][1]["content"]
    assert "Tweet metadata" in user_msg
    assert "@janedoe" in user_msg
    assert "#Finance" in user_msg
    assert "Title: TestCorp Merges" in user_msg


def test_prompt_has_no_meta_block_when_meta_is_none():
    captured = {}

    def fake_post(url, json=None, timeout=None):
        captured["body"] = json
        return _mock_ollama(_MOCK_RESULT)

    with patch("httpx.post", side_effect=fake_post):
        enrich_article(_ARTICLE, tweet_meta=None)

    user_msg = captured["body"]["messages"][1]["content"]
    assert "Tweet metadata" not in user_msg
    assert "Hashtags" not in user_msg
    assert "Title: TestCorp Merges" in user_msg


def test_prompt_has_no_meta_block_when_meta_is_empty():
    captured = {}

    def fake_post(url, json=None, timeout=None):
        captured["body"] = json
        return _mock_ollama(_MOCK_RESULT)

    with patch("httpx.post", side_effect=fake_post):
        enrich_article(_ARTICLE, tweet_meta={})

    user_msg = captured["body"]["messages"][1]["content"]
    assert "Tweet metadata" not in user_msg


def test_result_shape_unchanged():
    """The EnrichResult schema is the same regardless of tweet_meta."""
    with patch("httpx.post", return_value=_mock_ollama(_MOCK_RESULT)):
        result = enrich_article(_ARTICLE, tweet_meta=_RICH_META)
    assert result.summary == "TestCorp announced a merger."
    assert result.sentiment == "bullish"
    assert len(result.entities) == 1
    assert result.entities[0].name == "TestCorp"
