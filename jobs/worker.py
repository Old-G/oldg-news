"""worker: the scheduler (spec: «Расписание», time of the digest timezone).

fetch at :00 every FETCH_EVERY_H hours; daily preparation at DIGEST_PREPARE_AT, the waves at DIGEST_WAVES,
the weekly post at WEEKLY_POST_AT. DIGEST_WAVES=[] pauses everything that publishes (and the preparation).
"""

import asyncio
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from config import get_settings
from jobs import daily, fetch, weekly

log = logging.getLogger("worker")


async def run_fetch() -> None:
    errors = await fetch.run()
    if errors:
        log.error("fetch finished with errors: %s", errors)


async def run_prepare() -> None:
    try:
        await daily.run("prepare")
    except Exception:
        log.exception("prepare failed")  # recorded in runs; every wave prepares what is missing


async def run_wave(wave: int) -> None:
    try:
        await daily.run("wave", wave)
    except Exception:
        log.exception("wave %s failed", wave)  # recorded in runs; the next wave catches up


async def run_weekly() -> None:
    try:
        await weekly.run()
    except Exception:
        log.exception("weekly failed")


async def main() -> None:
    fetch.setup_logging()
    s = get_settings()
    scheduler = AsyncIOScheduler(timezone=s.digest_tz)
    common = {"max_instances": 1, "coalesce": True, "misfire_grace_time": 1800}
    # a CronTrigger built by hand takes the host's zone (UTC in the container), not the scheduler's: pass it
    tz = s.digest_tz
    scheduler.add_job(run_fetch, CronTrigger(hour=f"*/{s.fetch_every_h}", minute=0, timezone=tz), id="fetch", **common)

    def at(hhmm: str, **kw) -> CronTrigger:
        hour, minute = hhmm.split(":")
        return CronTrigger(hour=int(hour), minute=int(minute), timezone=tz, **kw)

    if s.digest_waves:
        scheduler.add_job(run_prepare, at(s.digest_prepare_at), id="prepare", **common)
        if s.weekly_post_at:
            day, hhmm = s.weekly_post_at.split()
            scheduler.add_job(run_weekly, at(hhmm, day_of_week=day), id="weekly", **common)
    for wave, hhmm in enumerate(s.digest_waves, 1):
        scheduler.add_job(run_wave, at(hhmm), args=[wave], id=f"wave{wave}", **common)
    scheduler.start()
    for job in scheduler.get_jobs():
        log.info("scheduled %s → next %s", job.id, job.next_run_time)
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
