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
