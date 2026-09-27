"""Work out what an uploaded export is, and fold re-exports into the chat we have.

WhatsApp can only export a chat's full history, so "adding new messages" means
uploading a newer export of a chat that's already in the archive. An upload is
one of:

- a newer (or partly overlapping) export of an existing chat -> merge it in,
- an export we already fully have -> nothing to do,
- a chat we've never seen -> add it, under a filename that can't clobber
  another chat (two groups can share a name).

Wrongly fusing two chats can't be undone, while a wrongly separate chat can
just be deleted — so matching needs strong evidence, and every rule below errs
towards "new chat". Forwarding one link to several chats puts an identical
(minute, body) message in each of them, so a handful of shared messages proves
nothing.

Merging never drops an entry from either side: entries are matched as a
multiset, and whatever the upload adds is placed next to its neighbours in the
upload's own order.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Iterable, Union

from .models import Message, SystemEvent
from .parser import RawEntry, split_entries

# Browsers suffix repeat downloads: "WhatsApp Chat with Gold (1).txt". Only
# small numbers, so a real name like "Reunion (2019)" is left alone.
_COPY_SUFFIX_RE = re.compile(r"\s*\(\d{1,2}\)$")

# A media message's first line: "<Media omitted>" when exported without media,
# "IMG-20230105-WA0001.jpg (file attached)" / "Report.pdf (file attached)"
# with media. A U+200E (left-to-right mark) sometimes prefixes it.
_MEDIA_RE = re.compile(
    r"^\u200e?(?:<[^<>]+>"
    r"|(?:IMG|VID|AUD|PTT|DOC|STK)-\d{8}-WA\d+\.\w+ \(.+\)"
    r"|.+ \(file attached\))$"
)

# "Alice created group "Gold"" — the group's birth certificate. It keeps the
# original name after renames and carries the creation minute, so two exports
# sharing it are the same group. (English exports only.)
_CREATED_GROUP_RE = re.compile(r"\bcreated (?:this )?group\b")
# "Messages and calls are end-to-end encrypted…" — stored in the chat when it
# started on this phone, so re-exports carry it at the same minute.
_E2E_NOTICE_RE = re.compile(r"end-to-end encrypt", re.IGNORECASE)

# Share of the overlapping period's messages that must match, on BOTH sides.
# Re-exports of one chat score ~1.0 (less the odd edit/deletion); distinct
# chats of the sample archive score <= 0.10 even when full of shared links.
# Falling short only means a separate chat, which can be deleted.
_CONTAINMENT = 0.9
_MIN_SHARED = 3
# When both exports carry the identical "created group" line (same minute,
# creator and original name) they're the same group already; the history
# check then only has to tolerate a small chat's edits and deletions.
_CONTAINMENT_SAME_GROUP = 0.5
# A contact saved between exports of a 1:1 chat renames the export; this many
# of the other person's own messages must match under the old and new name.
_MIN_RENAMED = 3


def base_chat_name(stem: str) -> str:
    """Strip a browser copy suffix: 'Chat with Gold (1)' -> 'Chat with Gold'."""
    return _COPY_SUFFIX_RE.sub("", stem).strip() or stem


def _body_key(body: str) -> str:
    # Exporting "without media" then "with media" renders the same message two
    # ways; keying both as <media> keeps the merge from duplicating it.
    first = body.split("\n", 1)[0]
    return "<media>" if _MEDIA_RE.match(first) else body


def entry_key(entry: Union[Message, SystemEvent]) -> tuple:
    # Sender is deliberately left out: saving a contact between exports turns
    # "+31 6 …" into "Jan", and keying on it would duplicate every message.
    if isinstance(entry, Message):
        return ("m", entry.timestamp, _body_key(entry.body))
    return ("s", entry.timestamp, entry.text)


@dataclass(frozen=True)
class ChatCandidate:
    chat_id: str
    name: str
    messages: Counter
    """Multiset of message entry keys."""
    senders: dict
    """Message key -> frozenset of the senders it appears under."""
    created: frozenset
    """Keys of "created group" events."""
    e2e: frozenset
    """Minutes of end-to-end-encryption notices."""

    @classmethod
    def of(cls, chat_id: str, name: str, entries: Iterable[Union[Message, SystemEvent]]) -> "ChatCandidate":
        messages: Counter = Counter()
        senders: dict[tuple, set[str]] = defaultdict(set)
        created = set()
        e2e = set()
        for e in entries:
            if isinstance(e, Message):
                k = entry_key(e)
                messages[k] += 1
                senders[k].add(e.sender)
            elif _CREATED_GROUP_RE.search(e.text):
                created.add(entry_key(e))
            elif _E2E_NOTICE_RE.search(e.text):
                e2e.add(e.timestamp)
        return cls(
            chat_id, name, messages,
            {k: frozenset(v) for k, v in senders.items()},
            frozenset(created), frozenset(e2e),
        )


def _window(keys: Counter, lo, hi) -> int:
    return sum(n for k, n in keys.items() if lo <= k[1] <= hi)


def same_history(
    a: Counter, b: Counter, containment: float = _CONTAINMENT, min_shared: int = _MIN_SHARED,
) -> bool:
    """Do two exports show the same conversation where their time spans overlap?

    Within the overlapping period, the shared messages must make up most of
    BOTH sides — so a small chat of forwarded links isn't "contained" in a big
    chat that got the same links, and a trimmed export (WhatsApp caps exports)
    still matches the full one. At least `min_shared` messages must match,
    and not all in one minute unless one chat has nothing outside it: a burst
    forwarded to two chats is exactly one minute of shared history.
    """
    if not a or not b:
        return False
    lo = max(min(k[1] for k in a), min(k[1] for k in b))
    hi = min(max(k[1] for k in a), max(k[1] for k in b))
    if lo > hi:
        return False
    shared = a & b
    n_shared = sum(shared.values())
    if n_shared < min_shared or n_shared < containment * max(_window(a, lo, hi), _window(b, lo, hi)):
        return False
    minutes = {k[1] for k in shared}
    if len(minutes) == 1 and min_shared > 1:
        (only,) = minutes
        return all(k[1] == only for k in a) or all(k[1] == only for k in b)
    return True


def _contact_renamed(incoming: ChatCandidate, c: ChatCandidate) -> bool:
    """Is `incoming` a 1:1 chat re-exported after its contact was saved or renamed?

    "WhatsApp Chat with +31 6 …" becomes "WhatsApp Chat with Jan". Evidence a
    broadcast list can't fake: the names that differ between the filenames are
    exactly the other person's sender name in each export, their own messages
    match under old and new name, and no other sender differs.
    """
    old_name, new_name = base_chat_name(c.name), base_chat_name(incoming.name)
    shared = incoming.messages & c.messages
    for old in {s for k in shared for s in c.senders[k]}:
        if not old_name.endswith(old):
            continue
        prefix = old_name[: len(old_name) - len(old)]
        if not new_name.startswith(prefix):
            continue
        new = new_name[len(prefix):]
        if not new or new == old:
            continue
        renamed = 0
        for k in shared:
            before = {new if s == old else s for s in c.senders[k]}
            if before != incoming.senders[k]:
                break
            renamed += old in c.senders[k]
        else:
            if renamed >= _MIN_RENAMED:
                return True
    return False


def _started_apart(incoming: ChatCandidate, c: ChatCandidate) -> bool:
    """Both exports know when their chat began, and it wasn't the same minute."""
    if incoming.created and c.created and not (
        {k[1] for k in incoming.created} & {k[1] for k in c.created}
    ):
        return True  # minute only: the creator's display name can change
    return bool(incoming.e2e and c.e2e and not (incoming.e2e & c.e2e))


