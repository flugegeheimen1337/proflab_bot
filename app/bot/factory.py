from __future__ import annotations

import logging

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.types import BotCommandScopeAllChatAdministrators, BotCommandScopeAllGroupChats, BotCommandScopeAllPrivateChats, ErrorEvent

from app.bot.commands import admin_group_commands, group_commands, private_commands
from app.bot.handlers.admin import router as admin_router
from app.bot.handlers.callbacks import router as callback_router
from app.bot.handlers.ephemeral import router as ephemeral_router
from app.bot.handlers.group import router as group_router
from app.bot.handlers.intercept import router as intercept_router
from app.bot.handlers.private import router as private_router
from app.bot.middlewares import DbSessionMiddleware
from app.bot.texts import INTERCEPT_WARNING
from app.context import AppContext
from app.core.config import Settings
from app.core.security import redact

logger = logging.getLogger(__name__)


def build_routers(settings: Settings):
    routers = [admin_router, private_router, group_router, callback_router, ephemeral_router]
    if settings.intercept_mode_enabled:
        routers.append(intercept_router)
    return routers


def create_dispatcher(app: AppContext) -> Dispatcher:
    storage = RedisStorage(redis=app.redis, state_ttl=86_400, data_ttl=86_400)
    dispatcher = Dispatcher(storage=storage)
    dispatcher.workflow_data["app"] = app
    dispatcher.update.middleware(DbSessionMiddleware(app.session_factory))
    for item in build_routers(app.settings):
        dispatcher.include_router(item)

    @dispatcher.errors()
    async def on_error(event: ErrorEvent) -> bool:
        logger.error(
            "handler failed error=%s detail=%s",
            type(event.exception).__name__,
            redact(str(event.exception))[:300],
        )
        return True

    return dispatcher


async def sync_commands(bot: Bot, settings: Settings) -> None:
    private = private_commands()
    await bot.set_my_commands(private, scope=BotCommandScopeAllPrivateChats())
    await bot.set_my_commands(admin_group_commands(), scope=BotCommandScopeAllChatAdministrators())
    group = group_commands(settings.ephemeral_mode_enabled)
    if group:
        await bot.set_my_commands(group, scope=BotCommandScopeAllGroupChats())
    else:
        await bot.delete_my_commands(scope=BotCommandScopeAllGroupChats())


async def warn_if_intercept(bot: Bot, settings: Settings) -> None:
    if not settings.intercept_mode_enabled:
        return
    logger.warning("intercept mode is enabled and does not provide anonymity")
    for admin_id in settings.admin_id_set:
        try:
            await bot.send_message(admin_id, INTERCEPT_WARNING)
        except Exception:
            logger.warning("intercept warning was not delivered")
