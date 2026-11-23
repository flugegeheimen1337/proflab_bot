from __future__ import annotations

import html

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, Message
from aiogram.filters.base import Filter

from app.bot.keyboards import admin_menu, broadcast_audience, broadcast_timing, confirm_broadcast
from app.bot.states import BroadcastForm
from app.bot.texts import explain_error
from app.context import AppContext
from app.core.security import pack
from app.core.time import now_utc
from app.db.models import Report
from app.db.repositories.store import Store
from app.services.aliases import AliasService
from app.services.analytics import AnalyticsService, format_stats
from app.services.broadcasts import BroadcastService
from app.services.chats import ChatService
from app.services.errors import DomainError
from app.services.formatting import render_broadcast
from app.services.limiter import depth
from app.services.moderation import ModerationService
from app.services.schedule import ScheduleError, parse_recurrence, parse_run_at
from app.services.users import UserService

router = Router(name="admin")


class AdminFilter(Filter):
    async def __call__(self, event: Message | CallbackQuery, app: AppContext) -> bool:
        user = event.from_user
        return bool(user and user.id in app.settings.admin_id_set)


router.message.filter(F.chat.type == "private", AdminFilter())


def _service_bundle(session, app: AppContext):
    return {
        "chats": ChatService(session, app.settings),
        "moderation": ModerationService(session),
        "analytics": AnalyticsService(session, app.settings.timezone),
        "broadcasts": BroadcastService(
            session,
            settings=app.settings,
            telegram=app.telegram,
            queue=app.queue,
            bot_username=app.bot_username,
        ),
        "users": UserService(session),
        "aliases": AliasService(session),
        "store": Store(session),
    }


async def _chat_for_admin(session, app: AppContext, telegram_id: int):
    user = await UserService(session).require(telegram_id)
    return await ChatService(session, app.settings).resolve_for_admin(user.active_chat_id, telegram_id), user


async def _stats(message: Message, session, app: AppContext, kind: str) -> None:
    chat, _user = await _chat_for_admin(session, app, message.from_user.id)
    analytics = AnalyticsService(session, app.settings.timezone)
    title, data = await analytics.period(None if chat is None else chat.id, kind)
    chat_title = "Все группы" if chat is None else (chat.title or "Группа")
    await message.answer(format_stats(title, chat_title, data))


@router.message(Command("admin"))
async def admin_home(message: Message, app: AppContext) -> None:
    await message.answer("Управление. Telegram ID участников здесь не показывается.", reply_markup=admin_menu(app.settings.secret()))


@router.message(Command("stats"))
async def stats_all(message: Message, session, app: AppContext) -> None:
    await _stats(message, session, app, "all")


@router.message(Command("stats_today"))
async def stats_today(message: Message, session, app: AppContext) -> None:
    await _stats(message, session, app, "today")


@router.message(Command("stats_week"))
async def stats_week(message: Message, session, app: AppContext) -> None:
    await _stats(message, session, app, "week")


@router.message(Command("stats_month"))
async def stats_month(message: Message, session, app: AppContext) -> None:
    await _stats(message, session, app, "month")


@router.message(Command("export_stats"))
async def export_stats(message: Message, session, app: AppContext) -> None:
    chat, _user = await _chat_for_admin(session, app, message.from_user.id)
    csv_text = await AnalyticsService(session, app.settings.timezone).daily_csv(None if chat is None else chat.id)
    await message.answer_document(
        BufferedInputFile(csv_text.encode("utf-8"), filename="stats.csv"),
        caption="Статистика по дням. Идентификаторов Telegram в файле нет.",
    )


@router.message(Command("groups"))
async def groups_cmd(message: Message, session, app: AppContext) -> None:
    chats = await Store(session).active_chats()
    if not chats:
        await message.answer("Подключённых групп нет. В группе выполните /link_group.")
        return
    lines = [f"• {chat.title or 'без названия'} ({chat.public_code})" for chat in chats]
    await message.answer("Группы:\n" + "\n".join(lines))


