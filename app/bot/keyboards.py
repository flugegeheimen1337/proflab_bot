from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.core.security import pack
from app.db.models import Chat
from app.services.buttons import Button


def to_markup(buttons: list[Button] | tuple[Button, ...]) -> InlineKeyboardMarkup | None:
    if not buttons:
        return None
    row = []
    for title, kind, value in buttons:
        if kind == "url":
            row.append(InlineKeyboardButton(text=title, url=value))
        else:
            row.append(InlineKeyboardButton(text=title, callback_data=value))
    return InlineKeyboardMarkup(inline_keyboard=[row])


def chat_select(chats: list[Chat], secret: str) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=chat.title or "Группа", callback_data=pack(f"g:{chat.public_code}", secret))]
        for chat in chats
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_menu(secret: str) -> InlineKeyboardMarkup:
    def btn(text: str, payload: str) -> InlineKeyboardButton:
        return InlineKeyboardButton(text=text, callback_data=pack(payload, secret))

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [btn("Статистика", "a:stats"), btn("Очередь", "a:queue")],
            [btn("Жалобы", "a:reports"), btn("Ошибки", "a:errors")],
            [btn("Рассылка", "a:bcast")],
        ]
    )


def broadcast_audience(secret: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(text="В группу", callback_data=pack("a:aud:group", secret)),
            InlineKeyboardButton(text="В личные сообщения", callback_data=pack("a:aud:direct", secret)),
        ]]
    )


def broadcast_timing(secret: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(text="Сейчас", callback_data=pack("a:when:now", secret)),
            InlineKeyboardButton(text="Ко времени", callback_data=pack("a:when:at", secret)),
            InlineKeyboardButton(text="Повторять", callback_data=pack("a:when:repeat", secret)),
        ]]
    )


def confirm_broadcast(secret: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(text="Запустить", callback_data=pack("a:go", secret)),
            InlineKeyboardButton(text="Отмена", callback_data=pack("a:stop", secret)),
        ]]
    )


def confirm_erase(secret: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Удалить мои данные", callback_data=pack("p:me", secret))]]
    )
