"""Experimental group intercept.

This mode deletes a visible group message and publishes it again under an alias.
It does NOT provide anonymity: people can see the author before deletion and can
link that person to the alias. Keep INTERCEPT_MODE_ENABLED=false for real use.
"""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.types import Message

from app.bot.texts import explain_error, queued
from app.context import AppContext
from app.services.chats import ChatService
from app.services.errors import DomainError
from app.services.messages import MessageService

logger = logging.getLogger(__name__)
router = Router(name="intercept")


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


@router.message(F.chat.type.in_({"group", "supergroup"}))
async def intercept(message: Message, session, app: AppContext, update_id: int | None = None) -> None:
    if not app.settings.intercept_mode_enabled:
        return
    if message.from_user is None or message.from_user.is_bot:
        return
    if (message.text or "").startswith("/"):
        return
    content = _content(message)
    if content is None:
        return
    logger.warning("intercept received a visible group message; anonymity is not provided")
    try:
        await message.delete()
    except Exception:
        try:
            await app.telegram.send_dm(
                message.from_user.id,
                "Сообщение уже могло быть видно в группе. Удалить его не удалось, поэтому бот не публикует копию.",
            )
        except Exception:
            logger.info("intercept warning was not delivered")
        return
    chat = await ChatService(session, app.settings).upsert(message.chat.id, message.chat.title)
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
            source="intercept",
            source_chat_id=message.chat.id,
            source_message_id=message.message_id,
            update_id=update_id,
            **content,
        )
    except DomainError as exc:
        try:
            await app.telegram.send_dm(message.from_user.id, explain_error(exc))
        except Exception:
            logger.info("intercept rejection was not delivered")
        return
    if result.created:
        try:
            await app.telegram.send_dm(message.from_user.id, queued(result.alias_label))
        except Exception:
            logger.info("intercept confirmation was not delivered")
