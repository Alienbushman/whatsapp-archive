import time
from pathlib import Path

import pytest
from rapidfuzz import fuzz, process

from whatsapp_archive.parser import parse_file

FIXTURE = Path(__file__).parent / "fixtures" / "mini_chat.txt"


@pytest.fixture(scope="module")
def chat():
    return parse_file(FIXTURE)


# ── Fuzzy chat-name matching ───────────────────────────────────────────────

def test_fuzzy_chat_name_typo():
    chat_names = {"test-group": "Test Group", "alpha": "Alpha Chat", "beta": "Beta Chat"}
    hits = process.extract("Tset Groop", list(chat_names.values()), scorer=fuzz.WRatio, limit=3, score_cutoff=60)
    assert len(hits) >= 1
    assert hits[0][0] == "Test Group"
    assert hits[0][1] >= 60


def test_fuzzy_chat_name_substring_typo():
    chat_names = {"gold": "WhatsApp Chat with Gold", "silver": "Silver Investors", "crypto": "Crypto Talk"}
    hits = process.extract("golldd", list(chat_names.values()), scorer=fuzz.WRatio, limit=3, score_cutoff=50)
    top_names = [h[0] for h in hits]
    assert "WhatsApp Chat with Gold" in top_names


def test_fuzzy_chat_name_investment_typo():
    chat_names = {"inv": "Investment Club", "other": "Other Chat"}
    hits = process.extract("investmnet", list(chat_names.values()), scorer=fuzz.WRatio, limit=3, score_cutoff=50)
    assert len(hits) >= 1
    assert "Investment Club" in [h[0] for h in hits]


# ── Fuzzy message-body matching ────────────────────────────────────────────

def test_fuzzy_message_match_alice_typo(chat):
    from whatsapp_archive.models import Message
    messages = [e for e in chat.entries if isinstance(e, Message)]
    q = "helloo evreyone"  # typo of "Hello everyone"
    hits = [(fuzz.partial_ratio(q.lower(), m.body.lower()), m) for m in messages]
    hits = [(s, m) for s, m in hits if s >= 60]
    assert len(hits) >= 1, "Should fuzzy-match 'Hello everyone' from Alice"


def test_fuzzy_message_match_final_typo(chat):
    from whatsapp_archive.models import Message
    messages = [e for e in chat.entries if isinstance(e, Message)]
    q = "fnial mesage"  # typo of "Final message"
    hits = [(fuzz.partial_ratio(q.lower(), m.body.lower()), m) for m in messages]
    hits = [(s, m) for s, m in hits if s >= 60]
    assert len(hits) >= 1, "Should fuzzy-match 'Final message' from Bob"


def test_fuzzy_message_match_continuation(chat):
    from whatsapp_archive.models import Message
    messages = [e for e in chat.entries if isinstance(e, Message)]
    q = "continuaton"  # typo of "continuation"
    hits = [(fuzz.partial_ratio(q.lower(), m.body.lower()), m) for m in messages]
    hits_filtered = [(s, m) for s, m in hits if s >= 60]
    assert len(hits_filtered) >= 1, "Should fuzzy-match the continuation message"


# ── Performance: O(N) scan should complete in under 500ms ─────────────────

def test_fuzzy_performance():
    from whatsapp_archive.models import Message
    chat = parse_file(FIXTURE)
    messages = [e for e in chat.entries if isinstance(e, Message)]
    # Repeat to simulate ~1000 messages
    big_messages = messages * (1000 // max(len(messages), 1) + 1)
    big_messages = big_messages[:1000]

    start = time.monotonic()
    q = "wllem"
    hits = [m for m in big_messages if fuzz.partial_ratio(q, m.body.lower()) >= 70]
    elapsed_ms = (time.monotonic() - start) * 1000

    assert elapsed_ms < 500, f"Fuzzy scan took {elapsed_ms:.1f}ms, expected < 500ms"
