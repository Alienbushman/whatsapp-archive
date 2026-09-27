"""Q20 — Chat lifecycle: DELETE endpoint, watch-loop deletion, upload dedup."""

from __future__ import annotations

import shutil
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from whatsapp_archive import create_app


FIXTURE_CHAT = Path(__file__).parent / "fixtures" / "mini_chat.txt"

_SAMPLE_A = (
    "1/5/23, 9:00 - Alice created group \"Chat A\"\n"
    "1/5/23, 9:01 - Alice: Hello A\n"
).encode("utf-8")

_SAMPLE_B = (
    "2/10/23, 10:00 - Bob created group \"Chat B\"\n"
    "2/10/23, 10:01 - Bob: Hello B\n"
).encode("utf-8")


@pytest.fixture(autouse=True)
def _no_background_loops(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BACKGROUND_SCRAPE", "false")
    monkeypatch.setenv("BACKGROUND_ENRICH", "false")


@pytest.fixture
def client(tmp_path: Path):
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    shutil.copy(FIXTURE_CHAT, archive_dir / "WhatsApp Chat with Seed.txt")
    app = create_app(archive_dir, ollama_url="http://localhost:11434")
    return TestClient(app), archive_dir


# ---------------------------------------------------------------------------
# Part A — DELETE /api/chats/{id}
# ---------------------------------------------------------------------------

def test_delete_chat_returns_200(client) -> None:
    tc, _ = client
    r = tc.delete("/api/chats/whatsapp-chat-with-seed")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["deleted"] is True
    assert body["chat_id"] == "whatsapp-chat-with-seed"
    assert isinstance(body["removed_messages_count"], int)


def test_delete_removes_from_list(client) -> None:
    tc, _ = client
    tc.delete("/api/chats/whatsapp-chat-with-seed")
    ids = {c["id"] for c in tc.get("/api/chats").json()}
    assert "whatsapp-chat-with-seed" not in ids


def test_delete_nonexistent_returns_404(client) -> None:
    tc, _ = client
    r = tc.delete("/api/chats/does-not-exist")
    assert r.status_code == 404


def test_delete_then_messages_returns_404(client) -> None:
    tc, _ = client
    tc.delete("/api/chats/whatsapp-chat-with-seed")
    r = tc.get("/api/chats/whatsapp-chat-with-seed/messages")
    assert r.status_code == 404


def test_delete_with_remove_file_deletes_disk_file(client) -> None:
    tc, archive_dir = client
    txt_path = archive_dir / "WhatsApp Chat with Seed.txt"
    assert txt_path.exists()
    r = tc.delete("/api/chats/whatsapp-chat-with-seed?remove_file=true")
    assert r.status_code == 200
    assert not txt_path.exists(), "Source .txt should be deleted when remove_file=true"


def test_delete_without_remove_file_parks_disk_file(client) -> None:
    # Contract changed: the file used to stay in the archive folder, so the
    # watcher / next restart loaded the chat straight back. It's now kept, but
    # moved out of the *.txt glob.
    tc, archive_dir = client
    txt_path = archive_dir / "WhatsApp Chat with Seed.txt"
    original = txt_path.read_bytes()
    r = tc.delete("/api/chats/whatsapp-chat-with-seed")
    assert r.status_code == 200
    assert not txt_path.exists()
    parked = archive_dir / ".removed-chats" / "WhatsApp Chat with Seed.txt"
    assert parked.read_bytes() == original, "Source .txt should be kept (moved) when remove_file=false"


def test_delete_parks_without_clobbering_earlier_removal(client) -> None:
    tc, archive_dir = client
    parked_dir = archive_dir / ".removed-chats"
    parked_dir.mkdir()
    (parked_dir / "WhatsApp Chat with Seed.txt").write_bytes(b"older copy")
    tc.delete("/api/chats/whatsapp-chat-with-seed")
    assert (parked_dir / "WhatsApp Chat with Seed.txt").read_bytes() == b"older copy"
    assert (parked_dir / "WhatsApp Chat with Seed (2).txt").exists()


def test_deleted_chat_does_not_come_back_on_restart(client) -> None:
    tc, archive_dir = client
    tc.delete("/api/chats/whatsapp-chat-with-seed")
    tc2 = TestClient(create_app(archive_dir, ollama_url="http://localhost:11434"))
    assert "whatsapp-chat-with-seed" not in {c["id"] for c in tc2.get("/api/chats").json()}


# ---------------------------------------------------------------------------
# Part B — Watch loop deletion reconciliation
# ---------------------------------------------------------------------------

def test_watch_loop_removes_deleted_file(tmp_path: Path) -> None:
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    txt = archive_dir / "WhatsApp Chat with Temp.txt"
    txt.write_bytes(_SAMPLE_A)

    app = create_app(archive_dir, ollama_url="http://localhost:11434",
                     watch=True, watch_interval=1)
    tc = TestClient(app)

    # Chat is present before deletion
    assert "whatsapp-chat-with-temp" in {c["id"] for c in tc.get("/api/chats").json()}

    # Delete the file and wait for the watcher tick
    txt.unlink()
    time.sleep(2.5)

    ids = {c["id"] for c in tc.get("/api/chats").json()}
    assert "whatsapp-chat-with-temp" not in ids, (
        "Chat should be removed from in-memory state when its source file is deleted"
    )


def test_watch_loop_does_not_resurrect_deleted_chat_and_reupload_works(tmp_path: Path) -> None:
    """The reported bug: delete in the UI → watcher reloads the file → re-upload says 'already exists'."""
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    (archive_dir / "WhatsApp Chat with Temp.txt").write_bytes(_SAMPLE_A)

    app = create_app(archive_dir, ollama_url="http://localhost:11434",
                     watch=True, watch_interval=1)
    tc = TestClient(app)

    assert tc.delete("/api/chats/whatsapp-chat-with-temp").status_code == 200
    time.sleep(2.5)
    assert "whatsapp-chat-with-temp" not in {c["id"] for c in tc.get("/api/chats").json()}

    r = tc.post(
        "/api/chats/upload",
        files={"file": ("WhatsApp Chat with Temp.txt", _SAMPLE_A, "text/plain")},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "created"
    assert r.json()["replaced"] is True


def test_watch_loop_survives_unreadable_file(tmp_path: Path) -> None:
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    app = create_app(archive_dir, ollama_url="http://localhost:11434",
                     watch=True, watch_interval=1)
    tc = TestClient(app)

    (archive_dir / "WhatsApp Chat with Broken.txt").write_bytes(b"\xff\xfe not utf-8 \x80")
    time.sleep(1.5)
    (archive_dir / "WhatsApp Chat with Temp.txt").write_bytes(_SAMPLE_A)
    time.sleep(2.5)

    ids = {c["id"] for c in tc.get("/api/chats").json()}
    assert "whatsapp-chat-with-temp" in ids, "a bad file must not stop the watcher loading later ones"
    assert "whatsapp-chat-with-broken" not in ids


def test_watch_reload_drops_stale_fingerprint(tmp_path: Path) -> None:
    """After a chat's file is edited, uploading its *old* content under a new name
    must not be answered from a stale hash pointing at the edited chat."""
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    txt = archive_dir / "WhatsApp Chat with Temp.txt"
    txt.write_bytes(_SAMPLE_A)
    app = create_app(archive_dir, ollama_url="http://localhost:11434",
                     watch=True, watch_interval=1)
    tc = TestClient(app)

    txt.write_bytes(_SAMPLE_B)  # replaced on disk with unrelated content
    time.sleep(2.5)

    r = tc.post(
        "/api/chats/upload",
        files={"file": ("WhatsApp Chat with Other.txt", _SAMPLE_A, "text/plain")},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "created"
    assert r.json()["id"] == "whatsapp-chat-with-other"


# ---------------------------------------------------------------------------
# Part C — Upload content-fingerprint dedup
# ---------------------------------------------------------------------------

def test_upload_duplicate_returns_existing_chat(client) -> None:
    tc, _ = client
    # First upload — new chat
    r1 = tc.post(
        "/api/chats/upload",
        files={"file": ("WhatsApp Chat with A.txt", _SAMPLE_A, "text/plain")},
    )
    assert r1.status_code == 200
    assert r1.json()["replaced"] is True

    # Second upload — identical bytes, different filename
    r2 = tc.post(
        "/api/chats/upload",
        files={"file": ("WhatsApp Chat with A Copy.txt", _SAMPLE_A, "text/plain")},
    )
    assert r2.status_code == 200
    body = r2.json()
    assert body["replaced"] is False
    assert body["id"] == "whatsapp-chat-with-a"  # returns the ORIGINAL chat id


def test_upload_duplicate_creates_no_extra_chat(client) -> None:
    tc, _ = client
    tc.post(
        "/api/chats/upload",
        files={"file": ("WhatsApp Chat with A.txt", _SAMPLE_A, "text/plain")},
    )
    tc.post(
        "/api/chats/upload",
        files={"file": ("WhatsApp Chat with A Copy.txt", _SAMPLE_A, "text/plain")},
    )
    chats = tc.get("/api/chats").json()
    a_chats = [c for c in chats if c["id"] == "whatsapp-chat-with-a"]
    assert len(a_chats) == 1, "Duplicate upload must not create a second chat entry"
    # Also assert the copy was not created
    copy_ids = [c for c in chats if c["id"] == "whatsapp-chat-with-a-copy"]
    assert len(copy_ids) == 0, "Copy chat must not appear when content is identical"


def test_upload_different_content_creates_new_chat(client) -> None:
    tc, _ = client
    tc.post(
        "/api/chats/upload",
        files={"file": ("WhatsApp Chat with A.txt", _SAMPLE_A, "text/plain")},
    )
    r2 = tc.post(
        "/api/chats/upload",
        files={"file": ("WhatsApp Chat with B.txt", _SAMPLE_B, "text/plain")},
    )
    assert r2.status_code == 200
    assert r2.json()["replaced"] is True
    ids = {c["id"] for c in tc.get("/api/chats").json()}
    assert "whatsapp-chat-with-a" in ids
    assert "whatsapp-chat-with-b" in ids


def test_delete_then_reupload_not_deduplicated(client) -> None:
    """After deleting a chat, re-uploading the same bytes should create a fresh chat."""
    tc, _ = client
    tc.post(
        "/api/chats/upload",
        files={"file": ("WhatsApp Chat with A.txt", _SAMPLE_A, "text/plain")},
    )
    tc.delete("/api/chats/whatsapp-chat-with-a?remove_file=true")

    r3 = tc.post(
        "/api/chats/upload",
        files={"file": ("WhatsApp Chat with A.txt", _SAMPLE_A, "text/plain")},
    )
    assert r3.status_code == 200
    assert r3.json()["replaced"] is True, (
        "After deleting a chat, re-uploading the same bytes should create a fresh entry"
    )


def test_watch_tick_during_upload_does_not_drop_the_new_chat(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A tick that lists the folder while an upload holds the registry used to
    miss the new file and "remove" the chat the upload had just added."""
    import whatsapp_archive.api as api_module

    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    app = create_app(archive_dir, ollama_url="http://localhost:11434",
                     watch=True, watch_interval=1)
    tc = TestClient(app)

    real_pick_target = api_module.pick_target

    def slow_pick_target(*args, **kwargs):
        time.sleep(2.5)  # hold the upload mid-flight across a watcher tick
        return real_pick_target(*args, **kwargs)

    monkeypatch.setattr(api_module, "pick_target", slow_pick_target)
    r = tc.post(
        "/api/chats/upload",
        files={"file": ("WhatsApp Chat with Temp.txt", _SAMPLE_A, "text/plain")},
    )
    assert r.json()["status"] == "created"
    time.sleep(0.3)  # let the tick that was waiting on the lock run
    assert "whatsapp-chat-with-temp" in {c["id"] for c in tc.get("/api/chats").json()}
