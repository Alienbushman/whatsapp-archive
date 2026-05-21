"""U1 — POST /api/chats/upload registers an uploaded .txt as a live chat."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from whatsapp_archive import create_app


FIXTURE_CHAT = Path(__file__).parent / "fixtures" / "mini_chat.txt"


@pytest.fixture(autouse=True)
def _no_background_loops(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BACKGROUND_SCRAPE", "false")
    monkeypatch.setenv("BACKGROUND_ENRICH", "false")


@pytest.fixture
def client(tmp_path: Path) -> tuple[TestClient, Path]:
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    # Seed an existing chat so we can assert the upload appears IN ADDITION to it.
    shutil.copy(FIXTURE_CHAT, archive_dir / "WhatsApp Chat with Seed.txt")
    app = create_app(archive_dir, ollama_url="http://localhost:11434")
    return TestClient(app), archive_dir


def _sample_chat_bytes() -> bytes:
    # A minimal valid WhatsApp export — one system event + one message.
    return (
        "1/5/23, 9:00 - Alice created group \"Uploaded\"\n"
        "1/5/23, 9:01 - Alice: Hello from upload\n"
    ).encode("utf-8")


def test_upload_valid_txt_returns_200_with_metadata(client) -> None:
    tc, _ = client
    r = tc.post(
        "/api/chats/upload",
        files={"file": ("WhatsApp Chat with Uploaded.txt", _sample_chat_bytes(), "text/plain")},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == "whatsapp-chat-with-uploaded"
    assert body["name"] == "WhatsApp Chat with Uploaded"
    assert body["message_count"] == 1
    assert body["link_count"] == 0


def test_upload_appears_in_chat_list(client) -> None:
    tc, _ = client
    tc.post(
        "/api/chats/upload",
        files={"file": ("WhatsApp Chat with Uploaded.txt", _sample_chat_bytes(), "text/plain")},
    )
    r = tc.get("/api/chats")
    assert r.status_code == 200
    ids = {c["id"] for c in r.json()}
    assert "whatsapp-chat-with-uploaded" in ids
    assert "whatsapp-chat-with-seed" in ids  # the seeded chat is still there


def test_upload_messages_endpoint_serves_uploaded(client) -> None:
    tc, _ = client
    tc.post(
        "/api/chats/upload",
        files={"file": ("WhatsApp Chat with Uploaded.txt", _sample_chat_bytes(), "text/plain")},
    )
    r = tc.get("/api/chats/whatsapp-chat-with-uploaded/messages")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] >= 1
    # The body of our minimal export contains "Hello from upload".
    bodies = [e.get("body", "") for e in body["entries"]]
    assert any("Hello from upload" in b for b in bodies)


def test_upload_rejects_non_txt_extension(client) -> None:
    tc, _ = client
    r = tc.post(
        "/api/chats/upload",
        files={"file": ("ransom.pdf", b"%PDF-1.4 fake bytes", "application/pdf")},
    )
    assert r.status_code == 400
    assert "txt" in r.json()["detail"].lower()


def test_upload_rejects_empty_body(client) -> None:
    tc, _ = client
    r = tc.post(
        "/api/chats/upload",
        files={"file": ("empty.txt", b"", "text/plain")},
    )
    assert r.status_code == 400


def test_upload_corrupted_utf8_cleans_up_disk(client) -> None:
    """Invalid UTF-8 makes parse_file raise; the bad file must not linger in archive_dir."""
    tc, archive_dir = client
    bad_bytes = b"\xff\xfe\x00 not valid utf8 here \x80\x81"
    r = tc.post(
        "/api/chats/upload",
        files={"file": ("bad-utf8.txt", bad_bytes, "text/plain")},
    )
    assert r.status_code == 400
    assert "parse failed" in r.json()["detail"].lower()
    assert not (archive_dir / "bad-utf8.txt").exists(), "Bad upload should be removed from archive_dir"


def test_upload_idempotent_overwrites_existing(client) -> None:
    """Re-uploading with the same filename overwrites and stays at one entry."""
    tc, _ = client
    payload = _sample_chat_bytes()
    r1 = tc.post(
        "/api/chats/upload",
        files={"file": ("WhatsApp Chat with Uploaded.txt", payload, "text/plain")},
    )
    assert r1.status_code == 200
    r2 = tc.post(
        "/api/chats/upload",
        files={"file": ("WhatsApp Chat with Uploaded.txt", payload, "text/plain")},
    )
    assert r2.status_code == 200
    # Second upload shouldn't double the chats list.
    chats = tc.get("/api/chats").json()
    matching = [c for c in chats if c["id"] == "whatsapp-chat-with-uploaded"]
    assert len(matching) == 1