def pick_target(incoming: ChatCandidate, candidates: Iterable[ChatCandidate]) -> str | None:
    """Return the chat_id an upload belongs to, or None if it's a new chat.

    Chats that began at different times are never the same. Otherwise:
    identical "created group" line (a renamed group too) -> matching history,
    leniently; same (copy-suffix-insensitive) name -> matching history; a 1:1
    chat whose contact was saved in between (see _contact_renamed) -> that plus
    matching history. A same-named chat with no messages and nothing to date it
    (e.g. a file that didn't parse before) is filled in by the upload.
    """
    base = base_chat_name(incoming.name).casefold()
    best: tuple[int, bool] | None = None
    best_id: str | None = None
    for c in candidates:
        if _started_apart(incoming, c):
            continue
        same_group = bool(incoming.created & c.created)
        same_name = base_chat_name(c.name).casefold() == base
        if same_group:
            eligible = (
                not c.messages or not incoming.messages
                or same_history(incoming.messages, c.messages, _CONTAINMENT_SAME_GROUP, min_shared=1)
            )
        elif same_name and not (c.messages or c.created or c.e2e):
            eligible = True
        elif same_name:
            eligible = same_history(incoming.messages, c.messages)
        else:
            eligible = (
                not incoming.created and not c.created
                and _contact_renamed(incoming, c)
                and same_history(incoming.messages, c.messages)
            )
        if not eligible:
            continue
        score = (sum((incoming.messages & c.messages).values()), same_name)
        if best is None or score > best:
            best, best_id = score, c.chat_id
    return best_id


