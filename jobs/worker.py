"""worker: the scheduler (spec: «Расписание», time of the digest timezone).

fetch at :00 every FETCH_EVERY_H hours; daily preparation at DIGEST_PREPARE_AT, the waves at DIGEST_WAVES,
the weekly post at WEEKLY_POST_AT, the taste profile at WEEKLY_TASTE_AT, ⭐ drafts every DRAFTS_POLL_S seconds. DIGEST_WAVES=[] pauses everything that publishes (and the preparation).
"""

import asyncio
import logging
from datetime import datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger

from config import get_settings
from jobs import daily, drafts, fetch, weekly, weekly_taste

log = logging.getLogger("worker")
scheduler: AsyncIOScheduler | None = None


async def run_fetch() -> None:
    errors = await fetch.run()
    if errors:
        log.error("fetch finished with errors: %s", errors)


async def run_prepare() -> None:
    try:
        await daily.run("prepare")
    except Exception:
        log.exception("prepare failed")  # recorded in runs; every wave prepares what is missing


async def run_wave(wave: int, attempt: int = 1) -> None:
    """A failed or unfinished wave (no network, no overview yet) runs again in WAVE_RETRY_MIN, up to WAVE_RETRIES
    times; the last try sends what there is. Waves are idempotent: a repeat never posts twice."""
    s = get_settings()
    try:
        await daily.run("wave", wave, last_try=attempt > s.wave_retries)
    except Exception:
        log.exception("wave %s, try %s failed", wave, attempt)  # recorded in runs
        if attempt > s.wave_retries:
            return  # the next wave catches up
        when = datetime.now(scheduler.timezone) + timedelta(minutes=s.wave_retry_min)
        scheduler.add_job(run_wave, DateTrigger(when), args=[wave, attempt + 1], id=f"wave{wave}-retry",
                          replace_existing=True, misfire_grace_time=1800)
        log.info("wave %s: try %s at %s", wave, attempt + 1, when)


async def run_drafts() -> None:
    try:
        await drafts.run()
    except Exception:
        log.exception("drafts failed")


async def run_weekly_taste() -> None:
    try:
        await weekly_taste.run()
    except Exception:
        log.exception("weekly_taste failed")


async def run_weekly() -> None:
    try:
        await weekly.run()
    except Exception:
        log.exception("weekly failed")


async def main() -> None:
    fetch.setup_logging()
    logging.getLogger("apscheduler.executors.default").setLevel(logging.WARNING)  # drafts poll every minute
    global scheduler
    s = get_settings()
    scheduler = AsyncIOScheduler(timezone=s.digest_tz)
    common = {"max_instances": 1, "coalesce": True, "misfire_grace_time": 1800}
    # a CronTrigger built by hand takes the host's zone (UTC in the container), not the scheduler's: pass it
    tz = s.digest_tz
    scheduler.add_job(run_fetch, CronTrigger(hour=f"*/{s.fetch_every_h}", minute=0, timezone=tz), id="fetch", **common)
    # ⭐ → drafts in the owner's DM; runs even when the waves are paused
    scheduler.add_job(run_drafts, IntervalTrigger(seconds=s.drafts_poll_s, timezone=tz), id="drafts", **common)

    def at(hhmm: str, **kw) -> CronTrigger:
        hour, minute = hhmm.split(":")
        return CronTrigger(hour=int(hour), minute=int(minute), timezone=tz, **kw)

    if s.weekly_taste_at != "off":  # publishes nothing: runs even when the waves are paused
        day, hhmm = s.weekly_taste_at.split()
        scheduler.add_job(run_weekly_taste, at(hhmm, day_of_week=day), id="weekly_taste", **common)
    if s.digest_waves:
        scheduler.add_job(run_prepare, at(s.digest_prepare_at), id="prepare", **common)
        if s.weekly_post_at != "off":  # an empty value in .env is ignored, so off is a word
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
