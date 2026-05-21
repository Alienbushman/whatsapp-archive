"""R5 — Deep-dive report tests."""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from whatsapp_archive import create_app
from whatsapp_archive.scrape.db import open_db, upsert_article, upsert_enrichment
from whatsapp_archive.research.deepdive import (
    generate_deepdive,
    get_deepdive_cache,
    save_deepdive_cache,
    _has_summary_language,
    _build_article_context,
)

FIXTURE_CHAT = Path(__file__).parent / "fixtures" / "mini_chat.txt"

_ENTITY_NAME = "TestCorp"
_OLLAMA_URL = "http://localhost:11434"

# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _no_background(monkeypatch):
    for env in [
        "BACKGROUND_SCRAPE", "BACKGROUND_ENRICH", "BACKGROUND_OCR",
        "BACKGROUND_DIGESTS", "BACKGROUND_ENGAGEMENT_REFRESH", "BACKGROUND_CLUSTERING",
    ]:
        monkeypatch.setenv(env, "false")


@pytest.fixture
def db(tmp_path: Path):
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    shutil.copy(FIXTURE_CHAT, archive_dir / "mini_chat.txt")
    conn = open_db(archive_dir)

    # Seed entity + articles
    conn.execute(
        "INSERT OR IGNORE INTO entities (name, kind, normalized_name) VALUES (?, ?, ?)",
        (_ENTITY_NAME, "company", _ENTITY_NAME.lower()),
    )
    conn.commit()
    entity_id = conn.execute(
        "SELECT id FROM entities WHERE normalized_name=?", (_ENTITY_NAME.lower(),)
    ).fetchone()["id"]

    from datetime import datetime, timezone
    for i in range(5):
        upsert_article(conn, f"https://x.com/article/{i}", "ok", title=f"Article {i}")
        art_id = conn.execute(
            "SELECT id FROM articles WHERE url=?", (f"https://x.com/article/{i}",)
        ).fetchone()["id"]
        sentiment = "bullish" if i % 2 == 0 else "bearish"
        tweet_meta = json.dumps({
            "author_handle": f"user{i % 3}",
            "favorite_count": 10 * i,
            "retweet_count": i,
        })
        conn.execute("UPDATE articles SET tweet_meta=?, published_at=? WHERE id=?",
                     (tweet_meta, f"2024-0{(i % 9) + 1}-01", art_id))
        upsert_enrichment(
            conn, art_id,
            f"TestCorp development {i}",
            "tech",
            json.dumps([{"name": _ENTITY_NAME, "kind": "company"}]),
            sentiment,
            "test-model",
            datetime.now(timezone.utc).isoformat(),
        )
        conn.execute(
            "INSERT OR IGNORE INTO article_entities (article_id, entity_id, assigned_by) VALUES (?, ?, 'llm')",
            (art_id, entity_id),
        )
    conn.commit()
    yield conn
    conn.close()


@pytest.fixture
def client(tmp_path: Path):
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    shutil.copy(FIXTURE_CHAT, archive_dir / "mini_chat.txt")
    return TestClient(create_app(archive_dir))


# ── LLM stub returning the right schema per section ───────────────────────────

def _make_llm_fn():
    """Returns a llm_fn that returns valid JSON for each section prompt."""
    call_count = [0]

    def llm_fn(ollama_url, prompt):
        call_count[0] += 1
        if "factual claims" in prompt or "specific factual assertions" in prompt:
            return [{"claim": "TestCorp raised $100M", "evidence_article_ids": [1, 2], "mention_count": 2}]
        if "Twitter authors" in prompt:
            return [{"handle": "alice", "stance": "bullish", "sample_quote": "Great company", "article_count": 2}]
        if "inflection points" in prompt:
            return [{"month": "2024-01", "event": "Series B closed", "supporting_article_ids": [1]}]
        if "CONFLICTING" in prompt:
            return [{"claim_a": "Revenue up 50%", "claim_b": "Revenue down 10%", "article_ids_a": [1], "article_ids_b": [2]}]
        if "NOT addressed" in prompt or "analytical gaps" in prompt:
            return ["What is the burn rate?", "Who are the key competitors?"]
        return []

    return llm_fn, call_count


# ── Unit tests ────────────────────────────────────────────────────────────────

def test_has_summary_language_detects_summary():
    assert _has_summary_language("Overall, the company is doing well") is True
    assert _has_summary_language("In summary, the claims are mixed") is True
    assert _has_summary_language("The sentiment is bullish across the board") is True


