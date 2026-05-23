from pathlib import Path

import pytest

from whatsapp_archive.models import Message, SystemEvent
from whatsapp_archive.parser import parse_file

FIXTURE = Path(__file__).parent / "fixtures" / "mini_chat.txt"


@pytest.fixture(scope="module")
def export():
    return parse_file(FIXTURE)


def test_total_entry_count(export):
    assert len(export.entries) == 7


def test_first_entry_is_system_event(export):
    entry = export.entries[0]
    assert isinstance(entry, SystemEvent)
    assert "created group" in entry.text


def test_normal_message(export):
    entry = export.entries[1]
    assert isinstance(entry, Message)
    assert entry.sender == "Alice"
    assert entry.body == "Hello everyone"


def test_multi_line_body(export):
    entry = export.entries[2]
    assert isinstance(entry, Message)
    assert entry.body == "Hi there\nThis is a continuation of Bob's message"


def test_phone_number_sender(export):
    entry = export.entries[3]
    assert isinstance(entry, Message)
    assert entry.sender == "+1 555 000 0001"


def test_deleted_message(export):
    entry = export.entries[4]
    assert isinstance(entry, Message)
    assert entry.is_deleted is True


def test_last_entry_no_trailing_newline(export):
    entry = export.entries[5]
    assert isinstance(entry, Message)
    assert entry.sender == "Bob"
    assert entry.body == "Final message"


def test_timestamps(export):
    entry = export.entries[1]
    assert isinstance(entry, Message)
    assert entry.timestamp.year == 2023
    assert entry.timestamp.month == 1
    assert entry.timestamp.day == 5
    assert entry.timestamp.hour == 9
    assert entry.timestamp.minute == 1


# ── ISO date format (YYYY/MM/DD) — newer Android default in many locales ────

ISO_FIXTURE = Path(__file__).parent / "fixtures" / "mini_chat_iso.txt"


@pytest.fixture(scope="module")
def iso_export():
    return parse_file(ISO_FIXTURE)


def test_iso_parses_all_entries(iso_export):
    """Regression for client-install bug: chats showed up but had 0 messages
    because the parser only knew M/D/YY format."""
    assert len(iso_export.entries) == 6


def test_iso_first_entry_is_encryption_event(iso_export):
    entry = iso_export.entries[0]
    assert isinstance(entry, SystemEvent)
    assert "end-to-end encrypted" in entry.text


def test_iso_messages_parse_with_sender_and_body(iso_export):
    msg = iso_export.entries[2]
    assert isinstance(msg, Message)
    assert msg.sender == "Alice"
    assert msg.body == "First message in ISO date format"


def test_iso_timestamps_are_correct(iso_export):
    """YYYY/MM/DD interpreted as year-month-day, not month-day-year."""
    msg = iso_export.entries[2]
    assert msg.timestamp.year == 2025
    assert msg.timestamp.month == 1
    assert msg.timestamp.day == 24
    # And the cross-month one
    last = iso_export.entries[5]
    assert last.timestamp.year == 2025
    assert last.timestamp.month == 2
    assert last.timestamp.day == 15


def test_iso_multiline_body_continuation(iso_export):
    msg = iso_export.entries[4]
    assert isinstance(msg, Message)
    assert "Multiline" in msg.body
    assert "this line continues the previous" in msg.body


def test_iso_url_extraction(iso_export):
    assert any("https://example.com/foo" in lnk.url for lnk in iso_export.links)


# ── More locale variants — robust parser should handle without breaking ─────

def _parse_inline(text: str):
    """Helper: write a temp file with `text`, parse, return entries."""
    import tempfile, os
    fd, path = tempfile.mkstemp(suffix=".txt", text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        return parse_file(Path(path))
    finally:
        os.unlink(path)


def test_format_iso_hyphen():
    """2025-01-24, 08:51 - Alice: hi"""
    ex = _parse_inline("2025-01-24, 08:51 - Alice: hi\n")
    assert len(ex.entries) == 1
    assert isinstance(ex.entries[0], Message)
    assert ex.entries[0].timestamp.year == 2025
    assert ex.entries[0].timestamp.month == 1
    assert ex.entries[0].timestamp.day == 24


def test_format_eu_slash_4y():
    """24/01/2025, 08:51 - Alice: hi  (EU, day-first, 4-digit year)"""
    ex = _parse_inline("24/01/2025, 08:51 - Alice: hi\n")
    assert len(ex.entries) == 1
    assert ex.entries[0].timestamp.day == 24
    assert ex.entries[0].timestamp.month == 1


def test_format_us_slash_4y():
    """01/24/2025, 08:51 - Alice: hi  (US, month-first, 4-digit year)
    Day > 12 makes this unambiguously US — should not be misread as EU."""
    ex = _parse_inline("01/24/2025, 08:51 - Alice: hi\n")
    assert len(ex.entries) == 1
    assert ex.entries[0].timestamp.month == 1
    assert ex.entries[0].timestamp.day == 24


def test_format_eu_dot_4y():
    """24.01.2025, 08:51 - Alice: hi  (German/Dutch convention)"""
    ex = _parse_inline("24.01.2025, 08:51 - Alice: hi\n")
    assert len(ex.entries) == 1
    assert ex.entries[0].timestamp.day == 24
    assert ex.entries[0].timestamp.month == 1
    assert ex.entries[0].timestamp.year == 2025


def test_format_eu_slash_2y_unambiguous():
    """24/01/25 — day > 12 forces EU interpretation"""
    ex = _parse_inline("24/01/25, 08:51 - Alice: hi\n")
    assert len(ex.entries) == 1
    assert ex.entries[0].timestamp.day == 24


def test_format_us_slash_2y_original():
    """Original M/D/YY format must still work — backward compat."""
    ex = _parse_inline("1/5/23, 9:01 - Alice: hi\n")
    assert len(ex.entries) == 1
    # Either US or EU could read this; falls back to US (last in order).
    # The important thing is it doesn't crash and produces ONE entry.
    assert ex.entries[0].timestamp.year == 2023


def test_format_unknown_date_treated_as_continuation():
    """A line with a regex-shape-matching but unparseable date (99/99/99) should
    be silently treated as a continuation rather than crashing the import."""
    text = (
        "2025/01/24, 09:00 - Alice: real message\n"
        "99/99/99, 99:99 - junk continuation\n"  # malformed; should fold in
    )
    # The malformed line has regex shape "<date>, <time>" but bad components.
    # Note: 99:99 fails the time regex too (\d{2}), so it'll never match in
    # the first place. The real test is malformed date with valid time.
    ex = _parse_inline(
        "2025/01/24, 09:00 - Alice: real\n"
        "55/99/22, 09:01 - this looks like an entry but isn't valid\n"
    )
    # We should get the one valid entry, with the malformed line absorbed.
    assert len(ex.entries) == 1
    assert "real" in ex.entries[0].body
    # The malformed line gets appended as a continuation of body
    assert "55/99/22" in ex.entries[0].body