@router.message(Command("queue"))
async def queue_cmd(message: Message, session, app: AppContext) -> None:
    lines = []
    for chat in await Store(session).active_chats():
        lines.append(f"• {chat.title or 'группа'}: {await depth(app.redis, f'cdepth:{chat.id}')}")
    await message.answer("Очередь публикации:\n" + ("\n".join(lines) if lines else "пусто"))


@router.message(Command("complaints"))
async def complaints_cmd(message: Message, session) -> None:
    reports = await Store(session).open_reports()
    if not reports:
        await message.answer("Открытых жалоб нет.")
        return
    lines = []
    for report in reports:
        row = await Store(session).message_by_id(report.message_id)
        alias = None if row is None else await Store(session).alias_for_user(row.chat_id, row.author_id)
        label = alias.label if alias else "неизвестный псевдоним"
        lines.append(f"• {label}, код {row.public_code if row else '—'}")
    await message.answer("Жалобы:\n" + "\n".join(lines))


@router.message(Command("errors"))
async def errors_cmd(message: Message, session) -> None:
    rows = await Store(session).recent_failed()
    if not rows:
        await message.answer("Ошибок в очереди нет.")
        return
    lines = [f"• {row.created_at:%Y-%m-%d %H:%M} {row.job_name}: {row.error}" for row in rows]
    await message.answer("Последние ошибки:\n" + "\n".join(lines))


async def _number(message: Message) -> tuple[int, int | None] | None:
    parts = (message.text or "").split()
    if len(parts) < 2:
        await message.answer("Укажите номер псевдонима, например /block 104.")
        return None
    try:
        number = int(parts[1])
    except ValueError:
        await message.answer("Номер псевдонима должен быть числом.")
        return None
    minutes = None
    if len(parts) > 2:
        try:
            minutes = int(parts[2])
        except ValueError:
            await message.answer("Минуты блокировки должны быть числом.")
            return None
    return number, minutes


@router.message(Command("block"))
async def block_cmd(message: Message, session, app: AppContext) -> None:
    parsed = await _number(message)
    chat, _user = await _chat_for_admin(session, app, message.from_user.id)
    if parsed is None or chat is None:
        if parsed is not None:
            await message.answer("Сначала выберите группу через /chats.")
        return
    number, minutes = parsed
    try:
        label = await ModerationService(session).block_alias(
            chat=chat,
            number=number,
            admin_id=message.from_user.id,
            admins=app.settings.admin_id_set,
            minutes=minutes,
            reason="admin",
        )
    except DomainError as exc:
        await message.answer(explain_error(exc))
        return
    await message.answer(f"{label} заблокирован в этой группе.")


@router.message(Command("unblock"))
async def unblock_cmd(message: Message, session, app: AppContext) -> None:
    parsed = await _number(message)
    chat, _user = await _chat_for_admin(session, app, message.from_user.id)
    if parsed is None or chat is None:
        if parsed is not None:
            await message.answer("Сначала выберите группу через /chats.")
        return
    try:
        label = await ModerationService(session).unblock_alias(
            chat=chat, number=parsed[0], admin_id=message.from_user.id, admins=app.settings.admin_id_set
        )
    except DomainError as exc:
        await message.answer(explain_error(exc))
        return
    await message.answer(f"{label} снова может писать.")


@router.message(Command("gblock"))
async def gblock_cmd(message: Message, session, app: AppContext) -> None:
    parsed = await _number(message)
    chat, _user = await _chat_for_admin(session, app, message.from_user.id)
    if parsed is None or chat is None:
        if parsed is not None:
            await message.answer("Сначала выберите группу через /chats.")
        return
    number, minutes = parsed
    try:
        label = await ModerationService(session).global_block(
            chat=chat,
            number=number,
            admin_id=message.from_user.id,
            admins=app.settings.admin_id_set,
            minutes=minutes,
            reason="admin",
        )
    except DomainError as exc:
        await message.answer(explain_error(exc))
        return
    await message.answer(f"{label} заблокирован во всех группах.")


