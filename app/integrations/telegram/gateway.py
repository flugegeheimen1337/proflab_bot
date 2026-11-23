from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Protocol

from app.services.buttons import Button


@dataclass(frozen=True)
class MemberInfo:
    status: str
    can_send_messages: bool
    can_send_photos: bool
    can_send_videos: bool
    can_send_documents: bool
    is_admin: bool

    @classmethod
    def absent(cls) -> MemberInfo:
        return cls("left", False, False, False, False, False)

    def allows(self, content_type: str) -> bool:
        if self.status in {"left", "kicked"}:
            return False
        if content_type == "text":
            return self.can_send_messages
        if content_type == "photo":
            return self.can_send_messages and self.can_send_photos
        if content_type == "video":
            return self.can_send_messages and self.can_send_videos
        if content_type == "document":
            return self.can_send_messages and self.can_send_documents
        return False

    def to_json(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"))

    @classmethod
    def from_json(cls, raw: str) -> MemberInfo:
        return cls(**json.loads(raw))


@dataclass
class Outgoing:
    chat_id: int
    kind: str
    text: str
    file_id: str | None = None
    filename: str | None = None
    reply_to_message_id: int | None = None
    buttons: tuple[Button, ...] = ()


class TelegramGateway(Protocol):
    async def get_member(self, chat_id: int, user_id: int) -> MemberInfo: ...

    async def send(self, outgoing: Outgoing) -> int: ...

    async def send_dm(self, user_id: int, text: str, buttons: tuple[Button, ...] = ()) -> int: ...

    async def delete_message(self, chat_id: int, message_id: int) -> None: ...
