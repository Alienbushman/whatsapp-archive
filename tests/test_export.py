import csv
import io
import json
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from whatsapp_archive import create_app
from whatsapp_archive.export.markdown import render_keyword_bundle

FIXTURE_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    archive_dir = tmp_path_factory.mktemp("archive")
    import shutil
    shutil.copy(FIXTURE_DIR / "mini_chat.txt", archive_dir / "mini_chat.txt")
    app = create_app(archive_dir)
    return TestClient(app)


# ── Markdown renderer unit tests ───────────────────────────────────────────

def test_render_markdown_header():
    md = render_keyword_bundle("hello", [], [], {}, generated_at="2026-01-01 00:00 UTC")
    assert '# Keyword bundle: "hello"' in md
    assert "0 messages" in md
    assert "0 articles" in md


def test_render_markdown_message_block():
    from datetime import datetime
    msg_hits = [{
        "ts": datetime(2023, 1, 5, 9, 1),
        "ts_str": "2023-01-05T09:01:00",
        "chat_name": "Test",
        "sender": "Alice",
        "body": "Hello everyone",
    }]
    md = render_keyword_bundle("hello", msg_hits, [], {}, generated_at="now")
    assert "Alice" in md
    assert "Hello everyone" in md
    assert "2023-01-05 09:01" in md
    assert "---" in md


def test_render_markdown_article_block():
    from datetime import datetime
    art = {
        "url": "https://example.com",
        "title": "Test Article",
        "published_at": "2023-01-05T10:00:00",
        "raw_text": "Some article content about the topic.",
        "summary": "",
    }
    md = render_keyword_bundle("test", [], [art], {}, generated_at="now")
    assert "Test Article" in md
    assert "https://example.com" in md
    assert "Some article content" in md


# ── API endpoint tests ─────────────────────────────────────────────────────

def test_export_keyword_md(client):
    resp = client.get("/api/export/keyword?q=hello&format=md")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/markdown")
    content = resp.content.decode()
    assert "hello" in content.lower()
    assert "---" in content  # section separator


def test_export_keyword_csv(client):
    resp = client.get("/api/export/keyword?q=hello&format=csv")
    assert resp.status_code == 200
    assert "text/csv" in resp.headers["content-type"]
    reader = csv.DictReader(io.StringIO(resp.content.decode()))
    rows = list(reader)
    assert len(rows) >= 1
    assert "body" in rows[0]
    assert "timestamp" in rows[0]
    assert any("Hello" in row.get("body", "") for row in rows)


def test_export_keyword_json(client):
    resp = client.get("/api/export/keyword?q=hello&format=json")
    assert resp.status_code == 200
    data = resp.json()
    assert "query" in data
    assert data["query"] == "hello"
    assert "messages" in data
    assert len(data["messages"]) >= 1


def test_export_keyword_no_match(client):
    resp = client.get("/api/export/keyword?q=xyzzyabcnotfound&format=md")
    assert resp.status_code == 200
    content = resp.content.decode()
    assert "0 messages" in content


def test_export_keyword_content_disposition(client):
    resp = client.get("/api/export/keyword?q=hello&format=md")
    cd = resp.headers.get("content-disposition", "")
    assert "attachment" in cd
    assert ".md" in cd


def test_export_keyword_csv_columns(client):
    resp = client.get("/api/export/keyword?q=check&format=csv")
    assert resp.status_code == 200
    reader = csv.DictReader(io.StringIO(resp.content.decode()))
    fieldnames = reader.fieldnames or []
    for col in ("timestamp", "chat", "sender", "body", "url", "article_title", "article_summary"):
        assert col in fieldnames, f"Missing column: {col}"
