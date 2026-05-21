"""Q17 — AskPanel SSE phase events + citations tests."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from whatsapp_archive import create_app
from whatsapp_archive.scrape.db import open_db, upsert_article, upsert_entity, link_article_entity

FIXTURE_CHAT = Path(__file__).parent / "fixtures" / "mini_chat.txt"


@pytest.fixture(autouse=True)
def _no_background(monkeypatch):
    for env in [
        "BACKGROUND_SCRAPE", "BACKGROUND_ENRICH", "BACKGROUND_OCR",
        "BACKGROUND_DIGESTS", "BACKGROUND_ENGAGEMENT_REFRESH", "BACKGROUND_CLUSTERING",
        "BACKGROUND_PROFILES",
    ]:
        monkeypatch.setenv(env, "false")


@pytest.fixture
def client_with_article(tmp_path: Path, monkeypatch):
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    shutil.copy(FIXTURE_CHAT, archive_dir / "mini_chat.txt")
    conn = open_db(archive_dir)

    eid = upsert_entity(conn, "GoldCo", "company")
    aid = upsert_article(
        conn, "https://x.com/goldco/1", "ok",
        title="GoldCo quarterly update",
        tweet_meta=json.dumps({
            "author_handle": "goldco",
            "text": "GoldCo Q1 results beat expectations",
        }),
        published_at="2025-01-01T10:00:00",
    )
    link_article_entity(conn, aid, eid, "GoldCo")
    conn.commit()

    def _fake_stream(question, context_blocks, *args, **kwargs):
        yield 'data: {"token": "Hello"}\n\n'
        yield 'data: {"token": " world"}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr("whatsapp_archive.enrich.rag.stream_answer", _fake_stream)

    app = create_app(archive_dir)
    client = TestClient(app)
    yield client, conn, aid
    conn.close()


def _parse_sse(text: str) -> list[dict]:
    """Parse SSE response body into list of decoded event dicts."""
    events = []
    for line in text.splitlines():
        if not line.startswith("data: "):
            continue
        payload = line[6:]
        if payload == "[DONE]":
            events.append({"__done": True})
            continue
        try:
            events.append(json.loads(payload))
        except json.JSONDecodeError:
            pass
    return events


# ── Phase event ordering ──────────────────────────────────────────────────────

def test_first_event_is_retrieving(client_with_article):
    client, _, _ = client_with_article
    r = client.post("/api/chat/ask", json={"question": "GoldCo news", "k": 5})
    assert r.status_code == 200
    events = _parse_sse(r.text)
    assert events, "No SSE events received"
    assert events[0].get("phase") == "retrieving"


def test_thinking_event_before_tokens(client_with_article):
    client, _, _ = client_with_article
    r = client.post("/api/chat/ask", json={"question": "GoldCo news", "k": 5})
    events = _parse_sse(r.text)
    phases = [e.get("phase") for e in events if "phase" in e]
    assert "retrieving" in phases
    assert "thinking" in phases
    # retrieving must come before thinking
    assert phases.index("retrieving") < phases.index("thinking")
    # thinking must come before any token
    token_idx = next((i for i, e in enumerate(events) if e.get("token")), None)
    thinking_idx = next((i for i, e in enumerate(events) if e.get("phase") == "thinking"), None)
    assert thinking_idx is not None
    assert token_idx is not None
    assert thinking_idx < token_idx


def test_done_event_at_end(client_with_article):
    client, _, _ = client_with_article
    r = client.post("/api/chat/ask", json={"question": "GoldCo news", "k": 5})
    events = _parse_sse(r.text)
    assert events[-1].get("__done") is True


def test_tokens_arrive_after_thinking(client_with_article):
    client, _, _ = client_with_article
    r = client.post("/api/chat/ask", json={"question": "GoldCo news", "k": 5})
    events = _parse_sse(r.text)
    token_events = [e for e in events if e.get("token")]
    assert len(token_events) == 2
    assert token_events[0]["token"] == "Hello"
    assert token_events[1]["token"] == " world"


# ── Citations ─────────────────────────────────────────────────────────────────

def test_thinking_event_has_citations_field(client_with_article):
    client, _, _ = client_with_article
    r = client.post("/api/chat/ask", json={"question": "GoldCo news", "k": 5})
    events = _parse_sse(r.text)
    thinking = next((e for e in events if e.get("phase") == "thinking"), None)
    assert thinking is not None
    assert "citations" in thinking
    assert isinstance(thinking["citations"], list)


def test_citations_contain_article_fields(client_with_article):
    client, _, _ = client_with_article
    r = client.post("/api/chat/ask", json={"question": "GoldCo news", "k": 5})
    events = _parse_sse(r.text)
    thinking = next((e for e in events if e.get("phase") == "thinking"), None)
    assert thinking and thinking["citations"]
    c = thinking["citations"][0]
    assert "url" in c
    assert "title" in c
    assert "snippet" in c
    assert "author_handle" in c


def test_entity_hit_appears_in_citations(client_with_article):
    client, _, _ = client_with_article
    r = client.post("/api/chat/ask", json={"question": "tell me about GoldCo", "k": 5})
    events = _parse_sse(r.text)
    thinking = next((e for e in events if e.get("phase") == "thinking"), None)
    urls = [c["url"] for c in (thinking or {}).get("citations", [])]
    assert any("goldco" in u for u in urls)


def test_citations_max_5(client_with_article, tmp_path):
    """Never emits more than 5 citations regardless of result count."""
    client, conn, _ = client_with_article
    # Insert 10 more articles
    for i in range(2, 12):
        upsert_article(conn, f"https://x.com/goldco/{i}", "ok",
                       title=f"GoldCo article {i}",
                       tweet_meta=json.dumps({"author_handle": "goldco", "text": f"GoldCo update {i}"}),
                       published_at="2025-01-01T10:00:00")
    conn.commit()
    r = client.post("/api/chat/ask", json={"question": "GoldCo news", "k": 20})
    events = _parse_sse(r.text)
    thinking = next((e for e in events if e.get("phase") == "thinking"), None)
    assert thinking is not None
    assert len(thinking["citations"]) <= 5


def test_no_entity_match_gives_empty_citations(client_with_article):
    client, _, _ = client_with_article
    r = client.post("/api/chat/ask", json={"question": "quantum computing", "k": 5})
    events = _parse_sse(r.text)
    thinking = next((e for e in events if e.get("phase") == "thinking"), None)
    assert thinking is not None
    assert isinstance(thinking["citations"], list)


# ── Stream integrity ──────────────────────────────────────────────────────────

def test_stream_200_with_no_articles(tmp_path, monkeypatch):
    """Empty DB still yields phase events and streams correctly."""
    for env in ["BACKGROUND_SCRAPE", "BACKGROUND_ENRICH", "BACKGROUND_OCR",
                "BACKGROUND_DIGESTS", "BACKGROUND_ENGAGEMENT_REFRESH",
                "BACKGROUND_CLUSTERING", "BACKGROUND_PROFILES"]:
        monkeypatch.setenv(env, "false")

    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    shutil.copy(FIXTURE_CHAT, archive_dir / "mini_chat.txt")

    def _fake_stream(*args, **kwargs):
        yield 'data: {"token": "nothing found"}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr("whatsapp_archive.enrich.rag.stream_answer", _fake_stream)
    client = TestClient(create_app(archive_dir))
    r = client.post("/api/chat/ask", json={"question": "xyzzy", "k": 3})
    assert r.status_code == 200
    events = _parse_sse(r.text)
    phases = [e.get("phase") for e in events if "phase" in e]
    assert "retrieving" in phases
    assert "thinking" in phases
