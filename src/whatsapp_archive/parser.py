import re
from datetime import datetime
from pathlib import Path
from typing import Union

from .models import ChatExport, LinkRef, Message, SystemEvent

# Matches the start of a new entry. WhatsApp exports use a wide variety of
# date formats depending on the phone's locale and OS version, all sharing
# the shape "<date>, <time> - <rest>". We accept any date with 1-4 digits per
# component and slash / hyphen / dot separators, then let _parse_timestamp
# work out which actual format it is.
_ENTRY_RE = re.compile(r"^(\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4}), (\d{1,2}:\d{2})(?:\s?[APap]\.?[Mm]\.?)? - (.+)$")

# Matches "sender: body" — sender has no colon before the first ": "
_SENDER_RE = re.compile(r"^([^:]+): (.*)$", re.DOTALL)

# WhatsApp inline markup to strip: *bold*, _italic_
_MARKUP_RE = re.compile(r"[*_]([^*_]+)[*_]")

# URL extraction — strips trailing punctuation that's likely not part of the URL
_URL_RE = re.compile(r"https?://\S+")
_URL_TRAIL = re.compile(r"[.,:;!?)>\]]+$")

# Date formats tried in order. Strictness ordering is critical:
#   1. ISO-style 4-digit-year-first (`%Y/%m/%d` and `%Y-%m-%d`) is unambiguous
#      — anything starting with 4 digits >= 1900 is definitely a year.
#   2. EU-style with 4-digit year is tried before US — both shapes can match
#      "31/01/2025" but only EU is correct (31 is not a valid month). US with
#      4-digit year is the natural fallback for `01/24/2025`.
#   3. Dot-separated 2-digit-year EU is tried before slash-separated US
#      2-digit-year — dots are uncommon in US locales, so a dot date is
#      almost certainly EU.
#   4. Slash-separated 2-digit-year US is the historical default and stays
#      last to preserve backward compatibility for old exports.
# A 12-hour time with AM/PM is allowed in the regex but only `%H:%M` (24-hour)
# is needed for strptime — the regex strips the AM/PM via the optional group.
_DATE_FORMATS = (
    # 4-digit-year formats first — year position is unambiguous.
    "%Y/%m/%d %H:%M",  # ISO slash:    2025/01/24
    "%Y-%m-%d %H:%M",  # ISO hyphen:   2025-01-24
    "%d.%m.%Y %H:%M",  # EU dot 4y:    24.01.2025  (dots are EU-only)
    "%d-%m-%Y %H:%M",  # EU hyphen 4y: 24-01-2025
    "%d/%m/%Y %H:%M",  # EU slash 4y:  24/01/2025  (works iff day > 12 OR month > 12)
    "%m/%d/%Y %H:%M",  # US slash 4y:  01/24/2025  (fallback for ambiguous DD<=12)
    # 2-digit-year formats — order matters for ambiguous strings like 1/5/23.
    # Historical default is US, keep it that way for backward compat. The
    # EU 2y variants come AFTER US 2y, so they only fire for strings US can't
    # parse (e.g. 24/01/25 — month=24 is invalid US).
    "%d.%m.%y %H:%M",  # EU dot 2y:    24.01.25    (dots disambiguate)
    "%m/%d/%y %H:%M",  # US slash 2y:  1/24/25     (original default — preserves backward compat)
    "%d/%m/%y %H:%M",  # EU slash 2y:  24/01/25    (matches only when US failed)
)


def _strip_markup(text: str) -> str:
    return _MARKUP_RE.sub(r"\1", text)


def _parse_timestamp(date_str: str, time_str: str) -> datetime:
    """Try each known WhatsApp date format until one parses.

    Order matters — see comment on _DATE_FORMATS. We try unambiguous formats
    (ISO with 4-digit year) first, then 4-digit-year locale variants, then
    the historical US 2-digit-year default last.

    Raises ValueError if no format matches; callers should treat the line as
    a body continuation rather than crashing the import.
    """
    combined = f"{date_str} {time_str}"
    last_err: Exception | None = None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(combined, fmt)
        except ValueError as e:
            last_err = e
    raise ValueError(f"Unrecognized WhatsApp timestamp: {combined!r}") from last_err


def _extract_urls(body: str) -> list[str]:
    return [_URL_TRAIL.sub("", u) for u in _URL_RE.findall(body)]


def parse_file(path: Path) -> ChatExport:
    entries: list[Union[Message, SystemEvent]] = []
    current: Union[Message, SystemEvent, None] = None

    with open(path, encoding="utf-8") as fh:
        for raw_line in fh:
            line = raw_line.rstrip("\n").rstrip("\r")

            m = _ENTRY_RE.match(line)
            ts: datetime | None = None
            if m:
                # A regex match doesn't guarantee a valid date (e.g. 99/99/99
                # would match the shape but fail to parse). Treat unparseable
                # dates as continuations of the prior message rather than
                # crashing the whole import — keeps partial chats usable.
                try:
                    ts = _parse_timestamp(m.group(1), m.group(2))
                except ValueError:
                    m = None
            if m and ts is not None:
                if current is not None:
                    entries.append(current)

                date_part, time_part, rest = m.group(1), m.group(2), m.group(3)

                sender_m = _SENDER_RE.match(rest)
                if sender_m:
                    sender = sender_m.group(1)
                    body = _strip_markup(sender_m.group(2))
                    current = Message(
                        timestamp=ts,
                        sender=sender,
                        body=body,
                        is_deleted=body.strip() == "This message was deleted",
                    )
                else:
                    current = SystemEvent(timestamp=ts, text=_strip_markup(rest))
            else:
                if isinstance(current, Message):
                    current = current.model_copy(
                        update={"body": current.body + "\n" + line}
                    )

    if current is not None:
        entries.append(current)

    # Build link catalog
    links: list[LinkRef] = []
    for idx, entry in enumerate(entries):
        if isinstance(entry, Message):
            for url in _extract_urls(entry.body):
                links.append(LinkRef(
                    url=url,
                    message_index=idx,
                    sender=entry.sender,
                    timestamp=entry.timestamp,
                ))

    return ChatExport(source_file=path, entries=entries, links=links)
