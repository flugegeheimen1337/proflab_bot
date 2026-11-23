from __future__ import annotations

import uuid
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.time import as_utc, now_utc
from app.db.models import Broadcast, BroadcastDelivery, BroadcastRun, Chat
from app.db.repositories.store import Store
from app.integrations.telegram.gateway import Outgoing, TelegramGateway
from app.services.errors import BotBlocked, NotFound, PermissionDenied, TemporaryTelegramError
from app.services.formatting import neutral_filename, render_broadcast, safe_error
from app.services.moderation import assert_admin
from app.services.queue import JobQueue
from app.services.schedule import due_slot


class BroadcastService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        settings: Settings,
        telegram: TelegramGateway,
        queue: JobQueue,
        bot_username: str,
    ) -> None:
        self.session = session
        self.settings = settings
        self.telegram = telegram
        self.queue = queue
        self.bot_username = bot_username
        self.store = Store(session)

    async def create(
        self,
        *,
        actor_id: int,
        audience: str,
        timing: str,
        chat: Chat | None,
        body: str,
        content_type: str,
        file_id: str | None,
        original_filename: str | None,
        run_at,
        recurrence: str | None,
        weekday: int | None,
        hour: int | None,
        minute: int | None,
    ) -> Broadcast:
        assert_admin(actor_id, self.settings.admin_id_set)
        if audience not in {"group", "direct"} or timing not in {"now", "at", "repeat"}:
            raise NotFound()
        if audience == "group" and chat is None:
            raise NotFound()
        broadcast = Broadcast(
            audience=audience,
            timing=timing,
            chat_id=None if chat is None else chat.id,
            body=body,
            content_type=content_type,
            file_id=file_id,
            neutral_filename=None
            if content_type == "text"
            else neutral_filename(original_filename, kind=content_type),
            run_at=now_utc() if timing == "now" else run_at,
            recurrence=recurrence,
            weekday=weekday,
            hour=hour,
            minute=minute,
            timezone=self.settings.timezone,
            status="active" if timing == "repeat" else "scheduled",
            created_by=actor_id,
        )
        self.session.add(broadcast)
        self.store.add_audit(admin_telegram_id=actor_id, action="broadcast_create", chat_id=broadcast.chat_id)
        await self.session.commit()
        if timing == "now":
            await self.queue.enqueue_broadcast(broadcast.id)
        return broadcast

    async def cancel(self, prefix: str, actor_id: int) -> Broadcast:
        assert_admin(actor_id, self.settings.admin_id_set)
        matches = await self.store.broadcast_by_prefix(prefix)
        if len(matches) != 1:
            raise NotFound()
        broadcast = matches[0]
        broadcast.status = "cancelled"
        broadcast.updated_at = now_utc()
        self.store.add_audit(admin_telegram_id=actor_id, action="broadcast_cancel", detail=str(broadcast.id)[:8])
        await self.session.commit()
        return broadcast

    async def execute(self, broadcast_id: uuid.UUID) -> BroadcastRun | None:
        broadcast = await self.session.get(Broadcast, broadcast_id)
        if broadcast is None or broadcast.status == "cancelled":
            return None
        slot = self._slot(broadcast)
        if slot is None:
            return None
        run = await self._start_run(broadcast, slot)
        if run is None:
            return None
        if broadcast.audience == "group":
            await self._send_group(broadcast, run)
        else:
            await self._send_direct(broadcast, run)
        if broadcast.timing != "repeat":
            broadcast.status = "done"
        broadcast.updated_at = now_utc()
        run.status = "done"
        run.finished_at = now_utc()
        await self.session.commit()
        await self._report(broadcast, run)
        return run

    async def enqueue_due(self) -> int:
        count = 0
        for broadcast in await self.store.due_broadcasts():
            if self._slot(broadcast) is None:
                continue
            await self.queue.enqueue_broadcast(broadcast.id)
            count += 1
        return count

    def _slot(self, broadcast: Broadcast) -> str | None:
        if broadcast.status == "cancelled":
            return None
        if broadcast.timing == "repeat":
            return due_slot(
                recurrence=broadcast.recurrence,
                weekday=broadcast.weekday,
                hour=broadcast.hour,
                minute=broadcast.minute,
                tz_name=broadcast.timezone,
                now=now_utc(),
            )
        if broadcast.status != "scheduled":
            return None
        if broadcast.run_at is not None and as_utc(broadcast.run_at) <= now_utc():
            return "once"
        return None

    async def _start_run(self, broadcast: Broadcast, slot: str) -> BroadcastRun | None:
        try:
            async with self.session.begin_nested():
                run = BroadcastRun(broadcast_id=broadcast.id, slot_key=slot, status="running", started_at=now_utc())
                self.session.add(run)
                await self.session.flush()
        except IntegrityError:
            run = await self.session.scalar(
                select(BroadcastRun).where(BroadcastRun.broadcast_id == broadcast.id, BroadcastRun.slot_key == slot)
            )
            if run is None or run.status == "done":
                return None
        else:
            await self.session.commit()
            return run
        run.status = "running"
        await self.session.commit()
        return run

    async def _send_direct(self, broadcast: Broadcast, run: BroadcastRun) -> None:
        users = await self.store.deliverable_users()
        run.total = len(users)
        await self.session.commit()
        for user in users:
            await self.session.refresh(broadcast)
            if broadcast.status == "cancelled" or user.telegram_id is None:
                continue
            target = f"user:{user.id}"
            if await self.store.delivery_exists(run.id, target):
                continue
            status, error = await self._deliver_dm(user.telegram_id, broadcast)
            if status == "blocked":
                user.bot_blocked = True
                run.blocked += 1
            elif status == "sent":
                run.sent += 1
            else:
                run.failed += 1
            self.session.add(
                BroadcastDelivery(run_id=run.id, target_key=target, status=status, error=error)
            )
            await self.session.commit()

    async def _send_group(self, broadcast: Broadcast, run: BroadcastRun) -> None:
        chat = await self.store.chat_by_id(broadcast.chat_id) if broadcast.chat_id else None
        run.total = 1 if chat else 0
        if chat is None:
            run.failed = 1
            await self.session.commit()
            return
        target = f"chat:{chat.id}"
        if not await self.store.delivery_exists(run.id, target):
            try:
                await self.telegram.send(
                    Outgoing(
                        chat_id=chat.telegram_chat_id,
                        kind=broadcast.content_type,
                        text=render_broadcast(broadcast.body),
                        file_id=broadcast.file_id,
                        filename=broadcast.neutral_filename,
                    )
                )
                status, error = "sent", None
                run.sent = 1
            except TemporaryTelegramError:
                await self.session.commit()
                raise
            except Exception as exc:
                status, error = "failed", safe_error(exc)
                run.failed = 1
            self.session.add(BroadcastDelivery(run_id=run.id, target_key=target, status=status, error=error))
        await self.session.commit()

    async def _deliver_dm(self, telegram_id: int, broadcast: Broadcast) -> tuple[str, str | None]:
        try:
            if broadcast.content_type == "text":
                await self.telegram.send_dm(telegram_id, render_broadcast(broadcast.body))
            else:
                await self.telegram.send(
                    Outgoing(
                        chat_id=telegram_id,
                        kind=broadcast.content_type,
                        text=render_broadcast(broadcast.body),
                        file_id=broadcast.file_id,
                        filename=broadcast.neutral_filename,
                    )
                )
            return "sent", None
        except BotBlocked:
            return "blocked", None
        except TemporaryTelegramError:
            raise
        except Exception as exc:
            return "failed", safe_error(exc)

    async def _report(self, broadcast: Broadcast, run: BroadcastRun) -> None:
        text = (
            "Отчёт рассылки\n"
            f"Адресатов: {run.total}\n"
            f"Отправлено: {run.sent}\n"
            f"Ошибок: {run.failed}\n"
            f"Заблокировали бота: {run.blocked}"
        )
        for admin_id in self.settings.admin_id_set:
            try:
                await self.telegram.send_dm(admin_id, text)
            except Exception:
                continue


def assert_actor(actor_id: int, admins: frozenset[int]) -> None:
    if actor_id not in admins:
        raise PermissionDenied()
