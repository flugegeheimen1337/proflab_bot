from __future__ import annotations

import inspect
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import inspect as sa_inspect

from app.bot.commands import group_commands, private_commands
from app.bot.factory import build_routers
from app.bot.texts import privacy_text
from app.core.config import get_settings
from app.core.logging import JsonFormatter
from app.core.security import pack, redact, sign, unsign
from app.core.time import now_utc
from app.db.base import Base
from app.db.models import User
from app.integrations.telegram.aiogram_gateway import AiogramGateway
from app.services.aliases import AliasService
from app.services.analytics import AnalyticsService
from app.services.broadcasts import BroadcastService
from app.services.buttons import publication_buttons
from app.services.chats import ChatService
from app.services.delivery import DeliveryService
from app.services.errors import NotAMember, PermissionDenied, QueueFull, RateLimited, TemporaryTelegramError, UserBanned
from app.services.formatting import neutral_filename, render_public
from app.services.limiter import GroupSendLimiter
from app.services.messages import MessageService
from app.services.moderation import ModerationService
from app.services.policy import decide_ephemeral
from app.services.schedule import due_slot, parse_recurrence, parse_run_at
from app.services.users import UserService
from tests.fakes import FakeRedis, FakeTelegram, InlineQueue

ROOT = Path(__file__).resolve().parents[1]
TOKEN_RE = re.compile(r"\d{8,12}:[A-Za-z0-9_-]{30,}")
CHAT = -100123
BOT = 999
USER = 4242


def _stack(session, factory, fake, redis, queue):
    settings = get_settings()
    return MessageService(
        session,
        settings=settings,
        telegram=fake,
        queue=queue,
        redis=redis,
        bot_id=BOT,
    ), settings, factory


async def _ready(session, fake: FakeTelegram):
    settings = get_settings()
    chat = await ChatService(session, settings).upsert(CHAT, "Лаборатория")
    fake.allow(CHAT, USER)
    fake.allow(CHAT, BOT)
    return chat


async def test_registration_alias_is_stable_and_not_chosen(database):
    session, _factory = database
    settings = get_settings()
    chat = await ChatService(session, settings).upsert(CHAT, "Лаборатория")
    other = await ChatService(session, settings).upsert(-100999, "Вторая")
    user, created, _restored = await UserService(session).register(USER)
    assert created is True
    again, created_again, _ = await UserService(session).register(USER)
    assert created_again is False
    assert again.id == user.id
    aliases = AliasService(session, rng=lambda _low, _high: 104)
    first = await aliases.ensure(user, chat)
    second = await aliases.ensure(user, chat)
    assert first.id == second.id
    assert first.label == "Аноним #104"
    other_alias = await aliases.ensure(user, other)
    assert other_alias.number == 104
    assert other_alias.id != first.id
    assert "number" not in inspect.signature(AliasService.ensure).parameters
    assert "username" not in User.__table__.columns
    assert "first_name" not in User.__table__.columns


async def test_alias_collision_gets_another_number(database):
    session, _factory = database
    chat = await ChatService(session, get_settings()).upsert(CHAT, "Лаборатория")
    first_user, _, _ = await UserService(session).register(1)
    second_user, _, _ = await UserService(session).register(2)
    numbers = iter([104, 104, 105])
    aliases = AliasService(session, rng=lambda _low, _high: next(numbers))
    first = await aliases.ensure(first_user, chat)
    second = await aliases.ensure(second_user, chat)
    assert first.number == 104
    assert second.number == 105


