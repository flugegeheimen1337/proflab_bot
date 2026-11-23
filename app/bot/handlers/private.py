from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from app.bot.keyboards import chat_select, confirm_erase
from app.bot.texts import HELP, RULES, WELCOME, explain_error, privacy_text, queued
from app.context import AppContext
from app.db.repositories.store import Store
from app.services.aliases import AliasService
from app.services.chats import ChatService
from app.services.errors import DomainError
from app.services.messages import MessageService
from app.services.users import UserService

router = Router(name="private")


def _content(message: Message) -> dict | None:
    if message.photo:
        return {
            "content_type": "photo",
            "body": message.caption,
            "file_id": message.photo[-1].file_id,
            "original_filename": None,
            "file_size": message.photo[-1].file_size,
        }
    if message.video:
        return {
            "content_type": "video",
            "body": message.caption,
            "file_id": message.video.file_id,
            "original_filename": message.video.file_name,
            "file_size": message.video.file_size,
        }
    if message.document:
        return {
            "content_type": "document",
            "body": message.caption,
            "file_id": message.document.file_id,
            "original_filename": message.document.file_name,
            "file_size": message.document.file_size,
        }
    if message.text:
        return {
            "content_type": "text",
            "body": message.text,
            "file_id": None,
            "original_filename": None,
            "file_size": None,
        }
    return None


async def _eligible(message: Message, session, app: AppContext, telegram_id: int):
    chats = await Store(session).active_chats()
    allowed = []
    is_admin = telegram_id in app.settings.admin_id_set
    for chat in chats:
        member = await app.telegram.get_member(chat.telegram_chat_id, telegram_id)
        if member.can_send_messages or is_admin:
            allowed.append(chat)
    return allowed


async def _room_reply_code(session, user, message: Message) -> str | None:
    if message.reply_to_message is None:
        return user.pending_reply_code
    store = Store(session)
    copy = await store.copy_by_telegram(user.id, message.reply_to_message.message_id)
    parent = await store.message_by_id(copy.message_id) if copy is not None else None
    if parent is None and user.telegram_id is not None:
        parent = await store.message_by_source(message.chat.id, user.telegram_id, message.reply_to_message.message_id)
    if parent is None:
        return user.pending_reply_code
    return parent.public_code


async def bind_chats(message: Message, session, app: AppContext, user) -> None:
    if user.telegram_id is None:
        return
    chats = await _eligible(message, session, app, user.telegram_id)
    posting = []
    for chat in chats:
        member = await app.telegram.get_member(chat.telegram_chat_id, user.telegram_id)
        if member.can_send_messages:
            posting.append(chat)
    if len(posting) == 1:
        await UserService(session).set_active_chat(user, posting[0])
        alias = await AliasService(session).ensure(user, posting[0])
        title = posting[0].title or "группа"
        await message.answer(f"Вы пишете в «{title}» как {alias.label}.")
        return
    if not chats:
        await message.answer("Бот пока не видит общую группу, где вы можете писать. Попросите администратора подключить её командой /link_group.")
        return
    await message.answer("Выберите группу.", reply_markup=chat_select(chats, app.settings.secret()))


@router.message(CommandStart(), F.chat.type == "private")
async def start(message: Message, command, session, app: AppContext, state: FSMContext) -> None:
    await state.clear()
    if message.from_user is None:
        return
    user, created, restored = await UserService(session).register(message.from_user.id)
    payload = (command.args or "").strip()
    if payload.startswith("r_"):
        await UserService(session).set_pending_reply(user, payload[2:])
        await message.answer("Следующее сообщение будет ответом в чате. /cancel отменяет ответ.")
    if created:
        await message.answer(WELCOME)
    elif restored:
        await message.answer("Запись восстановлена с прежним псевдонимом.\n\n" + WELCOME)
    elif not payload.startswith("r_"):
        await message.answer("Напишите сообщение сюда. Его копия уйдёт остальным участникам.")
    room = await ChatService(session, app.settings).ensure_room()
    await UserService(session).set_active_chat(user, room)
    alias = await AliasService(session).ensure(user, room)
    await message.answer(f"Вы {alias.label}. Пишите следующим сообщением.")


