"""I10 — /api/articles/<id>/similar via vector search."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from whatsapp_archive import create_app
from whatsapp_archive.scrape.db import open_db, upsert_article
from whatsapp_archive.scrape.vector import find_similar_articles, COLLECTION

FIXTURE_CHAT = Path(__file__).parent / "fixtures" / "mini_chat.txt"


@pytest.fixture(autouse=True)
def _no_background_loops(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BACKGROUND_SCRAPE", "false")
    monkeypatch.setenv("BACKGROUND_ENRICH", "false")


# ── Unit tests for find_similar_articles ─────────────────────────────────────

def _make_hit(id_: int, score: float, url: str) -> MagicMock:
    h = MagicMock()
    h.id = id_
    h.score = score
    h.payload = {"url": url}
    return h


def _make_point(id_: int, vector: list[float]) -> MagicMock:
    p = MagicMock()
    p.id = id_
    p.vector = vector
    return p


def _setup_search_mock(client: MagicMock, hits: list) -> None:
    """Configure mock to work with _query's query_points/search compat wrapper.

    MagicMock always reports hasattr() == True, so _query always takes the
    query_points branch. We must stub query_points (not search) to avoid empty results.
    """
    resp = MagicMock()
    resp.points = hits
    client.query_points.return_value = resp


def test_find_similar_returns_results():
    client = MagicMock()
    vec = [0.1] * 768
    client.retrieve.return_value = [_make_point(1, vec)]
    _setup_search_mock(client, [
        _make_hit(2, 0.95, "https://x.com/alice/2"),
        _make_hit(3, 0.85, "https://x.com/bob/3"),
    ])

    result = find_similar_articles(client, article_id=1, limit=5)

    client.retrieve.assert_called_once_with(COLLECTION, ids=[1], with_vectors=True)
    assert result is not None
    assert len(result) == 2
    assert result[0]["id"] == 2
    assert result[0]["score"] == 0.95
    assert result[0]["url"] == "https://x.com/alice/2"


def test_find_similar_excludes_self():
    client = MagicMock()
    vec = [0.1] * 768
    client.retrieve.return_value = [_make_point(1, vec)]
    # search returns self as top result
    _setup_search_mock(client, [
        _make_hit(1, 1.0, "https://x.com/alice/1"),
        _make_hit(2, 0.9, "https://x.com/alice/2"),
    ])

    result = find_similar_articles(client, article_id=1, limit=5, exclude_self=True)
    ids = [r["id"] for r in result]
    assert 1 not in ids
    assert 2 in ids


def test_find_similar_returns_none_when_no_vector():
    client = MagicMock()
    client.retrieve.return_value = []  # no point found

    result = find_similar_articles(client, article_id=999, limit=5)
    assert result is None


def test_find_similar_limit_respected():
    client = MagicMock()
    vec = [0.1] * 768
    client.retrieve.return_value = [_make_point(10, vec)]
    hits = [_make_hit(i, 0.9 - i * 0.01, f"https://x.com/a/{i}") for i in range(20)]
    _setup_search_mock(client, hits)

    result = find_similar_articles(client, article_id=10, limit=3)
    assert len(result) == 3


# ── API endpoint tests ────────────────────────────────────────────────────────

@pytest.fixture
def client_with_qdrant(tmp_path: Path):
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    shutil.copy(FIXTURE_CHAT, archive_dir / "mini_chat.txt")
    db = open_db(archive_dir)

    aid = upsert_article(
        db, "https://x.com/alice/status/99", "ok",
        title="Test tweet",
        raw_text="Test content",
        tweet_meta=json.dumps({"author_handle": "alice", "text": "test"}),
        fetched_at="2024-01-01T00:00:00",
    )

    vec = [0.1] * 768

    mock_qdrant = MagicMock()
    mock_qdrant.retrieve.return_value = [_make_point(aid, vec)]
    mock_qdrant.search.return_value = [_make_hit(aid, 1.0, "https://x.com/alice/status/99")]

    with patch("whatsapp_archive.api.get_client", return_value=mock_qdrant), \
         patch("whatsapp_archive.api.ensure_collection"):
        app = create_app(archive_dir)

    return TestClient(app), aid


def test_similar_endpoint_qdrant_unavailable(tmp_path: Path):
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    shutil.copy(FIXTURE_CHAT, archive_dir / "mini_chat.txt")

    # Qdrant unavailable → create_app sets qdrant=None
    with patch("whatsapp_archive.api.get_client", side_effect=Exception("Connection refused")):
        app = create_app(archive_dir)

    client = TestClient(app)
    r = client.get("/api/articles/1/similar")
    assert r.status_code == 503


def test_similar_endpoint_no_vector(tmp_path: Path):
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    shutil.copy(FIXTURE_CHAT, archive_dir / "mini_chat.txt")
    db = open_db(archive_dir)
    aid = upsert_article(db, "https://x.com/z/1", "ok", title="T", raw_text="",
                         tweet_meta='{}', fetched_at="2024-01-01T00:00:00")

    mock_qdrant = MagicMock()
    mock_qdrant.retrieve.return_value = []  # no vector

    with patch("whatsapp_archive.api.get_client", return_value=mock_qdrant), \
         patch("whatsapp_archive.api.ensure_collection"):
        app = create_app(archive_dir)

    client = TestClient(app)
    r = client.get(f"/api/articles/{aid}/similar")
    assert r.status_code == 404