async def test_message_idempotency_reply_and_no_telegram_id(database):
    session, factory = database
    fake = FakeTelegram()
    redis = FakeRedis()
    queue = InlineQueue()
    chat = await _ready(session, fake)
    service, settings, _factory = _stack(session, factory, fake, redis, queue)
    first = await service.accept(
        telegram_user_id=USER,
        chat=chat,
        content_type="text",
        body="привет <b>мир</b>",
        file_id=None,
        original_filename=None,
        file_size=None,
        source="private",
        source_chat_id=USER,
        source_message_id=10,
        update_id=77,
    )
    duplicate = await service.accept(
        telegram_user_id=USER,
        chat=chat,
        content_type="text",
        body="привет",
        file_id=None,
        original_filename=None,
        file_size=None,
        source="private",
        source_chat_id=USER,
        source_message_id=10,
        update_id=77,
    )
    assert first.created is True
    assert duplicate.created is False
    assert queue.jobs == [first.message.id]
    assert str(USER) not in first.alias_label
    delivery = DeliveryService(
        factory,
        settings=settings,
        telegram=fake,
        redis=redis,
        limiter=GroupSendLimiter(redis, 0, 20),
        bot_username="anon_test_bot",
    )
    await delivery.deliver(first.message.id)
    await delivery.deliver(first.message.id)
    assert len(fake.sent) == 1
    assert str(USER) not in fake.sent[0].text
    assert "<b>" not in fake.sent[0].text.split("\n", 1)[-1]
    assert "tg://user" not in fake.sent[0].text
    reply = await service.accept(
        telegram_user_id=USER,
        chat=chat,
        content_type="text",
        body="ответ",
        file_id=None,
        original_filename=None,
        file_size=None,
        source="private",
        source_chat_id=USER,
        source_message_id=11,
        reply_code=first.message.public_code,
    )
    await delivery.deliver(reply.message.id)
    assert reply.message.reply_to_id == first.message.id
    assert fake.sent[-1].reply_to_message_id == 1
    rendered = render_public("Аноним #1", '<a href="tg://user?id=1">я</a>')
    assert "<a href=" not in rendered
    buttons = publication_buttons(first.message.public_code, "anon_test_bot", settings.secret())
    assert str(USER) not in buttons[0][2]


async def test_membership_ban_rate_and_queue(database):
    session, factory = database
    fake = FakeTelegram()
    redis = FakeRedis()
    queue = InlineQueue()
    chat = await _ready(session, fake)
    service, settings, _factory = _stack(session, factory, fake, redis, queue)
    fake.members.pop((CHAT, USER))
    with pytest.raises(NotAMember):
        await service.accept(
            telegram_user_id=USER,
            chat=chat,
            content_type="text",
            body="нет",
            file_id=None,
            original_filename=None,
            file_size=None,
            source="private",
            source_chat_id=USER,
            source_message_id=1,
        )
    fake.allow(CHAT, USER)
    await ModerationService(session).block_alias(
        chat=chat,
        number=(await AliasService(session).ensure(await UserService(session).require(USER), chat)).number,
        admin_id=111,
        admins=settings.admin_id_set,
        minutes=None,
        reason="spam",
    )
    with pytest.raises(UserBanned):
        await service.accept(
            telegram_user_id=USER,
            chat=chat,
            content_type="text",
            body="бан",
            file_id=None,
            original_filename=None,
            file_size=None,
            source="private",
            source_chat_id=USER,
            source_message_id=2,
        )
    await ModerationService(session).unblock_alias(
        chat=chat,
        number=(await AliasService(session).ensure(await UserService(session).require(USER), chat)).number,
        admin_id=111,
        admins=settings.admin_id_set,
    )
    row = await service.store.settings_for(chat.id)
    row.messages_per_minute = 1
    row.max_queue_per_user = 1
    await service.accept(
        telegram_user_id=USER,
        chat=chat,
        content_type="text",
        body="раз",
        file_id=None,
        original_filename=None,
        file_size=None,
        source="private",
        source_chat_id=USER,
        source_message_id=3,
    )
    with pytest.raises(RateLimited):
        await service.accept(
            telegram_user_id=USER,
            chat=chat,
            content_type="text",
            body="два",
            file_id=None,
            original_filename=None,
            file_size=None,
            source="private",
            source_chat_id=USER,
            source_message_id=4,
        )


async def test_queue_full(database):
    session, factory = database
    fake = FakeTelegram()
    redis = FakeRedis()
    queue = InlineQueue()
    chat = await _ready(session, fake)
    service, _settings, _factory = _stack(session, factory, fake, redis, queue)
    row = await service.store.settings_for(chat.id)
    row.messages_per_minute = 10
    row.max_queue_per_user = 1
    await service.accept(
        telegram_user_id=USER,
        chat=chat,
        content_type="text",
        body="раз",
        file_id=None,
        original_filename=None,
        file_size=None,
        source="private",
        source_chat_id=USER,
        source_message_id=30,
    )
    with pytest.raises(QueueFull):
        await service.accept(
            telegram_user_id=USER,
            chat=chat,
            content_type="text",
            body="два",
            file_id=None,
            original_filename=None,
            file_size=None,
            source="private",
            source_chat_id=USER,
            source_message_id=31,
        )


