import asyncio

import pytest
from aiogram.exceptions import TelegramNetworkError, TelegramRetryAfter

from bot.send import with_retry


class Flaky:
    """Fails with the given errors first, then returns 'ok'."""

    def __init__(self, *errors):
        self.errors, self.calls = list(errors), 0

    async def __call__(self):
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        return "ok"


def net():
    return TelegramNetworkError(None, "Request timeout error")


def run(call, retries=3):
    pauses = []

    async def sleep(s):
        pauses.append(s)

    return asyncio.run(with_retry(call, retries, 30.0, sleep)), pauses


def test_network_errors_are_retried_with_growing_pauses():
    call = Flaky(net(), net())
    assert run(call) == ("ok", [30.0, 60.0]) and call.calls == 3


def test_flood_wait_pauses_as_telegram_asks():
    call = Flaky(TelegramRetryAfter(None, "Flood control", 7))
    assert run(call) == ("ok", [7])


def test_gives_up_after_the_retries():
    call = Flaky(net(), net(), net())
    with pytest.raises(TelegramNetworkError):
        run(call, retries=2)
    assert call.calls == 3


def test_other_errors_are_not_retried():
    call = Flaky(ValueError("bad html"))
    with pytest.raises(ValueError):
        run(call)
    assert call.calls == 1