@dataclass(frozen=True)
class MergeResult:
    added: int
    """Entries (messages and system events) in the upload that the existing chat didn't have."""
    added_messages: int
    text: str | None
    """Merged export text, or None when the upload adds nothing."""
    verbatim: bool
    """True when the upload contains everything the existing chat has, so the
    upload itself can be written as-is (keeps the file a pristine export)."""


def _by_time(old: list[RawEntry], added: list[RawEntry]) -> list[RawEntry]:
    # Two-pointer merge rather than a sort: each side keeps its own order.
    out: list[RawEntry] = []
    i = j = 0
    while i < len(old) and j < len(added):
        if added[j].entry.timestamp < old[i].entry.timestamp:
            out.append(added[j])
            j += 1
        else:
            out.append(old[i])
            i += 1
    out.extend(old[i:])
    out.extend(added[j:])
    return out


def _sender(entry: Union[Message, SystemEvent]) -> str | None:
    return entry.sender if isinstance(entry, Message) else None


def merge_exports(existing_text: str, incoming_text: str) -> MergeResult:
    """Merge an uploaded export into an existing one without losing entries."""
    preamble, old = split_entries(existing_text)
    _, new = split_entries(incoming_text)

    unmatched: dict[tuple, list[int]] = defaultdict(list)
    for i, r in enumerate(old):
        unmatched[entry_key(r.entry)].append(i)
    old_senders = {s for r in old if (s := _sender(r.entry)) is not None}
    new_senders = {s for r in new if (s := _sender(r.entry)) is not None}

    def claim(r: RawEntry) -> int | None:
        """The existing entry this upload entry is, if any."""
        slots = unmatched.get(entry_key(r.entry))
        if not slots:
            return None
        sender = _sender(r.entry)
        for n, i in enumerate(slots):
            if _sender(old[i].entry) == sender:
                return slots.pop(n)
        # A different sender may only take the slot if it looks like a contact
        # rename ("+31 6 …" -> "Jan"): the old name is gone from the upload and
        # the new one never appears in the archived copy. Otherwise Cat's photo
        # could pass for Bob's since-deleted photo from the same minute.
        if sender not in old_senders:
            for n, i in enumerate(slots):
                if _sender(old[i].entry) not in new_senders:
                    return slots.pop(n)
        return None

    # Anchor each added entry right after the existing entry that precedes it
    # in the upload — the export's own order beats timestamps, which can tie
    # within a minute or be skewed. Entries before the first match fall back
    # to timestamp order.
    added: list[RawEntry] = []
    leading: list[RawEntry] = []
    after: dict[int, list[RawEntry]] = defaultdict(list)
    matched: set[int] = set()
    first_anchor: int | None = None
    anchor: int | None = None
    for r in new:
        i = claim(r)
        if i is not None:
            matched.add(i)
            anchor = i if anchor is None else max(anchor, i)
            if first_anchor is None:
                first_anchor = i
        else:
            added.append(r)
            (leading if anchor is None else after[anchor]).append(r)

    n_messages = sum(1 for r in added if isinstance(r.entry, Message))
    if not added:
        return MergeResult(added=0, added_messages=0, text=None, verbatim=False)
    # A non-blank preamble is existing content we couldn't parse — keep it by
    # merging rather than overwriting with the upload.
    if len(matched) == len(old) and not preamble.strip():
        return MergeResult(added=len(added), added_messages=n_messages, text=incoming_text, verbatim=True)

    # Old entries the upload no longer has (edited, deleted for everyone —
    # usually the older export's last few) sit between matched ones; place the
    # additions among them by time, or they'd all queue up behind the newest.
    split = len(old) if first_anchor is None else first_anchor
    merged = _by_time(old[:split], leading)
    i = split
    while i < len(old):
        merged.append(old[i])
        j = i + 1
        while j < len(old) and j not in matched:
            j += 1
        merged.extend(_by_time(old[i + 1:j], after.get(i, [])))
        i = j
    text = preamble + "".join(r.raw if r.raw.endswith("\n") else r.raw + "\n" for r in merged)
    return MergeResult(added=len(added), added_messages=n_messages, text=text, verbatim=False)
