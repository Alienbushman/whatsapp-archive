from datetime import datetime
from pathlib import Path
from typing import Union

from pydantic import BaseModel


class Message(BaseModel):
    timestamp: datetime
    sender: str
    body: str
    is_deleted: bool


class SystemEvent(BaseModel):
    timestamp: datetime
    text: str


class LinkRef(BaseModel):
    url: str
    message_index: int
    sender: str
    timestamp: datetime


class ChatExport(BaseModel):
    source_file: Path
    entries: list[Union[Message, SystemEvent]]
    links: list[LinkRef] = []
