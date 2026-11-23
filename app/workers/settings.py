from arq import cron, func
from arq.connections import RedisSettings

from app.core.config import get_settings
from app.workers.jobs import deliver_message, recover_pending, run_broadcast, run_scheduled_broadcasts, shutdown, startup


class WorkerSettings:
    functions = [
        func(deliver_message, max_tries=8, timeout=180),
        func(run_broadcast, max_tries=8, timeout=600),
        func(run_scheduled_broadcasts, max_tries=1, timeout=120),
        func(recover_pending, max_tries=1, timeout=120),
    ]
    cron_jobs = [
        cron(recover_pending, run_at_startup=True, max_tries=1, timeout=120),
        cron(run_scheduled_broadcasts, run_at_startup=True, max_tries=1, timeout=120),
    ]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    max_jobs = 10
    job_timeout = 180
    keep_result = 0
