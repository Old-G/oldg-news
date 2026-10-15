"""worker: the scheduler (spec: «Расписание», time of the digest timezone).

fetch at :00 every FETCH_EVERY_H hours; the pick + wave 1, then the other waves at DIGEST_WAVES.
"""

import asyncio
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from config import get_settings
from jobs import daily, fetch

log = logging.getLogger("worker")


async def run_fetch() -> None:
    errors = await fetch.run()
    if errors:
        log.error("fetch finished with errors: %s", errors)


async def run_wave(wave: int) -> None:
    try:
        await daily.run(wave)
    except Exception:
        log.exception("wave %s failed", wave)  # recorded in runs; the next wave catches up


async def main() -> None:
    fetch.setup_logging()
    s = get_settings()
    scheduler = AsyncIOScheduler(timezone=s.digest_tz)
    common = {"max_instances": 1, "coalesce": True, "misfire_grace_time": 1800}
    scheduler.add_job(run_fetch, CronTrigger(hour=f"*/{s.fetch_every_h}", minute=0), id="fetch", **common)
    for wave, hhmm in enumerate(s.digest_waves, 1):
        hour, minute = hhmm.split(":")
        scheduler.add_job(run_wave, CronTrigger(hour=int(hour), minute=int(minute)), args=[wave], id=f"wave{wave}", **common)
    scheduler.start()
    for job in scheduler.get_jobs():
        log.info("scheduled %s → next %s", job.id, job.next_run_time)
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
