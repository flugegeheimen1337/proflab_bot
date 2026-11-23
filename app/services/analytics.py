from __future__ import annotations

import csv
import html
import io
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import bounds, now_utc
from app.db.repositories.store import Store

LABELS = (
    ("registered_users", "Зарегистрированы"),
    ("active_users", "Активные"),
    ("messages", "Сообщения"),
    ("replies", "Ответы"),
    ("new_aliases", "Новые псевдонимы"),
    ("reports", "Жалобы"),
    ("bans", "Блокировки"),
    ("from_private", "Из личных сообщений"),
    ("from_ephemeral", "Скрытыми командами"),
    ("from_intercept", "Режим перехвата"),
    ("broadcast_sent", "Рассылка: отправлено"),
    ("broadcast_failed", "Рассылка: ошибки"),
    ("broadcast_blocked", "Рассылка: заблокировали бота"),
)


class AnalyticsService:
    def __init__(self, session: AsyncSession, tz_name: str) -> None:
        self.store = Store(session)
        self.tz_name = tz_name

    async def overview(
        self,
        chat_id: uuid.UUID | None,
        start: datetime | None,
        end: datetime | None,
    ) -> dict[str, int]:
        sent, failed, blocked = await self.store.broadcast_totals(start, end)
        return {
            "registered_users": await self.store.count_users(),
            "active_users": await self.store.count_active_authors(chat_id, start, end),
            "messages": await self.store.count_messages(chat_id, start, end),
            "replies": await self.store.count_messages(chat_id, start, end, replies=True),
            "new_aliases": await self.store.count_aliases(chat_id, start, end),
            "reports": await self.store.count_reports(chat_id, start, end),
            "bans": await self.store.count_bans(chat_id, start, end),
            "from_private": await self.store.count_messages(chat_id, start, end, source="private"),
            "from_ephemeral": await self.store.count_messages(chat_id, start, end, source="ephemeral"),
            "from_intercept": await self.store.count_messages(chat_id, start, end, source="intercept"),
            "broadcast_sent": sent,
            "broadcast_failed": failed,
            "broadcast_blocked": blocked,
        }

    async def period(self, chat_id: uuid.UUID | None, kind: str) -> tuple[str, dict[str, int]]:
        moment = now_utc()
        if kind == "today":
            start, end = bounds(self.tz_name, moment, days=1)
            title = "Сегодня"
        elif kind == "week":
            start, end = bounds(self.tz_name, moment, days=7)
            title = "7 дней"
        elif kind == "month":
            start, end = bounds(self.tz_name, moment, month=True)
            title = "Этот месяц"
        else:
            start, end = None, None
            title = "За всё время"
        return title, await self.overview(chat_id, start, end)

    async def daily_csv(self, chat_id: uuid.UUID | None, days: int = 30) -> str:
        moment = now_utc()
        buffer = io.StringIO()
        writer = csv.DictWriter(
            buffer,
            fieldnames=["date", "messages", "replies", "active_users", "new_aliases", "reports", "bans"],
        )
        writer.writeheader()
        zone = ZoneInfo(self.tz_name)
        for offset in range(days - 1, -1, -1):
            day = moment - timedelta(days=offset)
            start, end = bounds(self.tz_name, day, days=1)
            writer.writerow(
                {
                    "date": day.astimezone(zone).date().isoformat(),
                    "messages": await self.store.count_messages(chat_id, start, end),
                    "replies": await self.store.count_messages(chat_id, start, end, replies=True),
                    "active_users": await self.store.count_active_authors(chat_id, start, end),
                    "new_aliases": await self.store.count_aliases(chat_id, start, end),
                    "reports": await self.store.count_reports(chat_id, start, end),
                    "bans": await self.store.count_bans(chat_id, start, end),
                }
            )
        return buffer.getvalue()


def format_stats(title: str, chat_title: str, data: dict[str, int]) -> str:
    lines = [f"<b>{html.escape(title)}</b>", html.escape(chat_title), ""]
    for key, label in LABELS:
        lines.append(f"{label}: {int(data.get(key, 0))}")
    lines.append("")
    lines.append("Прочтения сообщений в группе Bot API не отдаёт, поэтому их здесь нет.")
    return "\n".join(lines)
