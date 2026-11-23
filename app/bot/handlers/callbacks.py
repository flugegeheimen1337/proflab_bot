from __future__ import annotations

import html

from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from app.bot.handlers.admin import complaint_buttons, complaint_text, handle_admin_action
from app.bot.texts import explain_error
from app.context import AppContext
from app.core.security import unsign
from app.db.models import Report
from app.db.repositories.store import Store
from app.services.aliases import AliasService
from app.services.errors import DomainError
from app.services.messages import MessageService
from app.services.users import UserService

router = Router(name="callbacks")


@router.callback_query()
async def on_callback(query: CallbackQuery, session, app: AppContext, state: FSMContext) -> None:
    if query.data is None or query.from_user is None:
        await query.answer()
        return
    if not await Store(session).remember_callback(query.id):
        await query.answer()
        return
    payload = unsign(query.data, app.settings.secret())
    if payload is None:
        await query.answer("Кнопка устарела", show_alert=True)
        return
    if payload == "p:me":
        await UserService(session).erase(query.from_user.id)
        await query.answer("Данные отключены")
        if query.message is not None:
            await query.message.answer("Отправка отключена. /start восстановит тот же псевдоним.")
        return
    if payload.startswith("g:"):
        await _select_chat(query, session, app, payload.split(":", 1)[1])
        return
    if payload.startswith("c:"):
        await _complain(query, session, app, payload.split(":", 1)[1])
        return
    if payload.startswith("d:"):
        await _delete_own(query, session, app, payload.split(":", 1)[1])
        return
    if payload.startswith("a:"):
        if query.from_user.id not in app.settings.admin_id_set:
            await query.answer("Недостаточно прав", show_alert=True)
            return
        await handle_admin_action(query, payload, session, app, state)
        return
    await query.answer()


async def _select_chat(query: CallbackQuery, session, app: AppContext, code: str) -> None:
    chat = await Store(session).chat_by_code(code)
    if chat is None or query.from_user is None:
        await query.answer("Группа не найдена", show_alert=True)
        return
    user = await UserService(session).require(query.from_user.id)
    is_admin = query.from_user.id in app.settings.admin_id_set
    member = await app.telegram.get_member(chat.telegram_chat_id, query.from_user.id)
    if not member.can_send_messages and not is_admin:
        await query.answer("Вы не можете писать в эту группу", show_alert=True)
        return
    await UserService(session).set_active_chat(user, chat)
    if member.can_send_messages:
        alias = await AliasService(session).ensure(user, chat)
        text = f"Теперь вы пишете в «{html.escape(chat.title or 'группа')}» как {html.escape(alias.label)}."
    else:
        text = f"Для управления выбрана «{html.escape(chat.title or 'группа')}»."
    await query.answer("Группа выбрана")
    if query.message is not None:
        await query.message.answer(text)


async def _complain(query: CallbackQuery, session, app: AppContext, code: str) -> None:
    store = Store(session)
    message = await store.message_by_code(code)
    if message is None or query.from_user is None:
        await query.answer("Сообщение не найдено", show_alert=True)
        return
    reporter = await UserService(session).require(query.from_user.id)
    if await store.report_for(message.id, reporter.id):
        await query.answer("Вы уже отправили жалобу", show_alert=True)
        return
    report = Report(message_id=message.id, reporter_id=reporter.id, status="open")
    session.add(report)
    await session.flush()
    alias = await store.alias_for_user(message.chat_id, message.author_id)
    chat = await store.chat_by_id(message.chat_id)
    await query.answer("Жалоба отправлена модераторам")
    if alias is None or chat is None:
        return
    text = complaint_text(alias.label, chat.title or "группа", message.body or "")
    buttons = complaint_buttons(app.settings.secret(), report.id, chat.public_code, alias.number)
    for admin_id in app.settings.admin_id_set:
        try:
            await app.telegram.send_dm(admin_id, text, buttons)
        except Exception:
            continue


async def _delete_own(query: CallbackQuery, session, app: AppContext, code: str) -> None:
    if query.from_user is None:
        await query.answer()
        return
    service = MessageService(
        session,
        settings=app.settings,
        telegram=app.telegram,
        queue=app.queue,
        redis=app.redis,
        bot_id=app.bot_id,
    )
    try:
        result = await service.delete_own(query.from_user.id, code)
    except DomainError as exc:
        await query.answer(explain_error(exc), show_alert=True)
        return
    if result == "deleted":
        await query.answer("Удалено")
    elif result == "forbidden":
        await query.answer("Можно удалить только своё сообщение", show_alert=True)
    else:
        await query.answer("Сообщение не найдено", show_alert=True)
