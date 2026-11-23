from __future__ import annotations

import io
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramNetworkError, TelegramRetryAfter
from aiogram.types import BufferedInputFile, InlineKeyboardButton, InlineKeyboardMarkup, LinkPreviewOptions, ReplyParameters

from app.integrations.telegram.gateway import MemberInfo, Outgoing
from app.services.buttons import Button
from app.services.errors import BotBlocked, PermanentTelegramError, TemporaryTelegramError
from app.services.limiter import RedisLike

logger = logging.getLogger(__name__)
_PREVIEW_OFF = LinkPreviewOptions(is_disabled=True)


def member_from_aiogram(member: object) -> MemberInfo:
    status = getattr(member, "status")
    status_value = status.value if hasattr(status, "value") else str(status)
    is_admin = status_value in {"creator", "administrator"}
    if status_value in {"left", "kicked"}:
        return MemberInfo(status_value, False, False, False, False, False)
    if status_value == "restricted":
        return MemberInfo(
            status_value,
            bool(getattr(member, "can_send_messages", False)),
            bool(getattr(member, "can_send_photos", False)),
            bool(getattr(member, "can_send_videos", False)),
            bool(getattr(member, "can_send_documents", False)),
            False,
        )
    if status_value in {"member", "creator", "administrator"}:
        return MemberInfo(status_value, True, True, True, True, is_admin)
    return MemberInfo(status_value, False, False, False, False, False)


def markup(buttons: tuple[Button, ...] | list[Button]) -> InlineKeyboardMarkup | None:
    if not buttons:
        return None
    row = []
    for title, kind, value in buttons:
        if kind == "url":
            row.append(InlineKeyboardButton(text=title, url=value))
        else:
            row.append(InlineKeyboardButton(text=title, callback_data=value))
    return InlineKeyboardMarkup(inline_keyboard=[row])


class AiogramGateway:
    """Sends fresh messages as the bot. Never forwards and never copies user entities."""

    def __init__(self, bot: Bot, redis: RedisLike) -> None:
        self.bot = bot
        self.redis = redis

    async def get_member(self, chat_id: int, user_id: int) -> MemberInfo:
        key = f"member:{chat_id}:{user_id}"
        try:
            cached = await self.redis.get(key)
        except Exception:
            cached = None
        if cached:
            return MemberInfo.from_json(cached)
        try:
            member = await self.bot.get_chat_member(chat_id, user_id)
            info = member_from_aiogram(member)
        except TelegramBadRequest:
            info = MemberInfo.absent()
        try:
            await self.redis.set(key, info.to_json(), ex=45)
        except Exception:
            logger.warning("member cache skipped")
        return info

    async def send(self, outgoing: Outgoing) -> int:
        reply = None
        if outgoing.reply_to_message_id:
            reply = ReplyParameters(
                message_id=outgoing.reply_to_message_id,
                allow_sending_without_reply=True,
            )
        keyboard = markup(outgoing.buttons)
        try:
            if outgoing.kind == "text":
                sent = await self.bot.send_message(
                    outgoing.chat_id,
                    outgoing.text,
                    reply_markup=keyboard,
                    reply_parameters=reply,
                    link_preview_options=_PREVIEW_OFF,
                )
            elif outgoing.kind == "photo":
                sent = await self.bot.send_photo(
                    outgoing.chat_id,
                    photo=outgoing.file_id,
                    caption=outgoing.text,
                    reply_markup=keyboard,
                    reply_parameters=reply,
                )
            elif outgoing.kind in {"video", "document"}:
                payload = await self._neutral_file(outgoing)
                if outgoing.kind == "video":
                    sent = await self.bot.send_video(
                        outgoing.chat_id,
                        video=payload,
                        caption=outgoing.text,
                        reply_markup=keyboard,
                        reply_parameters=reply,
                    )
                else:
                    sent = await self.bot.send_document(
                        outgoing.chat_id,
                        document=payload,
                        caption=outgoing.text,
                        reply_markup=keyboard,
                        reply_parameters=reply,
                    )
            else:
                raise PermanentTelegramError(f"unsupported kind {outgoing.kind}")
        except TelegramRetryAfter as exc:
            raise TemporaryTelegramError(float(exc.retry_after)) from exc
        except TelegramNetworkError as exc:
            raise TemporaryTelegramError(2) from exc
        except TelegramForbiddenError as exc:
            raise BotBlocked() from exc
        except TelegramBadRequest as exc:
            raise PermanentTelegramError(str(exc)) from exc
        return sent.message_id

    async def send_dm(self, user_id: int, text: str, buttons: tuple[Button, ...] = ()) -> int:
        try:
            sent = await self.bot.send_message(
                user_id,
                text,
                reply_markup=markup(buttons),
                link_preview_options=_PREVIEW_OFF,
            )
        except TelegramRetryAfter as exc:
            raise TemporaryTelegramError(float(exc.retry_after)) from exc
        except TelegramNetworkError as exc:
            raise TemporaryTelegramError(2) from exc
        except TelegramForbiddenError as exc:
            raise BotBlocked() from exc
        except TelegramBadRequest as exc:
            raise PermanentTelegramError(str(exc)) from exc
        return sent.message_id

    async def delete_message(self, chat_id: int, message_id: int) -> None:
        try:
            await self.bot.delete_message(chat_id, message_id)
        except TelegramBadRequest as exc:
            if "message to delete not found" in str(exc).lower() or "message can't be deleted" in str(exc).lower():
                return
            raise PermanentTelegramError(str(exc)) from exc

    async def _neutral_file(self, outgoing: Outgoing) -> BufferedInputFile:
        if not outgoing.file_id:
            raise PermanentTelegramError("missing file")
        tg_file = await self.bot.get_file(outgoing.file_id)
        if not tg_file.file_path:
            raise PermanentTelegramError("missing file path")
        buffer = io.BytesIO()
        await self.bot.download_file(tg_file.file_path, buffer)
        buffer.seek(0)
        return BufferedInputFile(buffer.read(), filename=outgoing.filename or "file.bin")
