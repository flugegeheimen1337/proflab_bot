from __future__ import annotations

import html
import re

ALLOWED_EXTENSIONS = {
    "pdf",
    "zip",
    "txt",
    "doc",
    "docx",
    "xls",
    "xlsx",
    "png",
    "jpg",
    "jpeg",
    "mp4",
    "webp",
    "gif",
    "csv",
    "ppt",
    "pptx",
    "odt",
    "ods",
    "mp3",
    "ogg",
}


def clean_body(text: str | None) -> str | None:
    if text is None:
        return None
    cleaned = text.replace("\x00", "").strip()
    return cleaned or None


def render_public(alias: str, body: str | None) -> str:
    safe_alias = html.escape(alias, quote=False)
    if body:
        return f"<b>{safe_alias}</b>\n{html.escape(body, quote=False)}"
    return f"<b>{safe_alias}</b>"


def render_broadcast(body: str) -> str:
    return f"<b>Сообщение</b>\n{html.escape(body, quote=False)}"


def ensure_length(content_type: str, body: str | None, limit: int) -> None:
    from app.services.errors import ContentRejected

    if not body:
        return
    hard = 900 if content_type != "text" else min(limit, 4000)
    effective = min(limit, hard)
    if len(body) > effective:
        raise ContentRejected(f"length:{effective}")


def neutral_filename(original: str | None, *, kind: str) -> str:
    if kind == "video":
        return "file.mp4"
    if kind == "photo":
        return "file.jpg"
    if not original or "." not in original:
        return "file.bin"
    ext = original.rsplit(".", 1)[-1].lower()
    if not re.fullmatch(r"[a-z0-9]{1,8}", ext) or ext not in ALLOWED_EXTENSIONS:
        return "file.bin"
    return f"file.{ext}"


def safe_error(exc: Exception) -> str:
    from app.core.security import redact

    text = f"{type(exc).__name__}: {exc}"
    return redact(text)[:300]
