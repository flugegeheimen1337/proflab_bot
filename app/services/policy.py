from __future__ import annotations


def decide_ephemeral(
    *,
    enabled: bool,
    ephemeral_message_id: int | None,
    args: str | None,
    is_text: bool,
) -> str:
    """Decide whether an /anon update may be published.

    Bot API 10.2 marks a hidden command with ephemeral_message_id.
    A normal command has no such id, and its message_id is visible to the group.
    Publishing that text again would copy an already exposed message.
    """
    if not enabled:
        return "disabled"
    if not is_text:
        return "media"
    if ephemeral_message_id is None:
        return "visible"
    if not (args or "").strip():
        return "empty"
    return "ok"
