from __future__ import annotations

from app.core.security import pack

Button = tuple[str, str, str]


def publication_buttons(code: str, username: str, secret: str) -> list[Button]:
    buttons: list[Button] = []
    if username:
        buttons.append(("Ответить", "url", f"https://t.me/{username}?start=r_{code}"))
    buttons.append(("Пожаловаться", "callback", pack(f"c:{code}", secret)))
    return buttons


def delete_buttons(code: str, secret: str) -> list[Button]:
    return [("Удалить", "callback", pack(f"d:{code}", secret))]
