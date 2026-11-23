from __future__ import annotations

import uuid

from app.integrations.telegram.gateway import MemberInfo, Outgoing
from app.services.buttons import Button
from app.services.errors import BotBlocked


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.expire_at: dict[str, float] = {}
        self.now = 0.0

    def _purge(self, key: str) -> None:
        exp = self.expire_at.get(key)
        if exp is not None and self.now >= exp:
            self.values.pop(key, None)
            self.expire_at.pop(key, None)

    async def get(self, key: str):
        self._purge(key)
        return self.values.get(key)

    async def set(self, key: str, value: str, ex: int | None = None, px: int | None = None, nx: bool = False):
        self._purge(key)
        if nx and key in self.values:
            return None
        self.values[key] = str(value)
        if px is not None:
            self.expire_at[key] = self.now + (px / 1000)
        elif ex is not None:
            self.expire_at[key] = self.now + ex
        else:
            self.expire_at.pop(key, None)
        return True

    async def incr(self, key: str) -> int:
        self._purge(key)
        value = int(self.values.get(key, "0")) + 1
        self.values[key] = str(value)
        return value

    async def decr(self, key: str) -> int:
        self._purge(key)
        value = int(self.values.get(key, "0")) - 1
        self.values[key] = str(value)
        return value

    async def expire(self, key: str, seconds: int) -> bool:
        if key not in self.values:
            return False
        self.expire_at[key] = self.now + seconds
        return True

    async def delete(self, key: str) -> None:
        self.values.pop(key, None)
        self.expire_at.pop(key, None)

    async def ping(self) -> bool:
        return True


class InlineQueue:
    def __init__(self) -> None:
        self.jobs: list[uuid.UUID] = []

    async def enqueue_delivery(self, message_id: uuid.UUID) -> None:
        self.jobs.append(message_id)

    async def enqueue_broadcast(self, broadcast_id: uuid.UUID) -> None:
        self.jobs.append(broadcast_id)


class FakeTelegram:
    def __init__(self) -> None:
        self.members: dict[tuple[int, int], MemberInfo] = {}
        self.sent: list[Outgoing] = []
        self.dms: list[tuple[int, str, tuple]] = []
        self.deleted: list[tuple[int, int]] = []
        self.blocked: set[int] = set()
        self.fail_send: Exception | None = None

    def allow(self, chat_id: int, user_id: int, **kwargs) -> None:
        self.members[(chat_id, user_id)] = MemberInfo(
            status=kwargs.get("status", "member"),
            can_send_messages=kwargs.get("can_send_messages", True),
            can_send_photos=kwargs.get("can_send_photos", True),
            can_send_videos=kwargs.get("can_send_videos", True),
            can_send_documents=kwargs.get("can_send_documents", True),
            is_admin=kwargs.get("is_admin", False),
        )

    async def get_member(self, chat_id: int, user_id: int) -> MemberInfo:
        return self.members.get((chat_id, user_id), MemberInfo.absent())

    async def send(self, outgoing: Outgoing) -> int:
        if self.fail_send is not None:
            raise self.fail_send
        self.sent.append(outgoing)
        return len(self.sent)

    async def send_dm(self, user_id: int, text: str, buttons: tuple[Button, ...] = ()) -> int:
        if user_id in self.blocked:
            raise BotBlocked()
        self.dms.append((user_id, text, buttons))
        return len(self.dms)

    async def delete_message(self, chat_id: int, message_id: int) -> None:
        self.deleted.append((chat_id, message_id))
