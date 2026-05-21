import re
from datetime import datetime
from pathlib import Path
from typing import Union

from .models import ChatExport, LinkRef, Message, SystemEvent

# Matches the start of a new entry: "M/D/YY, H:MM - rest"
_ENTRY_RE = re.compile(r"^(\d{1,2}/\d{1,2}/\d{2}), (\d{1,2}:\d{2}) - (.+)$")

# Matches "sender: body" — sender has no colon before the first ": "
_SENDER_RE = re.compile(r"^([^:]+): (.*)$", re.DOTALL)

# WhatsApp inline markup to strip: *bold*, _italic_
_MARKUP_RE = re.compile(r"[*_]([^*_]+)[*_]")

# URL extraction — strips trailing punctuation that's likely not part of the URL
_URL_RE = re.compile(r"https?://\S+")
_URL_TRAIL = re.compile(r"[.,:;!?)>\]]+$")


def _strip_markup(text: str) -> str:
    return _MARKUP_RE.sub(r"\1", text)


def _parse_timestamp(date_str: str, time_str: str) -> datetime:
    return datetime.strptime(f"{date_str} {time_str}", "%m/%d/%y %H:%M")


def _extract_urls(body: str) -> list[str]:
    return [_URL_TRAIL.sub("", u) for u in _URL_RE.findall(body)]


def parse_file(path: Path) -> ChatExport:
    entries: list[Union[Message, SystemEvent]] = []
    current: Union[Message, SystemEvent, None] = None

    with open(path, encoding="utf-8") as fh:
        for raw_line in fh:
            line = raw_line.rstrip("\n").rstrip("\r")

            m = _ENTRY_RE.match(line)
            if m:
                if current is not None:
                    entries.append(current)

                date_part, time_part, rest = m.group(1), m.group(2), m.group(3)
                ts = _parse_timestamp(date_part, time_part)

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
