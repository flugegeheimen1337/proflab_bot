from __future__ import annotations

import hashlib
import hmac
import re
import secrets

_TOKEN_RE = re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{20,}\b")
_CALLBACK_LIMIT = 64


def sign(payload: str, secret: str) -> str:
    mac = hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()[:12]
    return f"{payload}.{mac}"


def unsign(token: str, secret: str) -> str | None:
    payload, separator, mac = token.rpartition(".")
    if not separator or not payload or not mac:
        return None
    expected = hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()[:12]
    if not hmac.compare_digest(expected, mac):
        return None
    return payload


def pack(payload: str, secret: str) -> str:
    token = sign(payload, secret)
    if len(token.encode("utf-8")) > _CALLBACK_LIMIT:
        raise ValueError("callback_data exceeds 64 bytes")
    return token


def new_public_code() -> str:
    return secrets.token_urlsafe(8)


def redact(text: str) -> str:
    return _TOKEN_RE.sub("[redacted-token]", text)