async def test_delete_requires_author(database):
    session, factory = database
    fake = FakeTelegram()
    redis = FakeRedis()
    queue = InlineQueue()
    chat = await _ready(session, fake)
    service, settings, _factory = _stack(session, factory, fake, redis, queue)
    result = await service.accept(
        telegram_user_id=USER,
        chat=chat,
        content_type="text",
        body="моё",
        file_id=None,
        original_filename=None,
        file_size=None,
        source="private",
        source_chat_id=USER,
        source_message_id=40,
    )
    delivery = DeliveryService(
        factory, settings=settings, telegram=fake, redis=redis, limiter=GroupSendLimiter(redis, 1, 20), bot_username="anon_test_bot"
    )
    await delivery.deliver(result.message.id)
    assert await service.delete_own(555, result.message.public_code) == "forbidden"
    assert fake.deleted == []
    assert await service.delete_own(USER, result.message.public_code) == "deleted"
    assert fake.deleted


async def test_broadcast_skips_blocked_and_is_idempotent(database):
    session, _factory = database
    settings = get_settings()
    fake = FakeTelegram()
    queue = InlineQueue()
    await UserService(session).register(USER)
    blocked, _, _ = await UserService(session).register(777)
    blocked.bot_blocked = True
    erased, _, _ = await UserService(session).register(778)
    erased.erased_at = now_utc()
    await session.commit()
    service = BroadcastService(session, settings=settings, telegram=fake, queue=queue, bot_username="anon_test_bot")
    with pytest.raises(PermissionDenied):
        await service.create(
            actor_id=222,
            audience="direct",
            timing="now",
            chat=None,
            body="секрет",
            content_type="text",
            file_id=None,
            original_filename=None,
            run_at=None,
            recurrence=None,
            weekday=None,
            hour=None,
            minute=None,
        )
    broadcast = await service.create(
        actor_id=111,
        audience="direct",
        timing="now",
        chat=None,
        body="объявление",
        content_type="text",
        file_id=None,
        original_filename=None,
        run_at=None,
        recurrence=None,
        weekday=None,
        hour=None,
        minute=None,
    )
    run = await service.execute(broadcast.id)
    assert run is not None
    assert run.sent == 1
    assert run.total == 1
    user_dms = [item for item in fake.dms if item[0] == USER]
    assert len(user_dms) == 1
    assert str(USER) not in user_dms[0][1]
    again = await service.execute(broadcast.id)
    assert again is None
    assert len([item for item in fake.dms if item[0] == USER]) == 1


async def test_stats_csv_has_no_identifiers(database):
    session, factory = database
    fake = FakeTelegram()
    redis = FakeRedis()
    queue = InlineQueue()
    chat = await _ready(session, fake)
    service, settings, _factory = _stack(session, factory, fake, redis, queue)
    await service.accept(
        telegram_user_id=USER,
        chat=chat,
        content_type="text",
        body="статистика",
        file_id=None,
        original_filename=None,
        file_size=None,
        source="private",
        source_chat_id=USER,
        source_message_id=50,
    )
    data = await AnalyticsService(session, "Europe/Moscow").overview(chat.id, None, None)
    assert data["messages"] == 1
    assert data["registered_users"] == 1
    csv_text = await AnalyticsService(session, "Europe/Moscow").daily_csv(chat.id, days=2)
    assert "telegram" not in csv_text.lower()
    assert str(USER) not in csv_text
    text = privacy_text()
    assert "абсолютн" in text.lower()
    assert "не" in text.lower()


async def test_reveal_hides_id_unless_allowed(database):
    session, _factory = database
    user, _, _ = await UserService(session).register(USER)
    service = ModerationService(session)
    hidden = service.reveal_text(user, "Аноним #104", allowed=False)
    shown = service.reveal_text(user, "Аноним #104", allowed=True)
    assert str(USER) not in hidden
    assert str(USER) in shown


def test_ephemeral_policy_and_commands():
    assert decide_ephemeral(enabled=False, ephemeral_message_id=5, args="привет", is_text=True) == "disabled"
    assert decide_ephemeral(enabled=True, ephemeral_message_id=None, args="привет", is_text=True) == "visible"
    assert decide_ephemeral(enabled=True, ephemeral_message_id=9, args="  ", is_text=True) == "empty"
    assert decide_ephemeral(enabled=True, ephemeral_message_id=9, args="привет", is_text=False) == "media"
    assert decide_ephemeral(enabled=True, ephemeral_message_id=9, args="привет", is_text=True) == "ok"
    assert all(command.command != "anon" for command in private_commands())
    assert group_commands(False) == []
    assert group_commands(True)[0].is_ephemeral is True
    names = [item.name for item in build_routers(get_settings())]
    assert "intercept" not in names
    assert "ephemeral" in names


