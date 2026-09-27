"""Uploading re-exports: new messages merge in, genuinely new chats are always added."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from whatsapp_archive import create_app
from whatsapp_archive.chat_merge import ChatCandidate, base_chat_name, merge_exports, pick_target, same_history
from whatsapp_archive.models import Message
from whatsapp_archive.parser import parse_text, split_entries


def _lines(*lines: str) -> str:
    return "".join(line + "\n" for line in lines)


GOLD_V1 = _lines(
    '1/5/23, 09:00 - Alice created group "Gold"',
    "1/5/23, 09:01 - Alice: gold is up",
    "1/5/23, 09:02 - Bob: nice",
    "multi-line continuation",
    "1/5/23, 09:03 - Alice: https://example.com/a",
)
# A later export of the same chat: everything in v1 plus two new messages.
GOLD_V2 = GOLD_V1 + _lines(
    "1/6/23, 10:00 - Bob: https://example.com/b",
    "1/6/23, 10:01 - Alice: see you",
)
SILVER = _lines(
    '2/1/23, 08:00 - Carol created group "Silver"',
    "2/1/23, 08:01 - Carol: silver chat",
    "2/1/23, 08:02 - Dave: agreed",
)


@pytest.fixture(autouse=True)
def _no_background_loops(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BACKGROUND_SCRAPE", "false")
    monkeypatch.setenv("BACKGROUND_ENRICH", "false")


@pytest.fixture
def archive(tmp_path: Path):
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    (archive_dir / "WhatsApp Chat with Gold.txt").write_text(GOLD_V1, encoding="utf-8")
    tc = TestClient(create_app(archive_dir, ollama_url="http://localhost:11434"))
    return tc, archive_dir


def _upload(tc: TestClient, name: str, text: str | bytes):
    data = text.encode("utf-8") if isinstance(text, str) else text
    return tc.post("/api/chats/upload", files={"file": (name, data, "text/plain")})


def _bodies(tc: TestClient, cid: str) -> list[str]:
    r = tc.get(f"/api/chats/{cid}/messages", params={"page_size": 500})
    assert r.status_code == 200, r.text
    return [e["body"] for e in r.json()["entries"] if e["type"] == "message"]


def _chat_ids(tc: TestClient) -> set[str]:
    return {c["id"] for c in tc.get("/api/chats").json()}


# ── API: the upload outcomes ────────────────────────────────────────────────


def test_newer_export_same_name_adds_new_messages(archive) -> None:
    tc, archive_dir = archive
    r = _upload(tc, "WhatsApp Chat with Gold.txt", GOLD_V2)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "updated"
    assert body["replaced"] is True
    assert body["id"] == "whatsapp-chat-with-gold"
    assert body["new_messages"] == 2
    assert body["message_count"] == 5
    assert _chat_ids(tc) == {"whatsapp-chat-with-gold"}
    # Superset → the upload is written verbatim, so the file stays a real export.
    assert (archive_dir / "WhatsApp Chat with Gold.txt").read_text(encoding="utf-8") == GOLD_V2


def test_browser_copy_suffix_is_treated_as_same_chat(archive) -> None:
    tc, archive_dir = archive
    r = _upload(tc, "WhatsApp Chat with Gold (1).txt", GOLD_V2)
    assert r.json()["status"] == "updated"
    assert r.json()["id"] == "whatsapp-chat-with-gold"
    assert not (archive_dir / "WhatsApp Chat with Gold (1).txt").exists()


def test_identical_reupload_is_unchanged_not_an_error(archive) -> None:
    tc, _ = archive
    r = _upload(tc, "WhatsApp Chat with Gold.txt", GOLD_V1)
    assert r.status_code == 200
    assert r.json()["status"] == "unchanged"
    assert r.json()["replaced"] is False
    assert r.json()["new_messages"] == 0


def test_older_export_is_unchanged_and_loses_nothing(archive) -> None:
    tc, archive_dir = archive
    _upload(tc, "WhatsApp Chat with Gold.txt", GOLD_V2)
    r = _upload(tc, "WhatsApp Chat with Gold.txt", GOLD_V1)
    assert r.json()["status"] == "unchanged"
    assert (archive_dir / "WhatsApp Chat with Gold.txt").read_text(encoding="utf-8") == GOLD_V2
    assert len(_bodies(tc, "whatsapp-chat-with-gold")) == 5


def test_same_name_different_chat_is_kept_separately(archive) -> None:
    """iOS names every export _chat.txt; two groups can share a name. Never overwrite."""
    tc, archive_dir = archive
    r = _upload(tc, "WhatsApp Chat with Gold.txt", SILVER)
    assert r.json()["status"] == "created"
    assert r.json()["id"] == "whatsapp-chat-with-gold-2"
    assert r.json()["name"] == "WhatsApp Chat with Gold (2)"
    assert (archive_dir / "WhatsApp Chat with Gold.txt").read_text(encoding="utf-8") == GOLD_V1
    assert _chat_ids(tc) == {"whatsapp-chat-with-gold", "whatsapp-chat-with-gold-2"}

    # A later export of that second chat, under the shared name, finds the right one.
    r2 = _upload(tc, "WhatsApp Chat with Gold.txt", SILVER + _lines("2/2/23, 09:00 - Carol: more"))
    assert r2.json()["status"] == "updated"
    assert r2.json()["id"] == "whatsapp-chat-with-gold-2"
    assert len(_bodies(tc, "whatsapp-chat-with-gold")) == 3


def test_renamed_group_export_merges_by_content(archive) -> None:
    tc, _ = archive
    r = _upload(tc, "WhatsApp Chat with Gold & Silver.txt", GOLD_V2)
    assert r.json()["status"] == "updated"
    assert r.json()["id"] == "whatsapp-chat-with-gold"
    assert _chat_ids(tc) == {"whatsapp-chat-with-gold"}


def test_one_shared_forwarded_message_does_not_merge_chats(archive) -> None:
    tc, _ = archive
    other = _lines(
        "1/5/23, 09:01 - Alice: gold is up",  # same message forwarded at the same minute
        "3/1/23, 10:00 - Erin: unrelated",
        "3/1/23, 10:01 - Frank: also unrelated",
    )
    r = _upload(tc, "WhatsApp Chat with Friends.txt", other)
    assert r.json()["status"] == "created"
    assert _chat_ids(tc) == {"whatsapp-chat-with-gold", "whatsapp-chat-with-friends"}


def test_new_chat_is_created_and_copy_suffix_dropped(archive) -> None:
    tc, archive_dir = archive
    r = _upload(tc, "WhatsApp Chat with Silver (1).txt", SILVER)
    assert r.json()["status"] == "created"
    assert r.json()["id"] == "whatsapp-chat-with-silver"
    assert r.json()["new_messages"] == 2
    assert (archive_dir / "WhatsApp Chat with Silver.txt").exists()


def test_same_batch_twice_new_then_unchanged(archive) -> None:
    tc, _ = archive
    assert _upload(tc, "WhatsApp Chat with Silver.txt", SILVER).json()["status"] == "created"
    assert _upload(tc, "WhatsApp Chat with Silver.txt", SILVER).json()["status"] == "unchanged"


def test_file_with_no_recognisable_messages_is_rejected_and_not_written(archive) -> None:
    tc, archive_dir = archive
    r = _upload(tc, "notes.txt", "just some notes\nnot a chat\n")
    assert r.status_code == 400
    assert "no whatsapp messages" in r.json()["detail"].lower()
    assert not (archive_dir / "notes.txt").exists()


def test_contact_saved_between_exports_does_not_duplicate(archive) -> None:
    tc, archive_dir = archive
    (archive_dir / "WhatsApp Chat with Pair.txt").write_text(
        _lines(*[f"4/1/23, 09:0{i} - +31-6-0000-0000: hi {i}" for i in range(3)]), encoding="utf-8"
    )
    r = _upload(tc, "WhatsApp Chat with Pair.txt", _lines(
        *[f"4/1/23, 09:0{i} - Jan: hi {i}" for i in range(3)],
        "4/2/23, 09:00 - Jan: next day",
    ))
    assert r.json()["status"] == "updated"
    assert r.json()["new_messages"] == 1
    assert _bodies(tc, "whatsapp-chat-with-pair") == ["hi 0", "hi 1", "hi 2", "next day"]

def test_crlf_and_bom_reexport_of_lf_chat_is_unchanged(archive) -> None:
    tc, _ = archive
    windows_style = b"\xef\xbb\xbf" + GOLD_V1.replace("\n", "\r\n").encode("utf-8")
    r = _upload(tc, "WhatsApp Chat with Gold.txt", windows_style)
    assert r.json()["status"] == "unchanged"


def test_merge_after_restart_reads_file_on_disk(archive) -> None:
    tc, archive_dir = archive
    _upload(tc, "WhatsApp Chat with Gold.txt", GOLD_V2)
    tc2 = TestClient(create_app(archive_dir, ollama_url="http://localhost:11434"))
    v3 = GOLD_V2 + _lines("1/8/23, 08:00 - Bob: third export")
    r = _upload(tc2, "WhatsApp Chat with Gold.txt", v3)
    assert r.json()["status"] == "updated"
    assert r.json()["new_messages"] == 1
    assert (archive_dir / "WhatsApp Chat with Gold.txt").read_text(encoding="utf-8") == v3


def test_bom_does_not_hide_the_first_message(archive) -> None:
    tc, _ = archive
    data = b"\xef\xbb\xbf" + _lines("5/1/23, 09:00 - Ann: first", "5/1/23, 09:01 - Ben: second").encode("utf-8")
    r = _upload(tc, "WhatsApp Chat with Bom.txt", data)
    assert r.json()["status"] == "created"
    assert r.json()["message_count"] == 2


# ── Not fusing different chats ──────────────────────────────────────────────
# Forwarding a link to several chats puts the same (minute, body) message in
# each. The owner's real archive has chat pairs sharing dozens of such links.


def _links(*minutes: str) -> list[str]:
    return [f"3/1/23, {m} - Me: https://example.com/{m.replace(':', '')}" for m in minutes]


def test_new_chat_mostly_of_shared_links_is_not_merged_into_another(archive) -> None:
    tc, _ = archive
    alice = _lines(*_links("10:00", "11:00", "12:00"), *[f"3/1/23, 1{i}:30 - Alice: reply {i}" for i in range(6)])
    assert _upload(tc, "WhatsApp Chat with Alice.txt", alice).json()["status"] == "created"
    bob = _lines(*_links("10:00", "11:00", "12:00"), "3/1/23, 12:05 - Bob: cheers")
    r = _upload(tc, "WhatsApp Chat with Bob.txt", bob)
    assert r.json()["status"] == "created"
    assert r.json()["id"] == "whatsapp-chat-with-bob"

    # …and the reverse: Bob archived first, Alice uploaded.
    tc.delete("/api/chats/whatsapp-chat-with-alice")
    r = _upload(tc, "WhatsApp Chat with Alice.txt", alice)
    assert r.json()["status"] == "created"
    assert len(_bodies(tc, "whatsapp-chat-with-bob")) == 4


def test_same_name_sharing_one_forward_is_a_different_chat(archive) -> None:
    tc, archive_dir = archive
    other_gold = _lines(
        "1/5/23, 09:01 - Carol: gold is up",  # the one forwarded message
        "1/5/23, 09:10 - Carol: something else",
        "1/5/23, 09:20 - Dave: and more",
        "1/5/23, 09:30 - Dave: unrelated",
    )
    r = _upload(tc, "WhatsApp Chat with Gold.txt", other_gold)
    assert r.json()["status"] == "created"
    assert r.json()["id"] == "whatsapp-chat-with-gold-2"
    assert (archive_dir / "WhatsApp Chat with Gold.txt").read_text(encoding="utf-8") == GOLD_V1


def test_renamed_group_without_shared_creation_event_is_new(archive) -> None:
    tc, _ = archive
    no_creation_line = GOLD_V2.split("\n", 1)[1]
    r = _upload(tc, "WhatsApp Chat with Gold Bugs.txt", no_creation_line)
    assert r.json()["status"] == "created"


def test_numbers_in_names_are_kept(archive) -> None:
    tc, _ = archive
    r = _upload(tc, "WhatsApp Chat with Reunion (2019).txt", SILVER)
    assert r.json()["name"] == "WhatsApp Chat with Reunion (2019)"
    reunion_2024 = _lines("6/1/24, 10:00 - Erin: new year", "6/1/24, 10:01 - Erin: new group")
    r = _upload(tc, "WhatsApp Chat with Reunion (2024).txt", reunion_2024)
    assert r.json()["status"] == "created"
    assert r.json()["name"] == "WhatsApp Chat with Reunion (2024)"


# ── Merge details ───────────────────────────────────────────────────────────


LONG = _lines(*[f"1/10/23, 10:{i:02d} - {'Ann' if i % 2 else 'Ben'}: message {i}" for i in range(20)])


def test_trimmed_newer_export_merges_both_sides(archive) -> None:
    """WhatsApp caps export length, so a newer export can lack the oldest messages."""
    tc, _ = archive
    _upload(tc, "WhatsApp Chat with Long.txt", LONG)
    trimmed_newer = LONG.split("\n", 8)[8] + _lines("1/11/23, 09:00 - Ann: brand new")
    r = _upload(tc, "WhatsApp Chat with Long.txt", trimmed_newer)
    assert r.json()["status"] == "updated"
    assert r.json()["new_messages"] == 1
    assert _bodies(tc, "whatsapp-chat-with-long") == [f"message {i}" for i in range(20)] + ["brand new"]


def test_media_export_mode_switch_does_not_duplicate(archive) -> None:
    tc, archive_dir = archive
    without_media = _lines(
        "7/1/23, 09:00 - Ann: look",
        "7/1/23, 09:01 - Ann: <Media omitted>",
        "7/1/23, 09:02 - Ben: wow",
    )
    with_media = _lines(
        "7/1/23, 09:00 - Ann: look",
        "7/1/23, 09:01 - Ann: \u200eIMG-20230701-WA0001.jpg (file attached)",
        "7/1/23, 09:02 - Ben: wow",
        "7/2/23, 10:00 - Ben: later",
    )
    _upload(tc, "WhatsApp Chat with Pics.txt", without_media)
    r = _upload(tc, "WhatsApp Chat with Pics.txt", with_media)
    assert r.json()["status"] == "updated"
    assert r.json()["new_messages"] == 1
    assert r.json()["message_count"] == 4
    # Superset once media is keyed alike → the richer export is kept verbatim.
    assert (archive_dir / "WhatsApp Chat with Pics.txt").read_text(encoding="utf-8") == with_media


def test_added_entry_is_placed_by_upload_order_not_after_the_whole_minute(archive) -> None:
    tc, _ = archive
    rest = [f"8/1/23, 09:00 - C: filler {i}" for i in range(10)]
    old = _lines("8/1/23, 09:00 - A: hi", "8/1/23, 09:00 - B: oops", *rest)
    new = _lines(
        "8/1/23, 09:00 - A: hi",
        "8/1/23, 09:00 - B: This message was deleted",
        *rest,
        "8/1/23, 09:05 - A: next",
    )
    _upload(tc, "WhatsApp Chat with Order.txt", old)
    assert _upload(tc, "WhatsApp Chat with Order.txt", new).json()["status"] == "updated"
    assert _bodies(tc, "whatsapp-chat-with-order") == [
        "hi", "oops", "This message was deleted", *[f"filler {i}" for i in range(10)], "next",
    ]


def test_edit_between_exports_of_a_tiny_chat_keeps_them_apart_rather_than_guess(archive) -> None:
    """Fail-safe direction: too little evidence → a separate chat, never a fused one."""
    tc, _ = archive
    _upload(tc, "WhatsApp Chat with Tiny.txt", _lines("8/2/23, 09:00 - A: hi", "8/2/23, 09:00 - B: oops"))
    r = _upload(tc, "WhatsApp Chat with Tiny.txt", _lines("8/2/23, 09:00 - A: hi", "8/2/23, 09:00 - B: fixed"))
    assert r.json()["status"] == "created"
    assert r.json()["id"] == "whatsapp-chat-with-tiny-2"


def test_upload_sees_file_changed_on_disk_without_watcher(archive) -> None:
    tc, archive_dir = archive
    (archive_dir / "WhatsApp Chat with Gold.txt").write_text(GOLD_V2, encoding="utf-8")
    r = _upload(tc, "WhatsApp Chat with Gold.txt", GOLD_V2)
    assert r.json()["status"] == "unchanged"
    assert r.json()["message_count"] == 5
    assert len(_bodies(tc, "whatsapp-chat-with-gold")) == 5


def test_identical_reupload_after_file_vanished_recreates_it(archive) -> None:
    tc, archive_dir = archive
    (archive_dir / "WhatsApp Chat with Gold.txt").unlink()
    r = _upload(tc, "WhatsApp Chat with Gold.txt", GOLD_V1)
    assert r.json()["status"] == "created"
    assert (archive_dir / "WhatsApp Chat with Gold.txt").read_text(encoding="utf-8") == GOLD_V1


def test_failed_delete_leaves_chat_in_place(archive) -> None:
    tc, archive_dir = archive
    (archive_dir / ".removed-chats").write_text("not a folder")  # parking will fail
    r = tc.delete("/api/chats/whatsapp-chat-with-gold")
    assert r.status_code == 500
    assert "whatsapp-chat-with-gold" in _chat_ids(tc)
    assert (archive_dir / "WhatsApp Chat with Gold.txt").exists()


def test_no_temp_files_left_behind(archive) -> None:
    tc, archive_dir = archive
    _upload(tc, "WhatsApp Chat with Gold.txt", GOLD_V2)
    _upload(tc, "WhatsApp Chat with Silver.txt", SILVER)
    assert sorted(p.name for p in archive_dir.iterdir() if p.suffix != ".db") == [
        "WhatsApp Chat with Gold.txt", "WhatsApp Chat with Silver.txt",
    ]


# ── Pure merge / match logic ────────────────────────────────────────────────


def _cand(cid: str, name: str, text: str) -> ChatCandidate:
    return ChatCandidate.of(cid, name, parse_text(text, Path("x.txt")).entries)


def test_base_chat_name() -> None:
    assert base_chat_name("WhatsApp Chat with Gold (1)") == "WhatsApp Chat with Gold"
    assert base_chat_name("WhatsApp Chat with Gold (12)") == "WhatsApp Chat with Gold"
    assert base_chat_name("Reunion (2019)") == "Reunion (2019)"
    assert base_chat_name("Gold (Au) prices") == "Gold (Au) prices"
    assert base_chat_name("(1)") == "(1)"


def test_same_history_needs_both_sides_to_match_in_the_overlap() -> None:
    big = _cand("big", "Big", LONG).messages
    newer = _cand("n", "N", LONG + _lines("1/11/23, 09:00 - Ann: more")).messages
    assert same_history(big, newer)
    # A small chat whose messages all also appear in a big one is not "the same chat".
    small = _cand("s", "S", _lines(*LONG.splitlines()[5:8], "1/10/23, 10:06 - Zed: mine")).messages
    assert not same_history(small, big)
    # No overlapping period → no evidence.
    assert not same_history(big, _cand("o", "O", SILVER).messages)


def test_pick_target_prefers_the_same_named_chat_with_matching_history() -> None:
    candidates = [
        _cand("gold", "WhatsApp Chat with Gold", GOLD_V1),
        _cand("gold-2", "WhatsApp Chat with Gold (2)", SILVER),
    ]
    assert pick_target(_cand("", "WhatsApp Chat with Gold", SILVER), candidates) == "gold-2"
    assert pick_target(_cand("", "WhatsApp Chat with Gold", GOLD_V2), candidates) == "gold"
    brand_new = _cand("", "WhatsApp Chat with Gold", _lines("5/5/23, 10:00 - Z: hi"))
    assert pick_target(brand_new, candidates) is None


def test_pick_target_renamed_group_needs_the_creation_event() -> None:
    candidates = [_cand("gold", "WhatsApp Chat with Gold", GOLD_V1)]
    assert pick_target(_cand("", "WhatsApp Chat with Gold Bugs", GOLD_V2), candidates) == "gold"
    headless = GOLD_V2.split("\n", 1)[1]
    assert pick_target(_cand("", "WhatsApp Chat with Gold Bugs", headless), candidates) is None


def test_pick_target_same_name_empty_chat_is_filled_in() -> None:
    candidates = [ChatCandidate.of("gold", "WhatsApp Chat with Gold", [])]
    assert pick_target(_cand("", "WhatsApp Chat with Gold", GOLD_V1), candidates) == "gold"


def test_merge_keeps_duplicate_identical_messages() -> None:
    old = _lines("1/5/23, 09:00 - A: ok", "1/5/23, 09:00 - B: ok")
    new = _lines("1/5/23, 09:00 - A: ok", "1/5/23, 09:00 - B: ok", "1/5/23, 09:00 - C: ok")
    result = merge_exports(old, new)
    assert result.added == 1
    assert result.verbatim
    assert result.text == new


def test_merge_keeps_unparsed_preamble_and_existing_order() -> None:
    old = "exported by some tool\n" + _lines("1/5/23, 09:05 - A: second", "1/5/23, 09:01 - A: clock skew")
    new = _lines("1/5/23, 09:03 - B: between", "1/5/23, 09:10 - B: last")
    result = merge_exports(old, new)
    assert not result.verbatim
    assert result.text is not None
    assert result.text.startswith("exported by some tool\n")
    bodies = [r.entry.body for r in split_entries(result.text)[1] if isinstance(r.entry, Message)]
    assert bodies == ["between", "second", "clock skew", "last"]


def test_merge_adds_trailing_newline_between_blocks() -> None:
    old = "x\n" + "1/5/23, 09:00 - A: no newline at end"  # the preamble forces an interleave
    new = _lines("1/5/23, 08:00 - B: earlier", "1/5/23, 09:00 - A: no newline at end", "1/5/23, 09:30 - B: later")
    result = merge_exports(old, new)
    assert result.text is not None
    entries = parse_text(result.text, source_file=Path("x.txt")).entries
    assert [e.body for e in entries] == ["earlier", "no newline at end", "later"]


# ── Round-2 review regressions ──────────────────────────────────────────────


def test_edited_last_message_stays_in_place_across_later_exports(archive) -> None:
    """The older export's last messages are the ones that get edited/deleted;
    they must not be pushed below every newer message (and stay there)."""
    tc, _ = archive
    base = [f"3/1/23, 09:{i:02d} - Ann: msg {i}" for i in range(11)]
    e1 = _lines(*base, "3/1/23, 10:05 - Bob: meet at 8pm")
    e2 = _lines(
        *base,
        "3/1/23, 10:05 - Bob: meet at 9pm <This message was edited>",
        "3/1/23, 11:00 - Ann: at 11",
        "3/1/23, 12:00 - Bob: at 12",
    )
    e3 = e2 + _lines("3/2/23, 08:00 - Ann: next day")
    for export in (e1, e2, e3):
        assert _upload(tc, "WhatsApp Chat with Plans.txt", export).status_code == 200
    assert _bodies(tc, "whatsapp-chat-with-plans")[-5:] == [
        "meet at 8pm", "meet at 9pm <This message was edited>", "at 11", "at 12", "next day",
    ]


def test_same_name_groups_created_at_different_times_are_never_fused(archive) -> None:
    """A family moves to a new group with the same name; the same photos went to both."""
    tc, _ = archive
    photos = [f"2/1/24, 10:0{i} - Ann: <Media omitted>" for i in range(3)]
    old_group = _lines('1/1/23, 09:00 - Ann created group "Family"', "1/1/23, 09:01 - Ann: hi all", *photos)
    new_group = _lines(
        '2/1/24, 09:59 - Ann created group "Family"', *photos,
        *[f"2/1/24, 11:{i:02d} - Ben: new group chat {i}" for i in range(5)],
    )
    assert _upload(tc, "WhatsApp Chat with Family.txt", old_group).json()["status"] == "created"
    r = _upload(tc, "WhatsApp Chat with Family.txt", new_group)
    assert r.json()["status"] == "created"
    assert r.json()["id"] == "whatsapp-chat-with-family-2"


def test_small_group_with_an_edit_merges_when_the_group_line_matches(archive) -> None:
    tc, _ = archive
    r = _upload(tc, "WhatsApp Chat with Gold.txt", _lines(
        '1/5/23, 09:00 - Alice created group "Gold"',
        "1/5/23, 09:01 - Alice: gold is up",
        "1/5/23, 09:02 - Bob: This message was deleted",
        "1/5/23, 09:03 - Alice: https://example.com/a",
        "1/6/23, 10:00 - Bob: later",
    ))
    assert r.json()["status"] == "updated"
    assert r.json()["id"] == "whatsapp-chat-with-gold"


def test_one_to_one_chat_reexported_after_saving_the_contact_merges(archive) -> None:
    tc, archive_dir = archive
    number = "+31-6-0000-0000"
    talk = [("4/1/23, 09:0%d" % i, "Me" if i % 2 else number, f"line {i}") for i in range(8)]
    old = _lines(*[f"{t} - {s}: {b}" for t, s, b in talk])
    assert _upload(tc, f"WhatsApp Chat with {number}.txt", old).json()["status"] == "created"
    saved = _lines(*[f"{t} - {'Jan' if s == number else s}: {b}" for t, s, b in talk], "4/2/23, 10:00 - Jan: new")
    r = _upload(tc, "WhatsApp Chat with Jan.txt", saved)
    assert r.json()["status"] == "updated"
    assert r.json()["id"] == "whatsapp-chat-with-31-6-0000-0000"
    assert r.json()["new_messages"] == 1


def test_broadcast_list_overlap_does_not_merge_one_to_one_chats(archive) -> None:
    tc, _ = archive
    broadcast = [f"4/3/23, 10:{i:02d} - Me: broadcast {i}" for i in range(10)]
    assert _upload(tc, "WhatsApp Chat with Ann.txt", _lines(*broadcast)).json()["status"] == "created"
    r = _upload(tc, "WhatsApp Chat with Bob.txt", _lines(*broadcast, "4/3/23, 11:00 - Bob: thanks"))
    assert r.json()["status"] == "created"


def test_unreadable_archived_copy_is_reported_not_answered_from_memory(archive) -> None:
    tc, archive_dir = archive
    gold = archive_dir / "WhatsApp Chat with Gold.txt"
    gold.write_bytes(b"\xff\xfe corrupted \x80")
    r = _upload(tc, "WhatsApp Chat with Gold.txt", GOLD_V1)  # identical to what was loaded
    assert r.status_code == 409
    assert "can't be read" in r.json()["detail"]
    assert gold.read_bytes() == b"\xff\xfe corrupted \x80", "never written over"


def test_upload_sees_file_dropped_into_folder_without_watcher(archive) -> None:
    tc, archive_dir = archive
    (archive_dir / "WhatsApp Chat with Silver.txt").write_text(SILVER, encoding="utf-8")
    r = _upload(tc, "WhatsApp Chat with Silver.txt", SILVER)
    assert r.json()["status"] == "unchanged"
    assert r.json()["id"] == "whatsapp-chat-with-silver"
    assert not (archive_dir / "WhatsApp Chat with Silver (2).txt").exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX file modes")
def test_written_chat_files_keep_normal_permissions(archive) -> None:
    tc, archive_dir = archive
    gold = archive_dir / "WhatsApp Chat with Gold.txt"
    gold.chmod(0o644)
    _upload(tc, "WhatsApp Chat with Gold.txt", GOLD_V2)
    assert gold.stat().st_mode & 0o777 == 0o644
    _upload(tc, "WhatsApp Chat with Silver.txt", SILVER)
    assert (archive_dir / "WhatsApp Chat with Silver.txt").stat().st_mode & 0o044  # group/other readable


# ── Round-3 review regressions ──────────────────────────────────────────────
# Chats without a "created group" line (1:1 chats, groups joined later).


def test_tiny_chat_of_forwards_is_not_merged_into_a_same_named_chat(archive) -> None:
    tc, _ = archive
    notice = ["1/8/24, 07:30 - Pat: School closed Friday", "1/8/24, 07:30 - Pat: <Media omitted>"]
    class_3a = _lines(*[f"1/{d}/24, 08:00 - Kim: 3A day {d}" for d in range(2, 8)], *notice)
    assert _upload(tc, "WhatsApp Chat with Parents.txt", class_3a).json()["status"] == "created"
    r = _upload(tc, "WhatsApp Chat with Parents.txt", _lines(*notice))  # a different group: just the forward
    assert r.json()["status"] == "created"
    assert r.json()["id"] == "whatsapp-chat-with-parents-2"


def test_burst_forwarded_into_two_same_named_chats_in_one_minute_is_not_evidence(archive) -> None:
    """Old school-year group's last activity == new group's first activity."""
    tc, _ = archive
    burst = [
        "8/30/24, 07:30 - Pat: New timetable",
        "8/30/24, 07:30 - Pat: <Media omitted>",
        "8/30/24, 07:30 - Pat: <Media omitted>",
    ]
    old_year = _lines(*[f"{m}/1/24, 08:00 - Kim: old year {m}" for m in range(1, 8)], *burst)
    new_year = _lines(*burst, *[f"9/{d}/24, 08:00 - Lee: new year {d}" for d in range(1, 10)])
    assert _upload(tc, "WhatsApp Chat with Class Parents.txt", old_year).json()["status"] == "created"
    r = _upload(tc, "WhatsApp Chat with Class Parents.txt", new_year)
    assert r.json()["status"] == "created"


def test_chats_started_at_different_times_are_never_the_same(archive) -> None:
    tc, _ = archive
    photos = [f"6/30/24, 10:0{i} - Dad: <Media omitted>" for i in range(3)]
    e2e = "Messages and calls are end-to-end encrypted. No one outside of this chat can read them."
    old_family = _lines(f"1/2/20, 09:00 - {e2e}", "1/2/20, 09:01 - Mum: hi", *photos)
    new_family = _lines(f"6/29/24, 18:00 - {e2e}", *photos, *[f"7/{d}/24, 12:00 - Sis: new {d}" for d in range(1, 6)])
    assert _upload(tc, "WhatsApp Chat with Family.txt", old_family).json()["status"] == "created"
    assert _upload(tc, "WhatsApp Chat with Family.txt", new_family).json()["status"] == "created"


def test_reexport_with_the_same_e2e_notice_still_merges(archive) -> None:
    tc, _ = archive
    e2e = "1/2/20, 09:00 - Messages and calls are end-to-end encrypted."
    talk = [f"1/2/20, 09:0{i} - {'Mum' if i % 2 else 'Me'}: line {i}" for i in range(1, 8)]
    assert _upload(tc, "WhatsApp Chat with Mum.txt", _lines(e2e, *talk)).json()["status"] == "created"
    r = _upload(tc, "WhatsApp Chat with Mum.txt", _lines(e2e, *talk, "1/3/20, 10:00 - Mum: next day"))
    assert r.json()["status"] == "updated"
    assert r.json()["new_messages"] == 1


def test_same_named_group_with_only_its_creation_line_is_not_filled_by_another_group(archive) -> None:
    tc, _ = archive
    _upload(tc, "WhatsApp Chat with Book Club.txt", _lines('3/3/23, 09:00 - You created group "Book Club"'))
    other = _lines(*[f"5/{d}/23, 19:00 - Ann: chapter {d}" for d in range(1, 6)])  # joined later: no creation line
    r = _upload(tc, "WhatsApp Chat with Book Club.txt", other)
    assert r.json()["status"] == "created"


def test_same_group_fills_in_an_export_that_had_only_system_lines(archive) -> None:
    tc, _ = archive
    created = '3/3/23, 09:00 - You created group "Book Club"'
    _upload(tc, "WhatsApp Chat with Book Club.txt", _lines(created))
    r = _upload(tc, "WhatsApp Chat with Book Club.txt", _lines(created, "3/3/23, 09:05 - Ann: first!"))
    assert r.json()["status"] == "updated"
    assert r.json()["new_messages"] == 1


def test_new_message_is_not_mistaken_for_a_since_deleted_one_from_someone_else(archive) -> None:
    tc, archive_dir = archive
    fillers = [f"9/1/23, 20:0{i} - Ann: chat {i}" for i in range(10)]
    old = _lines(*fillers, "9/1/23, 20:15 - Bob: <Media omitted>")
    new = _lines(
        *fillers,
        "9/1/23, 20:15 - Bob: This message was deleted",
        "9/1/23, 20:15 - Cat: <Media omitted>",
    )
    _upload(tc, "WhatsApp Chat with Pics2.txt", old)
    r = _upload(tc, "WhatsApp Chat with Pics2.txt", new)
    assert r.json()["status"] == "updated"
    assert r.json()["new_messages"] == 2  # the tombstone and Cat's photo
    text = (archive_dir / "WhatsApp Chat with Pics2.txt").read_text(encoding="utf-8")
    assert "Bob: <Media omitted>" in text and "Cat: <Media omitted>" in text


def test_renamed_file_is_followed_by_delete_and_upload(archive) -> None:
    """Renaming 'Gold & Silver' to 'Gold + Silver' keeps the slug and the mtime."""
    tc, archive_dir = archive
    _upload(tc, "WhatsApp Chat with Gold & Silver.txt", SILVER)
    (archive_dir / "WhatsApp Chat with Gold & Silver.txt").rename(archive_dir / "WhatsApp Chat with Gold + Silver.txt")
    r = tc.delete("/api/chats/whatsapp-chat-with-gold-silver")
    assert r.status_code == 200
    assert not (archive_dir / "WhatsApp Chat with Gold + Silver.txt").exists(), "the renamed file was parked"
    assert (archive_dir / ".removed-chats" / "WhatsApp Chat with Gold + Silver.txt").exists()
    assert "whatsapp-chat-with-gold-silver" not in _chat_ids(tc)


def test_file_locked_during_a_reconcile_is_retried(archive, monkeypatch: pytest.MonkeyPatch) -> None:
    """A sync client or virus scanner holding the file must not hide it until restart."""
    tc, archive_dir = archive
    (archive_dir / "WhatsApp Chat with Silver.txt").write_text(SILVER, encoding="utf-8")
    real_read_bytes = Path.read_bytes
    calls = {"n": 0}

    def locked_once(self: Path) -> bytes:
        if self.name == "WhatsApp Chat with Silver.txt" and calls["n"] == 0:
            calls["n"] += 1
            raise PermissionError("locked by another process")
        return real_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", locked_once)
    _upload(tc, "WhatsApp Chat with Gold.txt", GOLD_V1)  # its reconcile hits the lock
    assert "whatsapp-chat-with-silver" not in _chat_ids(tc)
    r = _upload(tc, "WhatsApp Chat with Silver.txt", SILVER)  # next reconcile retries
    assert r.json()["status"] == "unchanged"
    assert r.json()["id"] == "whatsapp-chat-with-silver"
