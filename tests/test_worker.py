import asyncio
from zoneinfo import ZoneInfo

from jobs import worker


class FakeScheduler:
    timezone = ZoneInfo("Europe/Minsk")

    def __init__(self):
        self.jobs = []

    def add_job(self, func, trigger, args, id, **kw):
        self.jobs.append((id, args))


def run_wave(monkeypatch, attempt, fails):
    calls = []

    async def fake_run(mode, wave, last_try):
        calls.append(last_try)
        if fails:
            raise RuntimeError("no network")

    monkeypatch.setattr(worker.daily, "run", fake_run)
    monkeypatch.setattr(worker, "scheduler", FakeScheduler())
    monkeypatch.setattr(worker, "get_settings", lambda: type("S", (), {"wave_retries": 2, "wave_retry_min": 15})())
    asyncio.run(worker.run_wave(1, attempt))
    return calls, worker.scheduler.jobs


def test_a_failed_wave_is_tried_again(monkeypatch):
    assert run_wave(monkeypatch, 1, fails=True) == ([False], [("wave1-retry", [1, 2])])


def test_the_last_try_sends_what_there_is_and_is_not_repeated(monkeypatch):
    assert run_wave(monkeypatch, 3, fails=True) == ([True], [])


def test_a_wave_that_went_out_is_not_repeated(monkeypatch):
    assert run_wave(monkeypatch, 1, fails=False) == ([False], [])