def test_security_limiter_schedule_and_filenames():
    secret = "test-callback-secret-32bytes"
    token = sign("c:abc", secret)
    assert unsign(token, secret) == "c:abc"
    assert unsign(token[:-1] + ("a" if token[-1] != "a" else "b"), secret) is None
    assert len(pack("c:abc", secret).encode()) <= 64
    leaked = "123456789:" + ("A" * 32)
    assert redact(f"boom {leaked} token") == "boom [redacted-token] token"
    record = logging_record(leaked)
    assert leaked not in JsonFormatter().format(record)
    assert neutral_filename("Ivan_secret.pdf", kind="document") == "file.pdf"
    assert neutral_filename("name.exe", kind="document") == "file.bin"
    assert parse_recurrence("daily 15:00").hour == 15
    parsed = parse_recurrence("weekly mon 09:30")
    assert parsed.weekday == 0 and parsed.minute == 30
    moment = datetime(2026, 11, 23, 12, 5, tzinfo=timezone.utc)
    assert due_slot(recurrence="daily", weekday=None, hour=15, minute=0, tz_name="Europe/Moscow", now=moment) == "2026-11-23"
    future = parse_run_at("2026-12-01 10:00", "Europe/Moscow", datetime(2026, 11, 23, tzinfo=timezone.utc))
    assert future.tzinfo is not None
    gateway = Path("app/integrations/telegram/aiogram_gateway.py").read_text(encoding="utf-8")
    assert "forward_message" not in gateway
    assert "copy_message" not in gateway
    assert "forwardMessage" not in gateway


def logging_record(message: str):
    import logging

    return logging.LogRecord("test", logging.INFO, __file__, 1, message, (), None)


@pytest.mark.asyncio
async def test_group_limiter_respects_interval_and_minute():
    redis = FakeRedis()
    limiter = GroupSendLimiter(redis, interval_ms=1000, per_minute=2)
    assert await limiter.acquire(CHAT) is True
    assert await limiter.acquire(CHAT) is False
    redis.now += 1.1
    assert await limiter.acquire(CHAT) is True
    redis.now += 1.1
    assert await limiter.acquire(CHAT) is False


def test_repository_has_no_bot_token():
    skip = {".git", ".venv", "venv", "__pycache__", ".pytest_cache", "backups"}
    allowed_suffixes = {".py", ".md", ".yml", ".yaml", ".toml", ".ini", ".example", ".sh", ".txt"}
    for path in ROOT.rglob("*"):
        if not path.is_file() or any(part in skip for part in path.parts) or path.name == ".env":
            continue
        if path.suffix.lower() not in allowed_suffixes and path.name not in {"Dockerfile", ".env.example", ".gitignore"}:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        assert TOKEN_RE.search(text) is None, path


def test_api_health_and_stats(tmp_path):
    from fastapi.testclient import TestClient

    from app.api.app import create_app
    from app.core.config import Settings
    from app.db.session import create_db_engine
    from pydantic import SecretStr

    import asyncio

    url = f"sqlite+aiosqlite:///{tmp_path}/api.db"
    engine = create_db_engine(url)

    async def init() -> None:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    asyncio.run(init())
    settings = Settings(
        bot_token=SecretStr("000000000:TEST_TOKEN_NOT_REAL"),
        callback_secret=SecretStr("test-callback-secret-32bytes"),
        database_url=url,
        admin_api_token=SecretStr("admin-token"),
        admin_ids="111",
    )
    app = create_app(settings, engine, FakeRedis())
    client = TestClient(app)
    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["database"] is True
    assert client.get("/api/v1/stats").status_code == 401
    allowed = client.get("/api/v1/stats", headers={"Authorization": "Bearer admin-token"})
    assert allowed.status_code == 200
    assert "registered_users" in allowed.json()
    assert str(USER) not in allowed.text


def test_migration_upgrade_matches_models(tmp_path, monkeypatch):
    url = f"sqlite+aiosqlite:///{tmp_path}/mig.db"
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    try:
        from alembic import command
        from alembic.config import Config

        command.upgrade(Config(str(ROOT / "alembic.ini")), "head")
    finally:
        get_settings.cache_clear()

    import asyncio

    from app.db.session import create_db_engine

    engine = create_db_engine(url)

    async def reflected() -> dict[str, set[str]]:
        async with engine.connect() as connection:
            def read(sync_connection):
                inspector = sa_inspect(sync_connection)
                return {
                    table: {column["name"] for column in inspector.get_columns(table)}
                    for table in inspector.get_table_names()
                    if table != "alembic_version"
                }

            return await connection.run_sync(read)

    try:
        found = asyncio.run(reflected())
    finally:
        asyncio.run(engine.dispose())
    expected = {table.name: {column.name for column in table.columns} for table in Base.metadata.sorted_tables}
    assert found.keys() == expected.keys()
    for name, columns in expected.items():
        assert columns == found[name]
