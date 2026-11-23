from __future__ import annotations

from aiogram.types import BotCommand


def private_commands() -> list[BotCommand]:
    commands = [
        BotCommand(command="start", description="Запуск и регистрация"),
        BotCommand(command="help", description="Как писать анонимно"),
        BotCommand(command="chats", description="Выбрать группу"),
        BotCommand(command="alias", description="Показать свой псевдоним"),
        BotCommand(command="rules", description="Правила"),
        BotCommand(command="privacy", description="Конфиденциальность"),
        BotCommand(command="cancel", description="Отменить ответ или рассылку"),
        BotCommand(command="delete_my_data", description="Удаление данных"),
    ]
    if any(command.command == "anon" for command in commands):
        raise RuntimeError("/anon must not be offered in private command scope")
    return commands


def group_commands(ephemeral_enabled: bool) -> list[BotCommand]:
    if not ephemeral_enabled:
        return []
    return [BotCommand(command="anon", description="Написать в эту тему анонимно", is_ephemeral=True)]


def admin_group_commands(ephemeral_enabled: bool) -> list[BotCommand]:
    # Administrators resolve this scope before the all-groups list.
    # Without /anon here, an admin's client sends the command as a normal visible message.
    commands = list(group_commands(ephemeral_enabled))
    commands.append(BotCommand(command="link_group", description="Подключить эту группу"))
    return commands