@router.message(Command("help"), F.chat.type == "private")
async def help_cmd(message: Message) -> None:
    await message.answer(HELP)


@router.message(Command("rules"), F.chat.type == "private")
async def rules_cmd(message: Message) -> None:
    await message.answer(RULES)


@router.message(Command("privacy"), F.chat.type == "private")
async def privacy_cmd(message: Message) -> None:
    await message.answer(privacy_text())


@router.message(Command("cancel"), F.chat.type == "private")
async def cancel_cmd(message: Message, session, state: FSMContext) -> None:
    await state.clear()
    if message.from_user is not None:
        user = await Store(session).user_by_telegram(message.from_user.id)
        if user is not None:
            await UserService(session).set_pending_reply(user, None)
    await message.answer("Отменено.")


@router.message(Command("chats"), F.chat.type == "private")
async def chats_cmd(message: Message, session, app: AppContext) -> None:
    if message.from_user is None:
        return
    user = await UserService(session).require(message.from_user.id)
    room = await ChatService(session, app.settings).ensure_room()
    alias = await AliasService(session).ensure(user, room)
    await message.answer(f"Переписка идёт в этом чате. Вы {alias.label}.")


@router.message(Command("alias"), F.chat.type == "private")
async def alias_cmd(message: Message, session, app: AppContext) -> None:
    if message.from_user is None:
        return
    user = await UserService(session).require(message.from_user.id)
    room = await ChatService(session, app.settings).ensure_room()
    alias = await AliasService(session).ensure(user, room)
    await message.answer(f"Вы {alias.label}. Сменить псевдоним нельзя.")


@router.message(Command("delete_my_data"), F.chat.type == "private")
async def delete_data(message: Message, app: AppContext) -> None:
    await message.answer(
        "Кнопка ниже отключит отправку и отвяжет вас от активной группы. "
        "Telegram ID останется в закрытой базе, чтобы сохранялись блокировки, и не публикуется в группе. "
        "Полное отвязывание ID делает администратор командой /purge. "
        "Повторный /start восстановит тот же псевдоним.",
        reply_markup=confirm_erase(app.settings.secret()),
    )


@router.message(StateFilter(None), F.chat.type == "private", F.func(lambda item: not (item.text or "").startswith("/")))
async def incoming(message: Message, session, app: AppContext, update_id: int | None = None) -> None:
    if message.from_user is None:
        return
    if message.media_group_id:
        await message.answer("Альбомы пока не поддерживаются. Отправьте один файл.")
        return
    content = _content(message)
    if content is None:
        await message.answer("Можно отправить текст, фото, видео или документ.")
        return
    user = await UserService(session).require(message.from_user.id)
    if user.erased_at is not None:
        await message.answer(explain_error_code("erased"))
        return
    chat = await ChatService(session, app.settings).ensure_room()
    service = MessageService(
        session,
        settings=app.settings,
        telegram=app.telegram,
        queue=app.queue,
        redis=app.redis,
        bot_id=app.bot_id,
    )
    try:
        result = await service.accept(
            telegram_user_id=message.from_user.id,
            chat=chat,
            source="private",
            source_chat_id=message.chat.id,
            source_message_id=message.message_id,
            reply_code=await _room_reply_code(session, user, message),
            update_id=update_id,
            **content,
        )
    except DomainError as exc:
        await message.answer(explain_error(exc))
        return
    if not result.created:
        await message.answer("Это сообщение уже принято.")
        return
    await message.answer(queued(result.alias_label))


def explain_error_code(code: str) -> str:
    from app.bot.texts import explain

    return explain(code)
