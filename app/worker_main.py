from __future__ import annotations

import asyncio

from arq.worker import create_worker

from app.runtime import ping_database, ping_redis, serve_health
from app.workers.settings import WorkerSettings


async def main() -> None:
    worker = create_worker(WorkerSettings)
    engine_box: dict = {}

    async def healthy() -> bool:
        engine = engine_box.get("engine")
        redis = engine_box.get("redis")
        if engine is None or redis is None:
            return False
        return await ping_database(engine) and await ping_redis(redis)

    original_startup = WorkerSettings.on_startup

    async def startup(ctx) -> None:
        await original_startup(ctx)
        engine_box["engine"] = ctx["engine"]
        engine_box["redis"] = ctx["app_redis"]

    worker.on_startup = startup
    health = await serve_health(8082, healthy)
    try:
        await worker.async_run()
    finally:
        health.close()
        await health.wait_closed()


if __name__ == "__main__":
    asyncio.run(main())