@router.message(Command("gunblock"))
async def gunblock_cmd(message: Message, session, app: AppContext) -> None:
    parsed = await _number(message)
    chat, _user = await _chat_for_admin(session, app, message.from_user.id)
    if parsed is None or chat is None:
        if parsed is not None:
            await message.answer("Сначала выберите группу через /chats.")
        return
    try:
        label = await ModerationService(session).global_unblock(
            chat=chat, number=parsed[0], admin_id=message.from_user.id, admins=app.settings.admin_id_set
        )
    except DomainError as exc:
        await message.answer(explain_error(exc))
        return
    await message.answer(f"Общая блокировка {label} снята.")


@router.message(Command("reveal"))
async def reveal_cmd(message: Message, session, app: AppContext) -> None:
    parsed = await _number(message)
    chat, _user = await _chat_for_admin(session, app, message.from_user.id)
    if parsed is None or chat is None:
        if parsed is not None:
            await message.answer("Сначала выберите группу через /chats.")
        return
    alias = await AliasService(session).by_number(chat, parsed[0])
    if alias is None:
        await message.answer("Псевдоним не найден.")
        return
    user = await Store(session).user_by_id(alias.user_id)
    if user is None:
        await message.answer("Участник не найден.")
        return
    Store(session).add_audit(
        admin_telegram_id=message.from_user.id,
        action="reveal",
        alias_label=alias.label,
        chat_id=chat.id,
        detail="allowed" if app.settings.reveal_telegram_ids else "denied",
    )
    await message.answer(ModerationService(session).reveal_text(user, alias.label, allowed=app.settings.reveal_telegram_ids))


@router.message(Command("purge"))
async def purge_cmd(message: Message, session, app: AppContext) -> None:
    parsed = await _number(message)
    chat, _user = await _chat_for_admin(session, app, message.from_user.id)
    if parsed is None or chat is None:
        if parsed is not None:
            await message.answer("Сначала выберите группу через /chats.")
        return
    alias = await AliasService(session).by_number(chat, parsed[0])
    if alias is None:
        await message.answer("Псевдоним не найден.")
        return
    user = await Store(session).user_by_id(alias.user_id)
    if user is None:
        await message.answer("Участник не найден.")
        return
    await UserService(session).purge(user)
    Store(session).add_audit(admin_telegram_id=message.from_user.id, action="purge", alias_label=alias.label, chat_id=chat.id)
    await message.answer(f"{alias.label}: связь с Telegram ID удалена. Сообщения в группе останутся под псевдонимом.")


@router.message(Command("spam"))
async def spam_cmd(message: Message, session, app: AppContext) -> None:
    chat, _user = await _chat_for_admin(session, app, message.from_user.id)
    if chat is None:
        await message.answer("Сначала выберите группу через /chats.")
        return
    row = await Store(session).settings_for(chat.id)
    if row is None:
        await message.answer("Настройки ещё не созданы.")
        return
    await message.answer(
        "Антиспам:\n"
        f"сообщений в минуту: {row.messages_per_minute}\n"
        f"длина: {row.max_message_length}\n"
        f"типы: {row.allowed_types}\n"
        f"блокировка, сек: {row.spam_block_seconds}\n"
        f"очередь на человека: {row.max_queue_per_user}\n"
        "Изменить: /spam_set messages_per_minute 3"
    )


@router.message(Command("spam_set"))
async def spam_set(message: Message, session, app: AppContext) -> None:
    parts = (message.text or "").split(maxsplit=2)
    if len(parts) < 3:
        await message.answer("Формат: /spam_set messages_per_minute 3")
        return
    chat, _user = await _chat_for_admin(session, app, message.from_user.id)
    if chat is None:
        await message.answer("Сначала выберите группу через /chats.")
        return
    try:
        shown = await ChatService(session, app.settings).update_spam(chat, parts[1], parts[2], message.from_user.id)
    except DomainError as exc:
        await message.answer(explain_error(exc))
        return
    await message.answer(f"{parts[1]} = {shown}")


@router.message(Command("broadcasts"))
async def broadcasts_cmd(message: Message, session) -> None:
    rows = await Store(session).due_broadcasts()
    if not rows:
        await message.answer("Нет запланированных рассылок.")
        return
    lines = [f"• {str(row.id)[:8]} {row.audience}/{row.timing} {row.status}" for row in rows[:10]]
    await message.answer("Рассылки:\n" + "\n".join(lines))


