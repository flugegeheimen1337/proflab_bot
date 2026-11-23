from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message

from app.bot.texts import (
    EPHEMERAL_DISABLED,
    EPHEMERAL_EMPTY,
    EPHEMERAL_MEDIA,
    EPHEMERAL_VISIBLE,
    explain_error,
    queued,
)
from app.context import AppContext
from app.services.chats import ChatService
from app.services.errors import DomainError
from app.services.messages import MessageService
from app.services.policy import decide_ephemeral

logger = logging.getLogger(__name__)
router = Router(name="ephemeral")
_HINTS = {
    "disabled": EPHEMERAL_DISABLED,
    "visible": EPHEMERAL_VISIBLE,
    "media": EPHEMERAL_MEDIA,
    "empty": EPHEMERAL_EMPTY,
}


async def _hint(app: AppContext, user_id: int, text: str) -> None:
    try:
        await app.telegram.send_dm(user_id, text)
    except Exception:
        logger.info("ephemeral hint was not delivered")


@router.message(Command("anon"), F.chat.type.in_({"group", "supergroup"}))
async def anon(message: Message, command, session, app: AppContext, update_id: int | None = None) -> None:
    if message.from_user is None:
        return
    decision = decide_ephemeral(
        enabled=app.settings.ephemeral_mode_enabled,
        ephemeral_message_id=message.ephemeral_message_id,
        args=command.args,
        is_text=bool(message.text),
    )
    if decision != "ok":
        await _hint(app, message.from_user.id, _HINTS[decision])
        return
    # Bot API 10.2: an ephemeral command keeps message_id at 0 and uses ephemeral_message_id.
    source_id = message.ephemeral_message_id
    if not source_id:
        await _hint(app, message.from_user.id, EPHEMERAL_VISIBLE)
        return
    chat = await ChatService(session, app.settings).upsert(message.chat.id, message.chat.title)
    reply_to = message.reply_to_message.message_id if message.reply_to_message else None
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
            content_type="text",
            body=command.args,
            file_id=None,
            original_filename=None,
            file_size=None,
            source="ephemeral",
            source_chat_id=message.chat.id,
            source_message_id=source_id,
            reply_to_telegram_message_id=reply_to,
            update_id=update_id,
        )
    except DomainError as exc:
        await _hint(app, message.from_user.id, explain_error(exc))
        return
    try:
        await message.reply(queued(result.alias_label))
    except Exception:
        await _hint(app, message.from_user.id, queued(result.alias_label))
