from __future__ import annotations

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
from redis.asyncio import Redis

from app.core.config import Settings, get_settings
from app.db.session import create_db_engine, create_session_factory
from app.runtime import ping_database, ping_redis
from app.services.analytics import AnalyticsService


def create_app(settings: Settings | None = None, engine=None, redis=None) -> FastAPI:
    settings = settings or get_settings()
    engine = engine or create_db_engine(settings.database_url)
    redis = redis or Redis.from_url(settings.redis_url, decode_responses=True)
    session_factory = create_session_factory(engine)
    app = FastAPI(title="Anonymous Group Chat", docs_url=None, redoc_url=None)

    @app.get("/health")
    async def health():
        database = await ping_database(engine)
        cache = await ping_redis(redis)
        ok = database and cache
        return JSONResponse(
            {"status": "ok" if ok else "degraded", "database": database, "redis": cache},
            status_code=200 if ok else 503,
        )

    @app.get("/api/v1/stats")
    async def stats(authorization: str | None = Header(default=None)):
        expected = settings.admin_api_token.get_secret_value()
        if not expected:
            raise HTTPException(status_code=404)
        if authorization != f"Bearer {expected}":
            raise HTTPException(status_code=401)
        session = session_factory()
        try:
            data = await AnalyticsService(session, settings.timezone).overview(None, None, None)
        finally:
            await session.close()
        return data

    return app