@router.message(Command("broadcast_cancel"))
async def broadcast_cancel(message: Message, session, app: AppContext) -> None:
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Формат: /broadcast_cancel 8символов")
        return
    try:
        broadcast = await BroadcastService(
            session,
            settings=app.settings,
            telegram=app.telegram,
            queue=app.queue,
            bot_username=app.bot_username,
        ).cancel(parts[1].strip(), message.from_user.id)
    except DomainError as exc:
        await message.answer(explain_error(exc))
        return
    await message.answer(f"Рассылка {str(broadcast.id)[:8]} отменена.")


@router.message(Command("broadcast"))
async def broadcast_start(message: Message, state: FSMContext, app: AppContext) -> None:
    await state.clear()
    await state.set_state(BroadcastForm.audience)
    await message.answer("Кому отправить?", reply_markup=broadcast_audience(app.settings.secret()))


@router.message(StateFilter(BroadcastForm.content))
async def broadcast_content(message: Message, state: FSMContext, app: AppContext) -> None:
    if message.photo:
        content_type, file_id, filename, body = "photo", message.photo[-1].file_id, None, message.caption or ""
    elif message.text:
        content_type, file_id, filename, body = "text", None, None, message.text
    else:
        await message.answer("Нужен текст или одно фото.")
        return
    await state.update_data(content_type=content_type, file_id=file_id, filename=filename, body=body)
    data = await state.get_data()
    if data.get("timing") == "at":
        await state.set_state(BroadcastForm.schedule)
        await message.answer("Когда отправить? Формат: 2026-11-24 15:00")
        return
    if data.get("timing") == "repeat":
        await state.set_state(BroadcastForm.schedule)
        await message.answer("Правило: daily 15:00 или weekly mon 15:00")
        return
    await _preview(message, state, app)


@router.message(StateFilter(BroadcastForm.schedule))
async def broadcast_schedule(message: Message, state: FSMContext, app: AppContext) -> None:
    data = await state.get_data()
    raw = message.text or ""
    try:
        if data.get("timing") == "at":
            run_at = parse_run_at(raw, app.settings.timezone, now_utc())
            await state.update_data(run_at=run_at.isoformat(), recurrence=None, weekday=None, hour=None, minute=None)
        else:
            parsed = parse_recurrence(raw)
            await state.update_data(
                run_at=None,
                recurrence=parsed.recurrence,
                weekday=parsed.weekday,
                hour=parsed.hour,
                minute=parsed.minute,
            )
    except ScheduleError:
        await message.answer(explain_error(ScheduleError()))
        return
    await _preview(message, state, app)


async def _preview(message: Message, state: FSMContext, app: AppContext) -> None:
    data = await state.get_data()
    await state.set_state(BroadcastForm.confirm)
    await message.answer(
        "Предпросмотр. В группу или личные сообщения уйдёт текст ниже, без псевдонима участника.\n\n"
        + render_broadcast(data.get("body") or ""),
        reply_markup=confirm_broadcast(app.settings.secret()),
    )


