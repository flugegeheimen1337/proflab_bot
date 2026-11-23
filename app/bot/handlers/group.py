from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import ChatMemberUpdated, Message

from app.context import AppContext
from app.services.chats import ChatService

router = Router(name="group")


def _status(member) -> str:
    status = member.status
    return status.value if hasattr(status, "value") else str(status)


@router.message(Command("link_group"), F.chat.type.in_({"group", "supergroup"}))
async def link_group(message: Message, session, app: AppContext) -> None:
    if message.from_user is None:
        return
    member = await app.telegram.get_member(message.chat.id, message.from_user.id)
    if not member.is_admin and message.from_user.id not in app.settings.admin_id_set:
        await message.answer("Подключить группу может её администратор.")
        return
    await ChatService(session, app.settings).upsert(message.chat.id, message.chat.title)
    await message.answer(
        "Группа подключена. Пишите боту в личные сообщения: в группе появится псевдоним, а не ваше имя."
    )


@router.my_chat_member()
async def bot_membership(event: ChatMemberUpdated, session, app: AppContext) -> None:
    value = _status(event.new_chat_member)
    service = ChatService(session, app.settings)
    if value in {"member", "administrator", "creator"}:
        await service.upsert(event.chat.id, event.chat.title)
    elif value in {"left", "kicked"}:
        await service.deactivate(event.chat.id)


@router.message(F.migrate_to_chat_id)
async def migrated(message: Message, session, app: AppContext) -> None:
    if message.migrate_to_chat_id is None:
        return
    await ChatService(session, app.settings).migrate(message.chat.id, message.migrate_to_chat_id)