def test_has_summary_language_clean():
    assert _has_summary_language("TestCorp raised $100M in Q1 2024") is False


def test_build_article_context_truncates():
    articles = [
        {"title": "T", "summary": "S" * 300, "published_at": "2024-01-01", "id": i}
        for i in range(20)
    ]
    ctx = _build_article_context(articles, max_chars=500)
    assert len(ctx) <= 520  # small overshoot tolerance


def test_generate_deepdive_structure(db):
    llm_fn, _ = _make_llm_fn()
    report = generate_deepdive(db, _ENTITY_NAME, _OLLAMA_URL, llm_fn=llm_fn)
    assert report is not None
    assert report["entity_name"] == _ENTITY_NAME
    assert report["article_count"] >= 1
    assert "sections" in report
    sections = report["sections"]
    assert "claims" in sections
    assert "authors" in sections
    assert "time_arc" in sections
    assert "contradictions" in sections
    assert "open_questions" in sections


def test_generate_deepdive_claims(db):
    llm_fn, _ = _make_llm_fn()
    report = generate_deepdive(db, _ENTITY_NAME, _OLLAMA_URL, llm_fn=llm_fn)
    claims = report["sections"]["claims"]
    assert len(claims) >= 1
    assert claims[0]["claim"] == "TestCorp raised $100M"
    assert claims[0]["mention_count"] == 2


def test_generate_deepdive_authors(db):
    llm_fn, _ = _make_llm_fn()
    report = generate_deepdive(db, _ENTITY_NAME, _OLLAMA_URL, llm_fn=llm_fn)
    authors = report["sections"]["authors"]
    assert len(authors) >= 1
    assert authors[0]["handle"] == "alice"
    assert authors[0]["stance"] == "bullish"


def test_generate_deepdive_contradictions(db):
    llm_fn, _ = _make_llm_fn()
    report = generate_deepdive(db, _ENTITY_NAME, _OLLAMA_URL, llm_fn=llm_fn)
    contras = report["sections"]["contradictions"]
    assert len(contras) >= 1
    assert contras[0]["claim_a"] == "Revenue up 50%"


def test_generate_deepdive_open_questions(db):
    llm_fn, _ = _make_llm_fn()
    report = generate_deepdive(db, _ENTITY_NAME, _OLLAMA_URL, llm_fn=llm_fn)
    questions = report["sections"]["open_questions"]
    assert len(questions) >= 1
    assert isinstance(questions[0], str)


def test_deepdive_cache_roundtrip(db):
    llm_fn, _ = _make_llm_fn()
    report = generate_deepdive(db, _ENTITY_NAME, _OLLAMA_URL, llm_fn=llm_fn)
    cached = get_deepdive_cache(db, _ENTITY_NAME)
    assert cached is not None
    assert cached["entity_name"] == report["entity_name"]


def test_deepdive_uses_cache(db):
    llm_fn, call_count = _make_llm_fn()
    generate_deepdive(db, _ENTITY_NAME, _OLLAMA_URL, llm_fn=llm_fn)
    first_calls = call_count[0]
    generate_deepdive(db, _ENTITY_NAME, _OLLAMA_URL, llm_fn=llm_fn)
    assert call_count[0] == first_calls  # no extra LLM calls on second run


def test_deepdive_force_refresh(db):
    llm_fn, call_count = _make_llm_fn()
    generate_deepdive(db, _ENTITY_NAME, _OLLAMA_URL, llm_fn=llm_fn)
    first_calls = call_count[0]
    generate_deepdive(db, _ENTITY_NAME, _OLLAMA_URL, llm_fn=llm_fn, force_refresh=True)
    assert call_count[0] > first_calls


def test_deepdive_unknown_entity_returns_none(db):
    llm_fn, _ = _make_llm_fn()
    result = generate_deepdive(db, "NoSuchEntity_XYZ", _OLLAMA_URL, llm_fn=llm_fn)
    assert result is None


# ── API endpoint tests ────────────────────────────────────────────────────────

def test_deepdive_get_not_found(client):
    r = client.get("/api/research/deepdive/UnknownEntity")
    assert r.status_code == 404


def test_deepdive_post_unknown_entity(client):
    r = client.post("/api/research/deepdive/NoSuchEntity_XYZ")
    assert r.status_code == 404