async def handle_admin_action(query: CallbackQuery, payload: str, session, app: AppContext, state: FSMContext) -> None:
    if query.message is None or query.from_user is None:
        await query.answer()
        return
    if payload == "a:stats":
        chat, _user = await _chat_for_admin(session, app, query.from_user.id)
        title, data = await AnalyticsService(session, app.settings.timezone).period(None if chat is None else chat.id, "today")
        await query.message.answer(format_stats(title, chat.title if chat and chat.title else "Все группы", data))
        await query.answer()
        return
    if payload == "a:queue":
        await queue_cmd(query.message, session, app)
        await query.answer()
        return
    if payload == "a:reports":
        await complaints_cmd(query.message, session)
        await query.answer()
        return
    if payload == "a:errors":
        await errors_cmd(query.message, session)
        await query.answer()
        return
    if payload == "a:bcast":
        await state.set_state(BroadcastForm.audience)
        await query.message.answer("Кому отправить?", reply_markup=broadcast_audience(app.settings.secret()))
        await query.answer()
        return
    if payload.startswith("a:aud:"):
        audience = payload.split(":")[2]
        await state.update_data(audience=audience)
        await state.set_state(BroadcastForm.timing)
        await query.message.answer("Когда?", reply_markup=broadcast_timing(app.settings.secret()))
        await query.answer()
        return
    if payload.startswith("a:when:"):
        timing = payload.split(":")[2]
        await state.update_data(timing=timing)
        await state.set_state(BroadcastForm.content)
        await query.message.answer("Пришлите текст или одно фото с подписью.")
        await query.answer()
        return
    if payload == "a:stop":
        await state.clear()
        await query.message.answer("Рассылка отменена.")
        await query.answer()
        return
    if payload == "a:go":
        await _launch(query, session, app, state)
        return
    if payload.startswith("a:close:"):
        report = await session.get(Report, _uuid(payload.split(":", 2)[2]))
        if report is None:
            await query.answer("Жалоба не найдена", show_alert=True)
            return
        report.status = "closed"
        report.closed_at = now_utc()
        Store(session).add_audit(admin_telegram_id=query.from_user.id, action="report_close", detail=str(report.id)[:8])
        await query.answer("Жалоба закрыта")
        return
    if payload.startswith("a:ban:"):
        _prefix, _action, code, number_text = payload.split(":", 3)
        chat = await Store(session).chat_by_code(code)
        if chat is None:
            await query.answer("Группа не найдена", show_alert=True)
            return
        try:
            label = await ModerationService(session).block_alias(
                chat=chat,
                number=int(number_text),
                admin_id=query.from_user.id,
                admins=app.settings.admin_id_set,
                minutes=None,
                reason="report",
            )
        except (DomainError, ValueError) as exc:
            await query.answer(explain_error(exc) if isinstance(exc, DomainError) else "Ошибка", show_alert=True)
            return
        await query.answer(f"{label} заблокирован")
        return
    await query.answer()


async def _launch(query: CallbackQuery, session, app: AppContext, state: FSMContext) -> None:
    data = await state.get_data()
    if "body" not in data or "audience" not in data:
        await query.answer("Черновик истёк", show_alert=True)
        await state.clear()
        return
    chat = None
    if data["audience"] == "group":
        chat, _user = await _chat_for_admin(session, app, query.from_user.id)
        if chat is None:
            await query.answer("Нет выбранной группы", show_alert=True)
            return
    run_at = None
    if data.get("run_at"):
        from datetime import datetime

        run_at = datetime.fromisoformat(data["run_at"])
    try:
        broadcast = await BroadcastService(
            session,
            settings=app.settings,
            telegram=app.telegram,
            queue=app.queue,
            bot_username=app.bot_username,
        ).create(
            actor_id=query.from_user.id,
            audience=data["audience"],
            timing=data.get("timing") or "now",
            chat=chat,
            body=data["body"],
            content_type=data.get("content_type") or "text",
            file_id=data.get("file_id"),
            original_filename=data.get("filename"),
            run_at=run_at,
            recurrence=data.get("recurrence"),
            weekday=data.get("weekday"),
            hour=data.get("hour"),
            minute=data.get("minute"),
        )
    except DomainError as exc:
        await query.answer(explain_error(exc), show_alert=True)
        return
    await state.clear()
    await query.message.answer(f"Рассылка {str(broadcast.id)[:8]} сохранена со статусом {broadcast.status}.")
    await query.answer("Сохранено")


def _uuid(value: str):
    import uuid

    return uuid.UUID(value)


def complaint_buttons(secret: str, report_id, chat_code: str, alias_number: int):
    return (
        ("Закрыть", "callback", pack(f"a:close:{report_id}", secret)),
        ("Блок", "callback", pack(f"a:ban:{chat_code}:{alias_number}", secret)),
    )


def complaint_text(alias: str, chat_title: str, body: str) -> str:
    snippet = html.escape((body or "без текста")[:300])
    return f"Жалоба на {html.escape(alias)} в «{html.escape(chat_title)}».\n{snippet}"
