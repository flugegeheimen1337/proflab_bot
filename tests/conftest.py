import os

os.environ.setdefault("BOT_TOKEN", "000000000:TEST_TOKEN_NOT_REAL")
os.environ.setdefault("CALLBACK_SECRET", "test-callback-secret-32bytes")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./pytest-placeholder.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("ADMIN_IDS", "111")
os.environ.setdefault("EPHEMERAL_MODE_ENABLED", "false")
os.environ.setdefault("INTERCEPT_MODE_ENABLED", "false")
os.environ.setdefault("REVEAL_TELEGRAM_IDS", "false")
os.environ.setdefault("TIMEZONE", "Europe/Moscow")

import pytest

from app.db.base import Base
from app.db.models import *  # noqa: F403
from app.db.session import create_db_engine, create_session_factory


@pytest.fixture
async def database(tmp_path):
    engine = create_db_engine(f"sqlite+aiosqlite:///{tmp_path}/test.db")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = create_session_factory(engine)
    session = factory()
    try:
        yield session, factory
    finally:
        await session.close()
        await engine.dispose()
